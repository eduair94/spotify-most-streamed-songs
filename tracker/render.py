"""LEADERBOARD.md, data/latest.json and the README excerpt, from history entries."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .history import (
    BELOW_1000,
    IMPLAUSIBLE_JUMP,
    JUMP_FLOOR,
    JUMP_SHARE,
    NO_EARLIER_COUNT,
    VERSION_CHANGED,
    Entry,
    Row,
    days_before,
    split_artists,
)

API_GUIDE_URL = "https://spotify.checkleaked.cc/spotify-play-count-api"
TRACK_URL = "https://open.spotify.com/track/{}"
README_START = "<!-- leaderboard:start -->"
README_END = "<!-- leaderboard:end -->"
NOTICE = "Unofficial. The numbers are the play counts Spotify shows on its public pages; not affiliated with Spotify."
NONE = "—"

# ASCII punctuation that could change how a name renders inside a table or a link
# ("$" opens math on GitHub, "|" ends the cell, "[" "]" break the link).
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|$~])")


def escape_markdown(text: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def song_link(row: Row) -> str:
    name = escape_markdown(row.name) or row.track_id
    return f"[{name}]({TRACK_URL.format(row.track_id)})"


def artist_text(row: Row) -> str:
    return escape_markdown(", ".join(split_artists(row.artists))) or NONE


def format_count(value: Optional[int]) -> str:
    return NONE if value is None else f"{value:,}"


def format_change(difference: Optional[int], percent: Optional[float]) -> str:
    if difference is None:
        return NONE
    text = "0" if difference == 0 else f"{difference:+,}"
    if percent is not None:
        text += " (0.00%)" if difference == 0 else f" ({percent:+.2f}%)"
    return text


def _name_key(entry: Entry) -> Tuple[str, str]:
    return entry.row.name.casefold(), entry.row.track_id


def rank_by_streams(entries: Sequence[Entry]) -> List[Entry]:
    """Most total streams first; tracks without a play count last; ties by name."""

    return sorted(entries, key=lambda e: (e.row.playcount is None, -(e.row.playcount or 0), *_name_key(e)))


def rank_by_daily(entries: Sequence[Entry]) -> List[Entry]:
    """Tracks with a daily figure, most daily streams first; ties by total streams, then name."""

    return sorted(
        (e for e in entries if e.daily is not None),
        key=lambda e: (-(e.daily or 0), -(e.row.playcount or 0), *_name_key(e)),
    )


def _plural(count: int, noun: str) -> str:
    """``1 song`` / ``2 songs``."""

    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def _songs(count: int, verb: str, verbs: str, kind: str = "") -> str:
    """``1 song has`` / ``2 songs have``: the count, the noun and the verb in agreement."""

    return f"{_plural(count, f'{kind} song' if kind else 'song')} {verb if count == 1 else verbs}"


def _song_list(entries: Sequence[Entry], *, with_change: bool = False, limit: int = 10) -> str:
    """Links to the songs, most total streams first (the first ``limit``), with the count's change if asked."""

    shown = rank_by_streams(entries)[:limit]
    items = []
    for entry in shown:
        text = song_link(entry.row)
        if with_change and entry.count_change is not None:
            text += f" ({entry.count_change:+,})"
        items.append(text)
    more = len(entries) - len(shown)
    return ", ".join(items) + (f" and {more:,} more" if more else "")


def _daily_notes(entries: Sequence[Entry], by_daily: Sequence[Entry], yesterday: str) -> List[str]:
    """What the reader needs to know about the daily figures, and about the songs without one."""

    notes = []
    zero = sum(1 for e in by_daily if e.daily == 0)
    lower = sum(1 for e in by_daily if (e.daily or 0) < 0)
    if zero:
        notes.append(
            f"{_songs(zero, 'shows', 'show')} 0 daily streams, which usually means Spotify had not refreshed "
            "the play count since the day before; the next daily figure then covers two days."
        )
    if lower:
        notes.append(
            f"{_songs(lower, 'has', 'have')} a lower play count than the day before: Spotify occasionally lowers a count."
        )
    without: Dict[str, List[Entry]] = {}
    for entry in entries:
        reason = entry.no_daily_reason
        if reason:
            without.setdefault(reason, []).append(entry)
    switched = without.get(VERSION_CHANGED, [])
    if switched:
        notes.append(
            f"{_songs(len(switched), 'was', 'were')} answered with another release of the song than on {yesterday} "
            "(Spotify relinked the track id), so the two counts belong to different releases and give no daily "
            f"figure: {_song_list(switched)}."
        )
    jumps = without.get(IMPLAUSIBLE_JUMP, [])
    if jumps:
        notes.append(
            f"The play count of {_plural(len(jumps), 'song')} moved by more than {JUMP_SHARE:.0%} (and more than "
            f"{JUMP_FLOOR:,} plays) since {yesterday}, which streams do not explain: Spotify most likely replaced or "
            f"corrected the count. No daily figure is given for {'it' if len(jumps) == 1 else 'them'}: "
            f"{_song_list(jumps, with_change=True)}."
        )
    no_earlier = len(without.get(NO_EARLIER_COUNT, []))
    if no_earlier:
        notes.append(
            f"{_songs(no_earlier, 'has', 'have')} no play count from {yesterday} to compare with: added since, "
            "or not read that day."
        )
    below = len(without.get(BELOW_1000, []))
    if below:
        notes.append(
            f"{_songs(below, 'has', 'have')} a play count of 0 on one of the two days: Spotify shows 0 below "
            "1,000 plays, so the gain is unknown."
        )
    return notes


