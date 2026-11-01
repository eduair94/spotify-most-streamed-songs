"""Command line: ``python -m tracker [run|render|check]``.

run     fetch today's play counts for tracks.txt, append them to data/history.csv (and the
        relinked ids to data/relinks.csv), then write LEADERBOARD.md, data/latest.json and
        the README excerpt
render  rewrite those files from data/history.csv and data/relinks.csv without calling the API
check   validate tracks.txt and show how many requests a daily run takes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Set

from . import __version__
from .fetch import (
    BATCH_SIZE,
    FetchBatch,
    FetchReport,
    RequestBudget,
    TrackPlays,
    fetch_plays,
    make_client,
    requests_needed,
    resolve_api,
    sdk_fetcher,
)
from .history import (
    IMPLAUSIBLE_JUMP,
    JUMP_SHARE,
    VERSION_CHANGED,
    Entry,
    Relink,
    Row,
    days_before,
    entries_for_day,
    first_day,
    last_rows,
    latest_day,
    read_history,
    read_relinks,
    rows_for_day,
    upsert,
    upsert_relinks,
    write_history,
    write_relinks,
    write_text_if_changed,
)
from .render import build_latest, dump_latest, render_excerpt, render_leaderboard, replace_section
from .tracks import TrackList, read_tracks

#: Requests per run, retries included, unless TRACKER_MAX_REQUESTS or --max-requests says
#: otherwise. 30 a day is at most 930 in a 31-day month: under the free RapidAPI plan's 1,000.
DEFAULT_MAX_REQUESTS = 30

#: Minutes after which a run starts no new request (--time-limit). daily.yml gives the job
#: 30 minutes, which leaves room for the last request, the install and the commit.
DEFAULT_TIME_LIMIT = 20.0

#: Size of data/history.csv from which every run warns. GitHub warns about files over 50 MB
#: and refuses to take files over 100 MB, so the history must be split by year before then.
HISTORY_WARN_BYTES = 50_000_000


def _print(line: str) -> None:
    """print(), with the characters the console cannot show replaced instead of ending the run."""

    try:
        print(line)
    except UnicodeEncodeError:  # a song title on a console that cannot show it (Windows, output redirected)
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(encoding, errors="replace").decode(encoding, errors="replace"))


class Console:
    """Log lines; inside GitHub Actions, warnings and errors also become annotations."""

    def __init__(self, write: Callable[[str], None] = _print, github: bool = False) -> None:
        self._write = write
        self._github = github

    def info(self, message: str) -> None:
        self._write(message)

    def notice(self, message: str) -> None:
        self._annotate("notice", message, message)

    def warning(self, message: str) -> None:
        self._annotate("warning", message, f"warning: {message}")

    def error(self, message: str) -> None:
        self._annotate("error", message, f"error: {message}")

    def _annotate(self, kind: str, message: str, plain: str) -> None:
        if not self._github:
            self._write(plain)
            return
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        self._write(f"::{kind}::{escaped}")


@dataclass
class Paths:
    tracks: Path
    history: Path
    relinks: Path
    latest: Path
    leaderboard: Path
    readme: Path

    @classmethod
    def at(cls, root: Path, tracks: Optional[Path] = None) -> Paths:
        return cls(
            tracks=tracks or root / "tracks.txt",
            history=root / "data" / "history.csv",
            relinks=root / "data" / "relinks.csv",
            latest=root / "data" / "latest.json",
            leaderboard=root / "LEADERBOARD.md",
            readme=root / "README.md",
        )


def load_tracks(paths: Paths, console: Console) -> Optional[TrackList]:
    if not paths.tracks.exists():
        console.error(f"{paths.tracks} does not exist")
        return None
    tracks = read_tracks(paths.tracks)
    for line, message in tracks.errors:
        console.warning(f"{paths.tracks.name} line {line}: {message} (skipped)")
    for line, message in tracks.warnings:
        console.warning(f"{paths.tracks.name} line {line}: {message} (skipped)")
    return tracks


def _unknown_in_latest(path: Path, day: str) -> Set[str]:
    """The ids data/latest.json lists as unknown to Spotify, when it is the file for ``day`` (else none)."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(document, dict) or document.get("date") != day or not isinstance(document.get("unknown_ids"), list):
        return set()
    return {track_id for track_id in document["unknown_ids"] if isinstance(track_id, str)}


