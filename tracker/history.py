"""data/history.csv: one row per track per UTC day, and the streams gained between days.

The file is kept sorted by date, then track id, so each day's rows are appended at the end.
Recording a day that is already there replaces those tracks' rows for that day instead of
adding new ones: re-running the tracker on the same day never duplicates rows.

data/relinks.csv lists the days on which Spotify answered a listed track id with another track
(track relinking, see fetch.py); that day's history row then holds the other track's count. Two
days' counts give a figure only when the same track answered on both days, and only when the
change is one that streams can explain (see :func:`implausible`).
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, TypeVar, Union

FIELDS = ("date", "track_id", "name", "artists", "playcount")
RELINK_FIELDS = ("date", "track_id", "answered_id")

#: Between the artist names in the ``artists`` column ("," would split "Tyler, The Creator").
ARTIST_SEPARATOR = "; "

# Why two days' counts give no figure (``Entry.no_daily_reason``; ``no_daily_reason`` in latest.json).
#: There is no count from the earlier day to compare with.
NO_EARLIER_COUNT = "no_earlier_count"
#: One of the counts is 0: Spotify shows 0 until a track passes 1,000 plays.
BELOW_1000 = "below_1000"
#: Another track answered for the listed id than on the earlier day: the counts are of two releases.
VERSION_CHANGED = "version_changed"
#: The count moved by more than streams explain (see :func:`implausible`).
IMPLAUSIBLE_JUMP = "implausible_jump"

#: A count that moves, per day, by more than this share of the song's play count and by more
#: than JUMP_FLOOR plays gives no figure (see :func:`implausible`).
JUMP_SHARE = 0.05
JUMP_FLOOR = 1_000_000

T = TypeVar("T")


@dataclass(frozen=True)
class Row:
    date: str  # YYYY-MM-DD (UTC)
    track_id: str
    name: str
    artists: str  # credited artist names, joined with ARTIST_SEPARATOR
    playcount: Optional[int]


@dataclass(frozen=True)
class Relink:
    """A day on which Spotify answered a listed track id with another track (track relinking)."""

    date: str  # YYYY-MM-DD (UTC)
    #: The id as listed in tracks.txt: the ``track_id`` of that day's history row.
    track_id: str
    #: The track that answered, another release of the song: that day's history row holds its count.
    answered_id: str


def split_artists(text: str) -> List[str]:
    """The artist names of a ``Row.artists`` value."""

    return [name for name in (part.strip() for part in text.split(ARTIST_SEPARATOR)) if name]


def _number(text: Optional[str], *, line: int, column: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"history.csv line {line}: {column} is not a whole number: {text!r}") from None
    if value < 0:
        raise ValueError(f"history.csv line {line}: {column} is negative: {text!r}")
    return value


def _date(record: Mapping[str, Optional[str]], *, file: str, line: int) -> str:
    text = (record.get("date") or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{file} line {line}: date is not YYYY-MM-DD: {text!r}") from None


def _read_csv(path: Union[str, Path], fields: Sequence[str], parse: Callable[[Dict[str, Any], int], T]) -> List[T]:
    """``parse(record, line)`` of every record with a date; an empty list when there is no file.

    Raises ValueError for a file that cannot be read (``parse`` raises it for a bad record).
    """

    file = Path(path)
    if not file.exists():
        return []
    with file.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        try:
            absent = [name for name in fields if name not in (reader.fieldnames or [])]
            if absent:
                raise ValueError(f"{file}: missing column(s): {', '.join(absent)}")
            return [parse(record, reader.line_num) for record in reader if (record.get("date") or "").strip()]
        except csv.Error as error:
            # DictReader counts a line once its record has been read, so the bad record starts on the next one.
            raise ValueError(f"{file.name} line {reader.line_num + 1}: {error}") from None


def read_history(path: Union[str, Path]) -> List[Row]:
    """Every row of the history file (an empty list when it does not exist yet).

    Raises ValueError for a file that cannot be read as the history.
    """

    def parse(record: Dict[str, Any], line: int) -> Row:
        return Row(
            date=_date(record, file="history.csv", line=line),
            track_id=(record.get("track_id") or "").strip(),
            name=record.get("name") or "",
            artists=record.get("artists") or "",
            playcount=_number(record.get("playcount"), line=line, column="playcount"),
        )

    return _read_csv(path, FIELDS, parse)


def read_relinks(path: Union[str, Path]) -> List[Relink]:
    """Every entry of the relinks file (an empty list when it does not exist yet).

    Raises ValueError for a file that cannot be read as one.
    """

    def parse(record: Dict[str, Any], line: int) -> Relink:
        day = _date(record, file="relinks.csv", line=line)
        track_id = (record.get("track_id") or "").strip()
        answered_id = (record.get("answered_id") or "").strip()
        if not track_id or not answered_id:
            raise ValueError(f"relinks.csv line {line}: a track_id and an answered_id are needed")
        return Relink(date=day, track_id=track_id, answered_id=answered_id)

    return _read_csv(path, RELINK_FIELDS, parse)


def _cell(value: Optional[int]) -> str:
    return "" if value is None else str(value)


def sort_rows(rows: Iterable[Row]) -> List[Row]:
    return sorted(rows, key=lambda row: (row.date, row.track_id))


def _format_csv(fields: Sequence[str], records: Iterable[Sequence[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(fields)
    writer.writerows(records)
    return buffer.getvalue()


def format_history(rows: Iterable[Row]) -> str:
    """The CSV text: header, then rows by date and track id, ``\\n`` line ends."""

    return _format_csv(FIELDS, ([row.date, row.track_id, row.name, row.artists, _cell(row.playcount)] for row in sort_rows(rows)))


def format_relinks(relinks: Iterable[Relink]) -> str:
    """The relinks CSV text: header, then entries by date and track id, ``\\n`` line ends."""

    ordered = sorted(relinks, key=lambda relink: (relink.date, relink.track_id))
    return _format_csv(RELINK_FIELDS, ([relink.date, relink.track_id, relink.answered_id] for relink in ordered))


def write_text_if_changed(path: Union[str, Path], text: str) -> bool:
    """Write ``text`` (UTF-8, ``\\n`` line ends) unless the file already holds it. True if written."""

    file = Path(path)
    if file.exists() and file.read_bytes() == text.encode("utf-8"):
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(temporary, file)
    return True


def write_history(path: Union[str, Path], rows: Iterable[Row]) -> bool:
    return write_text_if_changed(path, format_history(rows))


def write_relinks(path: Union[str, Path], relinks: Iterable[Relink]) -> bool:
    return write_text_if_changed(path, format_relinks(relinks))


def upsert(rows: Iterable[Row], new_rows: Iterable[Row]) -> List[Row]:
    """``rows`` plus ``new_rows``; a new row replaces the row of the same track and day."""

    merged: Dict[Tuple[str, str], Row] = {(row.date, row.track_id): row for row in rows}
    for row in new_rows:
        merged[(row.date, row.track_id)] = row
    return sort_rows(merged.values())


def rows_for_day(day: str, plays: Iterable[Any]) -> List[Row]:
    """History rows for ``day`` from fetched play counts (objects with the TrackPlays fields)."""

    return [
        Row(
            date=day,
            track_id=item.id,
            name=item.name,
            artists=ARTIST_SEPARATOR.join(item.artists),
            playcount=item.playcount,
        )
        for item in plays
    ]


def upsert_relinks(relinks: Iterable[Relink], day: str, plays: Iterable[Any]) -> List[Relink]:
    """``relinks`` with ``day`` recorded for the fetched ``plays`` (objects with the TrackPlays fields).

    Like :func:`upsert`: recording a day again replaces that day's entries of the tracks fetched
    this time, so a track that is no longer relinked loses its entry for the day.
    """

    plays = list(plays)
    fetched = {item.id for item in plays}
    kept = [relink for relink in relinks if relink.date != day or relink.track_id not in fetched]
    added = [Relink(date=day, track_id=item.id, answered_id=item.answered_id) for item in plays if item.answered_id != item.id]
    return sorted(kept + added, key=lambda relink: (relink.date, relink.track_id))


def answered_ids(relinks: Iterable[Relink]) -> Dict[Tuple[str, str], str]:
    """``{(date, track_id): answered_id}``. A listed id that is not here answered as itself that day."""

    return {(relink.date, relink.track_id): relink.answered_id for relink in relinks}


# --- streams between days ----------------------------------------------------


def streams_between(current: Optional[int], earlier: Optional[int]) -> Optional[int]:
    """Plays gained from ``earlier`` to ``current``.

    None when either count is missing, or is 0: Spotify shows 0 until a track passes 1,000
    plays, so the real count, and the difference, are unknown. The result is negative when
    Spotify lowered a count.
    """

    if not current or not earlier:  # None or 0
        return None
    return current - earlier


def implausible(change: int, playcount: int, days: int = 1) -> bool:
    """Whether a count that moved by ``change`` in ``days`` days moved by more than streams explain.

    That is a move, up or down, of more than JUMP_SHARE of ``playcount`` (the larger of the two
    counts) and of more than JUMP_FLOOR plays, per day: Spotify replaced or corrected the count,
    or answered with another release of the song without saying so. The floor leaves small songs
    alone: one with a few thousand plays can double in a day.
    """

    size = abs(change)
    return size > JUMP_SHARE * playcount * days and size > JUMP_FLOOR * days


def change(current: Optional[int], previous: Optional[int]) -> Tuple[Optional[int], Optional[float]]:
    """``(difference, percent)``; None where a value is missing, and the percent also when previous <= 0."""

    if current is None or previous is None:
        return None, None
    difference = current - previous
    return difference, (difference / previous * 100.0 if previous > 0 else None)


@dataclass(frozen=True)
class Entry:
    """A track's row for one day with the streams it gained."""

    row: Row
    #: Plays gained since exactly one day earlier; None when the two days give no figure.
    daily: Optional[int]
    #: The same figure one day earlier (plays gained between two and one days earlier).
    previous_daily: Optional[int]
    #: Plays gained since exactly seven days earlier.
    weekly: Optional[int]
    #: Why ``daily`` is None: NO_EARLIER_COUNT, BELOW_1000, VERSION_CHANGED or IMPLAUSIBLE_JUMP.
    no_daily: Optional[str] = None
    #: The track whose count ``row`` holds: another release when Spotify relinked ``row.track_id``.
    answered_id: Optional[str] = None
    #: The row one day earlier, when there is one, and the track that answered then.
    earlier: Optional[Row] = None
    earlier_answered_id: Optional[str] = None

    @property
    def answered(self) -> str:
        """The id of the track whose count ``row`` holds."""

        return self.answered_id or self.row.track_id

    @property
    def no_daily_reason(self) -> Optional[str]:
        """Why there is no daily figure, or None when there is one."""

        return None if self.daily is not None else self.no_daily or NO_EARLIER_COUNT

    @property
    def count_change(self) -> Optional[int]:
        """The count minus the count one day earlier, as recorded (None without both)."""

        if self.earlier is None or self.earlier.playcount is None or self.row.playcount is None:
            return None
        return self.row.playcount - self.earlier.playcount

    @property
    def daily_change(self) -> Tuple[Optional[int], Optional[float]]:
        """How ``daily`` compares with ``previous_daily``: ``(difference, percent)``."""

        return change(self.daily, self.previous_daily)