def render_leaderboard(
    entries: Sequence[Entry],
    *,
    day: str,
    tracked: int,
    top: int = 100,
    daily_top: int = 50,
    not_updated: int = 0,
    unknown: Sequence[str] = (),
    first_day: Optional[str] = None,
) -> str:
    """The LEADERBOARD.md text.

    ``not_updated`` counts the tracked songs without a play count on ``day`` (other than the
    ``unknown`` ids, which Spotify does not know), and ``first_day`` is the first day in the
    history (None: ``day`` itself).
    """

    by_streams = rank_by_streams(entries)[:top]
    by_daily = rank_by_daily(entries)
    yesterday = days_before(day, 1)
    lines = [
        "# Most-streamed songs on Spotify",
        "",
        f"Total and daily Spotify streams on **{day}** (UTC) of the {tracked:,} songs in "
        "[`tracks.txt`](tracks.txt). Updated once a day. Only the tracked songs are ranked: "
        "this is not a ranking of every song on Spotify.",
        "",
        "Total streams is the play count Spotify shows for the song, counted since its release. "
        "Daily streams is that count minus the same song's count one day earlier, and 7-day streams "
        f"the same over seven days; {NONE} means there is no figure: no earlier count to compare with yet, "
        "a count below 1,000 plays (which Spotify shows as 0), or a change that is not streams, such as "
        "the counts of two different releases of the song (the notes under the daily table say which). "
        "Spotify refreshes play counts roughly once a day, not at the moment this tracker reads them: "
        "if a count was not refreshed in between, that day shows 0 and the next day covers two days.",
        "",
        "<!-- Generated by `python -m tracker` from data/history.csv and data/relinks.csv. "
        "Manual edits are overwritten by the next run. -->",
        "",
        f"## Top {len(by_streams)} by total streams",
        "",
        "| # | Song | Artist | Total streams | Daily streams | 7-day streams |",
        "|--:|:--|:--|--:|--:|--:|",
    ]
    for position, entry in enumerate(by_streams, start=1):
        row = entry.row
        lines.append(
            f"| {position} | {song_link(row)} | {artist_text(row)} | {format_count(row.playcount)} "
            f"| {format_count(entry.daily)} | {format_count(entry.weekly)} |"
        )
    lines += ["", f"## Top {min(daily_top, len(by_daily)) if by_daily else daily_top} by daily streams", ""]
    compared = any(entry.earlier is not None for entry in entries)
    notes: List[str] = []
    if by_daily:
        lines += [
            f"Streams gained from {yesterday} to {day}, among all {tracked:,} tracked songs. The change "
            "compares them with the streams gained the day before.",
            "",
            "| # | Song | Artist | Daily streams | Change from the day before | Total streams |",
            "|--:|:--|:--|--:|--:|--:|",
        ]
        for position, entry in enumerate(by_daily[:daily_top], start=1):
            row = entry.row
            lines.append(
                f"| {position} | {song_link(row)} | {artist_text(row)} | {format_count(entry.daily)} "
                f"| {format_change(*entry.daily_change)} | {format_count(row.playcount)} |"
            )
        notes = _daily_notes(entries, by_daily, yesterday)
    elif not compared and (first_day is None or first_day >= day):
        lines.append(
            f"No daily streams for {day} yet: they need the same songs' play counts from {yesterday}, and "
            "data/history.csv has none. Daily streams start with the second day of tracking."
        )
    elif not compared:
        lines.append(
            f"No daily streams for {day}: data/history.csv has no play counts of these songs from {yesterday} "
            "(no run recorded them that day), and daily streams only compare consecutive days. They come back "
            "once two consecutive days are recorded."
        )
    else:
        lines.append(f"No song has a daily figure for {day}.")
        notes = _daily_notes(entries, by_daily, yesterday)
    for note in notes:
        lines += ["", note]
    lines += [
        "",
        "## Data",
        "",
        "- [`data/history.csv`](data/history.csv): one row per song per day since tracking started.",
        "- [`data/latest.json`](data/latest.json): the latest day for every tracked song, with daily and 7-day streams.",
    ]
    if not_updated:
        lines += [
            "",
            f"{_songs(not_updated, 'has', 'have', 'tracked')} no play count for {day}: the API could not read "
            "them in this run. Earlier counts, where there are any, are in data/history.csv.",
        ]
    if unknown:
        count = len(unknown)
        listed = ", ".join(f"`{track_id}`" for track_id in unknown[:20]) + (" ..." if count > 20 else "")
        lines += [
            "",
            f"Spotify does not know {_plural(count, 'track id')} in tracks.txt, so {'it gets' if count == 1 else 'they get'} "
            f"no play count: {listed}. Remove {'it' if count == 1 else 'them'} from the list.",
        ]
    lines += [
        "",
        f"Collected with the [Spotify play count API]({API_GUIDE_URL}). {NOTICE}",
        "",
    ]
    return "\n".join(lines)