def render_outputs(
    paths: Paths,
    rows: Sequence[Row],
    track_ids: Sequence[str],
    day: str,
    *,
    relinks: Iterable[Relink] = (),
    unknown: Optional[Set[str]] = None,
    top: int = 100,
    daily_top: int = 50,
) -> List[str]:
    """Write LEADERBOARD.md and the README excerpt for ``day``. Returns what changed.

    data/latest.json is written only when ``day`` is the latest day in the history, so that
    rendering an earlier day never makes it go back in time. ``unknown`` are the ids Spotify
    does not know; None keeps the ones data/latest.json lists for ``day``.
    """

    entries = entries_for_day(rows, day, track_ids, relinks)
    have = {entry.row.track_id for entry in entries}
    if unknown is None:
        unknown = _unknown_in_latest(paths.latest, day)
    unknown_ids = [track_id for track_id in track_ids if track_id in unknown and track_id not in have]
    skip = have | set(unknown_ids)
    last = last_rows(rows)
    not_updated = [(track_id, last.get(track_id)) for track_id in track_ids if track_id not in skip]
    changed = []
    leaderboard = render_leaderboard(
        entries,
        day=day,
        tracked=len(track_ids),
        top=top,
        daily_top=daily_top,
        not_updated=len(not_updated),
        unknown=unknown_ids,
        first_day=first_day(rows, track_ids),
    )
    if write_text_if_changed(paths.leaderboard, leaderboard):
        changed.append(paths.leaderboard.name)
    if day == latest_day(rows, track_ids):
        latest = build_latest(entries, day=day, tracked=len(track_ids), not_updated=not_updated, unknown=unknown_ids)
        if write_text_if_changed(paths.latest, dump_latest(latest)):
            changed.append("data/" + paths.latest.name)
    if paths.readme.exists():
        text = paths.readme.read_text(encoding="utf-8")
        updated = replace_section(text, render_excerpt(entries, day=day))
        if updated != text and write_text_if_changed(paths.readme, updated):
            changed.append(paths.readme.name)
    return changed


def _warn_about_counts(console: Console, plays: Sequence[TrackPlays], entries: Sequence[Entry], day: str) -> None:
    """Warn about counts of another release than the listed id, and about counts that give no daily figure."""

    relinked = [item for item in plays if item.relinked]
    if relinked:
        console.warning(
            f"Spotify answered {len(relinked)} listed track id(s) with another release of the song (track relinking): "
            "the counts recorded under those ids are the other releases' counts: "
            + ", ".join(f"{item.id} -> {item.answered_id} ({item.name})" for item in relinked)
        )
    yesterday = days_before(day, 1)
    switched = [entry for entry in entries if entry.no_daily_reason == VERSION_CHANGED]
    if switched:
        console.warning(
            f"{len(switched)} song(s) were answered by another track than on {yesterday}, so they get no daily figure "
            f"for {day}: " + ", ".join(f"{e.row.track_id} ({e.earlier_answered_id} -> {e.answered})" for e in switched)
        )
    jumps = [entry for entry in entries if entry.no_daily_reason == IMPLAUSIBLE_JUMP]
    if jumps:
        console.warning(
            f"The play count of {len(jumps)} song(s) moved by more than {JUMP_SHARE:.0%} since {yesterday}, which "
            f"streams do not explain, so it gives no daily figure for {day}: "
            + ", ".join(f"{e.row.track_id} ({e.count_change or 0:+,})" for e in jumps)
        )