def index_rows(rows: Iterable[Row]) -> Dict[str, Dict[str, Row]]:
    """``{track_id: {date: row}}``."""

    index: Dict[str, Dict[str, Row]] = {}
    for row in rows:
        index.setdefault(row.track_id, {})[row.date] = row
    return index


def days_before(day: str, days: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=days)).isoformat()


def _gained(
    days: Mapping[str, Row], versions: Mapping[str, str], later: str, earlier: str, span: int = 1
) -> Tuple[Optional[int], Optional[str]]:
    """Plays gained from day ``earlier`` to day ``later`` (``span`` days apart): ``(figure, None)``,
    or ``(None, reason)`` when the two days give no figure. ``versions`` maps a day to the
    track that answered that day."""

    new, old = days.get(later), days.get(earlier)
    current = new.playcount if new else None
    previous = old.playcount if old else None
    if current is None or previous is None:
        return None, NO_EARLIER_COUNT
    if current == 0 or previous == 0:
        return None, BELOW_1000
    if versions.get(later) != versions.get(earlier):
        return None, VERSION_CHANGED
    gained = current - previous
    if implausible(gained, max(current, previous), span):
        return None, IMPLAUSIBLE_JUMP
    return gained, None


def _jump_within(days: Mapping[str, Row], versions: Mapping[str, str], start: str, end: str) -> bool:
    """Whether a count jumped (see :func:`implausible`) between two consecutive days after ``start``, up to ``end``."""

    on = date.fromisoformat(start) + timedelta(days=1)
    last = date.fromisoformat(end)
    while on <= last:
        later = on.isoformat()
        if _gained(days, versions, later, days_before(later, 1))[1] == IMPLAUSIBLE_JUMP:
            return True
        on += timedelta(days=1)
    return False