def _percent(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def build_latest(
    entries: Sequence[Entry],
    *,
    day: str,
    tracked: int,
    not_updated: Sequence[Tuple[str, Optional[Row]]] = (),
    unknown: Sequence[str] = (),
) -> Dict[str, Any]:
    """The data/latest.json document (deterministic: no timestamps beyond the date)."""

    songs = []
    for position, entry in enumerate(rank_by_streams(entries), start=1):
        row = entry.row
        difference, percent = entry.daily_change
        songs.append(
            {
                "position": position,
                "id": row.track_id,
                "answered_id": entry.answered,
                "name": row.name,
                "artists": split_artists(row.artists),
                "playcount": row.playcount,
                "daily_streams": entry.daily,
                "no_daily_reason": entry.no_daily_reason,
                "daily_streams_change": difference,
                "daily_streams_change_pct": _percent(percent),
                "streams_7d": entry.weekly,
                "url": TRACK_URL.format(row.track_id),
            }
        )
    return {
        "date": day,
        "tracked_songs": tracked,
        "songs_with_data": len(songs),
        "source": API_GUIDE_URL,
        "notice": NOTICE,
        "songs": songs,
        "not_updated": [
            {"id": track_id, "name": last.name if last else None, "last_date": last.date if last else None}
            for track_id, last in not_updated
        ],
        "unknown_ids": list(unknown),
    }


def dump_latest(document: Mapping[str, Any]) -> str:
    """JSON text with one song per line, so a day's diff reads song by song."""

    keys = list(document)
    lines = ["{"]
    for index, key in enumerate(keys):
        value = document[key]
        comma = "," if index < len(keys) - 1 else ""
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            lines.append(f"  {json.dumps(key)}: [")
            for item_index, item in enumerate(value):
                item_comma = "," if item_index < len(value) - 1 else ""
                lines.append("    " + json.dumps(item, ensure_ascii=False) + item_comma)
            lines.append("  ]" + comma)
        else:
            lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_excerpt(entries: Sequence[Entry], *, day: str, top: int = 10) -> str:
    """The short table that goes between the README markers."""

    shown = rank_by_streams(entries)[:top]
    lines = [
        f"Top {len(shown)} of the tracked songs by total streams on **{day}** (UTC). "
        "Top 100, and the top 50 by daily streams: [LEADERBOARD.md](LEADERBOARD.md).",
        "",
        "| # | Song | Artist | Total streams | Daily streams |",
        "|--:|:--|:--|--:|--:|",
    ]
    for position, entry in enumerate(shown, start=1):
        row = entry.row
        lines.append(
            f"| {position} | {song_link(row)} | {artist_text(row)} | {format_count(row.playcount)} "
            f"| {format_count(entry.daily)} |"
        )
    return "\n".join(lines)


def replace_section(text: str, content: str) -> str:
    """``text`` with ``content`` between the README markers (unchanged when they are missing)."""

    start = text.find(README_START)
    end = text.find(README_END, start + len(README_START)) if start != -1 else -1
    if start == -1 or end == -1:
        return text
    return text[: start + len(README_START)] + "\n" + content.strip("\n") + "\n" + text[end:]