def record_and_render(
    paths: Paths,
    track_ids: Sequence[str],
    day: str,
    report: FetchReport,
    console: Console,
    *,
    history: Sequence[Row],
    relinks: Iterable[Relink] = (),
    top: int = 100,
    daily_top: int = 50,
) -> int:
    """Add a fetch report's play counts to ``history`` for ``day``, save it and re-render. Returns the exit code.

    The generated files show the latest day in the history: ``day`` itself, unless an
    earlier day was recorded with ``--date``.
    """

    if report.missing:
        console.warning(
            f"Spotify does not know {len(report.missing)} track id(s); remove them from tracks.txt: " + ", ".join(report.missing)
        )
    if report.stopped:
        console.warning(f"Stopped early: {report.stopped}.")
    if not report.plays:
        console.error(f"No play counts were fetched with {report.requests} request(s), so nothing was written.")
        return 1
    plays = [report.plays[track_id] for track_id in track_ids if track_id in report.plays]
    rows = upsert(history, rows_for_day(day, plays))
    relinks = upsert_relinks(relinks, day, plays)
    changed = ["data/history.csv"] if write_history(paths.history, rows) else []
    if write_relinks(paths.relinks, relinks):
        changed.append("data/" + paths.relinks.name)
    shown = latest_day(rows, track_ids) or day
    unknown: Optional[Set[str]] = None
    if shown == day:
        # An id that an earlier run of the same day found unknown stays so, unless this run read it.
        unknown = set(report.missing) | (_unknown_in_latest(paths.latest, day) - set(report.plays))
    changed += render_outputs(paths, rows, track_ids, shown, relinks=relinks, unknown=unknown, top=top, daily_top=daily_top)
    missing = set(report.missing)
    failed = [track_id for track_id in report.failed if track_id not in missing]
    console.info(
        f"Recorded {len(plays)} of {len(track_ids)} tracks for {day} with {report.requests} request(s). "
        + (f"The files show {shown}, the latest day in data/history.csv. " if shown != day else "")
        + (f"Updated: {', '.join(changed)}." if changed else "No file changed.")
    )
    _warn_about_counts(console, plays, entries_for_day(rows, day, [item.id for item in plays], relinks), day)
    if failed:
        listed = ", ".join(failed[:20]) + (" ..." if len(failed) > 20 else "")
        console.warning(f"{len(failed)} track(s) got no play count this run; the next run will try again: {listed}")
    size = paths.history.stat().st_size
    if size > HISTORY_WARN_BYTES:
        console.warning(
            f"data/history.csv is {size / 1_000_000:.0f} MB. GitHub refuses files over 100 MB, so split the history "
            "into one file per year before it gets there (see README.md)."
        )
    return 0


def run_tracker(
    paths: Paths,
    track_ids: Sequence[str],
    day: str,
    fetch_batch: FetchBatch,
    console: Console,
    *,
    history: Sequence[Row],
    relinks: Iterable[Relink] = (),
    budget: Optional[RequestBudget] = None,
    retry_passes: int = 2,
    time_limit: Optional[float] = None,
    pause: float = 1.0,
    top: int = 100,
    daily_top: int = 50,
) -> int:
    """Fetch, record and render with any ``fetch_batch`` (the SDK one in ``run``).

    ``history`` and ``relinks`` are data/history.csv and data/relinks.csv as read before
    fetching, so that a broken file stops the run before it spends any request. ``budget``
    caps the requests and ``time_limit`` (seconds) the time spent fetching.
    """

    report = fetch_plays(
        track_ids,
        fetch_batch,
        retry_passes=retry_passes,
        budget=budget,
        time_limit=time_limit,
        pause=pause,
        log=console.info,
    )
    return record_and_render(
        paths, track_ids, day, report, console, history=history, relinks=relinks, top=top, daily_top=daily_top
    )