def entries_for_day(
    rows: Iterable[Row],
    day: str,
    track_ids: Optional[Sequence[str]] = None,
    relinks: Iterable[Relink] = (),
) -> List[Entry]:
    """Each track's row on ``day`` with the streams it gained since exactly 1 and 7 days earlier.

    Only ``track_ids`` are included when given (in that order); tracks without a row on
    ``day`` are left out. Two counts give a figure when both are above 0, the same track
    answered on both days (``relinks``), and the change is plausible as streams: the 7-day
    figure also needs every step between two recorded consecutive days of the week to be.
    """

    index = index_rows(rows)
    answered = answered_ids(relinks)
    week = [days_before(day, back) for back in range(8)]  # day, then the seven days before it
    day_1, day_2, day_7 = week[1], week[2], week[7]
    ids = list(track_ids) if track_ids is not None else sorted(index)
    entries = []
    for track_id in ids:
        days = index.get(track_id, {})
        row = days.get(day)
        if row is None:
            continue
        versions = {on: answered.get((on, track_id), track_id) for on in week}
        daily, no_daily = _gained(days, versions, day, day_1)
        previous_daily, _ = _gained(days, versions, day_1, day_2)
        weekly, _ = _gained(days, versions, day, day_7, span=7)
        if weekly is not None and _jump_within(days, versions, day_7, day):
            weekly = None
        earlier = days.get(day_1)
        entries.append(
            Entry(
                row=row,
                daily=daily,
                previous_daily=previous_daily,
                weekly=weekly,
                no_daily=no_daily,
                answered_id=versions[day],
                earlier=earlier,
                earlier_answered_id=versions[day_1] if earlier else None,
            )
        )
    return entries


def latest_day(rows: Iterable[Row], track_ids: Optional[Sequence[str]] = None) -> Optional[str]:
    """The most recent date with a row (for ``track_ids`` only, when given)."""

    wanted = set(track_ids) if track_ids is not None else None
    days = [row.date for row in rows if wanted is None or row.track_id in wanted]
    return max(days) if days else None


def first_day(rows: Iterable[Row], track_ids: Optional[Sequence[str]] = None) -> Optional[str]:
    """The earliest date with a row (for ``track_ids`` only, when given)."""

    wanted = set(track_ids) if track_ids is not None else None
    days = [row.date for row in rows if wanted is None or row.track_id in wanted]
    return min(days) if days else None


def last_rows(rows: Iterable[Row]) -> Dict[str, Row]:
    """Each track's most recent row."""

    out: Dict[str, Row] = {}
    for row in rows:
        current = out.get(row.track_id)
        if current is None or row.date >= current.date:
            out[row.track_id] = row
    return out