def _max_requests(value: Optional[int], env: Mapping[str, str]) -> int:
    if value is not None:
        result = value
    else:
        text = (env.get("TRACKER_MAX_REQUESTS") or "").strip()
        if not text:
            return DEFAULT_MAX_REQUESTS
        try:
            result = int(text)
        except ValueError:
            raise ValueError(f"TRACKER_MAX_REQUESTS must be a whole number, not {text!r}") from None
    if result < 1:
        raise ValueError("the request limit must be at least 1")
    return result


def _day(value: Optional[str]) -> str:
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"--date must be YYYY-MM-DD, not {value!r}") from None


def _positive(option: str, value: float) -> float:
    if not value > 0:  # also refuses NaN
        raise ValueError(f"{option} must be greater than 0")
    return value


def _rows(option: str, value: int) -> int:
    if value < 1:
        raise ValueError(f"{option} must be at least 1")
    return value


def cmd_run(args: argparse.Namespace, env: Mapping[str, str], session: Any, console: Console) -> int:
    paths = Paths.at(Path(args.root), Path(args.tracks) if args.tracks else None)
    tracks = load_tracks(paths, console)
    if tracks is None:
        return 1
    if not tracks.ids:
        console.error(f"{paths.tracks.name} lists no tracks.")
        return 1
    key = (env.get("SPOTIFY_API_KEY") or "").strip()
    if not key:
        console.notice(
            "SPOTIFY_API_KEY is not set, so there is nothing to fetch. Add your API key as a repository "
            "secret named SPOTIFY_API_KEY (Settings > Secrets and variables > Actions) to start tracking."
        )
        return 0
    base = env.get("SPOTIFY_API_BASE")
    try:
        provider, _, _ = resolve_api(base)
        max_requests = _max_requests(args.max_requests, env)
        day = _day(args.date)
        timeout = _positive("--timeout", args.timeout)
        time_limit = _positive("--time-limit", args.time_limit) * 60
        top = _rows("--top", args.top)
        daily_top = _rows("--daily-top", args.daily_top)
        # Before the client exists: a broken data file must not cost a single request.
        history = read_history(paths.history)
        relinks = read_relinks(paths.relinks)
    except ValueError as error:
        console.error(str(error))
        return 1
    count = len(tracks.ids)
    needed = requests_needed(count)
    console.info(
        f"{count} tracks: {needed} request(s) of up to {BATCH_SIZE} through "
        f"{'RapidAPI' if provider == 'rapidapi' else 'the direct API'}, at most {max_requests} this run "
        f"(retries included). Recording {day} (UTC)."
    )
    if needed > max_requests:
        console.warning(
            f"{count} tracks need {needed} requests but this run may send {max_requests}: the last "
            f"{count - max_requests * BATCH_SIZE} tracks in the list are skipped. Raise TRACKER_MAX_REQUESTS "
            "if your plan allows more."
        )
    budget = RequestBudget(max_requests)
    client = make_client(key, base, timeout=timeout, retries=args.retries, budget=budget, session=session)
    try:
        return run_tracker(
            paths,
            tracks.ids,
            day,
            sdk_fetcher(client),
            console,
            history=history,
            relinks=relinks,
            budget=budget,
            retry_passes=args.retry_passes,
            time_limit=time_limit,
            pause=args.pause,
            top=top,
            daily_top=daily_top,
        )
    finally:
        client.close()


def cmd_render(args: argparse.Namespace, console: Console) -> int:
    paths = Paths.at(Path(args.root), Path(args.tracks) if args.tracks else None)
    tracks = load_tracks(paths, console)
    if tracks is None:
        return 1
    try:
        top = _rows("--top", args.top)
        daily_top = _rows("--daily-top", args.daily_top)
        rows = read_history(paths.history)
        relinks = read_relinks(paths.relinks)
        latest = latest_day(rows, tracks.ids)
        day = _day(args.date) if args.date else latest
    except ValueError as error:
        console.error(str(error))
        return 1
    if latest is None or day is None:
        console.info("data/history.csv has no rows for these tracks yet; nothing to render.")
        return 0
    if not entries_for_day(rows, day, tracks.ids):
        console.error(f"data/history.csv has no rows for {day}.")
        return 1
    changed = render_outputs(paths, rows, tracks.ids, day, relinks=relinks, top=top, daily_top=daily_top)
    console.info(
        f"Rendered {day}. "
        + (f"Updated: {', '.join(changed)}." if changed else "No file changed.")
        + (f" data/latest.json stays on {latest}, the latest day." if day != latest else "")
    )
    return 0


def cmd_check(args: argparse.Namespace, console: Console) -> int:
    paths = Paths.at(Path(args.root), Path(args.tracks) if args.tracks else None)
    tracks = load_tracks(paths, console)
    if tracks is None:
        return 1
    count = len(tracks.ids)
    needed = requests_needed(count)
    console.info(
        f"{paths.tracks.name}: {count} tracks, {needed} request(s) per daily run "
        f"(up to {BATCH_SIZE} tracks each), about {needed * 31} a month before retries."
    )
    if needed > DEFAULT_MAX_REQUESTS:
        console.warning(
            f"That is more than the default limit of {DEFAULT_MAX_REQUESTS} requests per run; "
            "set TRACKER_MAX_REQUESTS if your plan allows it."
        )
    if tracks.errors:
        console.error(f"{len(tracks.errors)} line(s) of {paths.tracks.name} are not Spotify tracks.")
        return 1
    if not count:
        console.error(f"{paths.tracks.name} lists no tracks.")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="repository folder (default: current folder)")
    common.add_argument("--tracks", help="track list file (default: ROOT/tracks.txt)")
    common.add_argument("--top", type=int, default=100, help="rows in the total streams table (default: 100)")
    common.add_argument("--daily-top", type=int, default=50, help="rows in the daily streams table (default: 50)")

    parser = argparse.ArgumentParser(
        prog="python -m tracker",
        description="Daily Spotify play count (streams) tracker.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="{run,render,check}")

    run = commands.add_parser("run", parents=[common], help="fetch today's play counts and update the files")
    run.add_argument("--date", help="UTC day to record, YYYY-MM-DD (default: today)")
    run.add_argument(
        "--max-requests",
        type=int,
        help=f"requests this run may send, retries included (default: TRACKER_MAX_REQUESTS or {DEFAULT_MAX_REQUESTS})",
    )
    run.add_argument(
        "--retry-passes",
        type=int,
        default=2,
        help="extra passes for tracks whose play count was not ready or whose request failed (default: 2)",
    )
    run.add_argument(
        "--retries",
        type=int,
        default=0,
        help="SDK retries of a request that failed with 429/5xx or a network error, each counted towards "
        "--max-requests (default: 0; the next pass asks for those tracks again)",
    )
    run.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for one response (default: 90)")
    run.add_argument(
        "--time-limit",
        type=float,
        default=DEFAULT_TIME_LIMIT,
        help=f"minutes after which no new request is started (default: {DEFAULT_TIME_LIMIT:g})",
    )
    run.add_argument("--pause", type=float, default=1.0, help="seconds between requests (default: 1)")

    render = commands.add_parser("render", parents=[common], help="rewrite the generated files from the data files")
    render.add_argument("--date", help="day to render, YYYY-MM-DD (default: the latest in the history)")

    commands.add_parser("check", parents=[common], help="validate tracks.txt")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    session: Any = None,
    write: Optional[Callable[[str], None]] = None,
) -> int:
    """Entry point. ``env``, ``session`` (a requests-like session) and ``write`` are for tests."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0].startswith("-") and arguments[0] not in ("-h", "--help", "--version"):
        arguments.insert(0, "run")
    args = build_parser().parse_args(arguments)
    env = os.environ if env is None else env
    console = Console(write or _print, github=env.get("GITHUB_ACTIONS") == "true")
    if args.command == "render":
        return cmd_render(args, console)
    if args.command == "check":
        return cmd_check(args, console)
    return cmd_run(args, env, session, console)
