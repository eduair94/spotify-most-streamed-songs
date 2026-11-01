"""data/history.csv: one row per track per UTC day, and the streams gained between days.

The file is kept sorted by date, then track id, so each day's rows are appended at the end.
Recording a day that is already there replaces those tracks' rows for that day instead of
adding new ones: re-running the tracker on the same day never duplicates rows.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

FIELDS = ("date", "track_id", "name", "artists", "playcount")

#: Between the artist names in the ``artists`` column ("," would split "Tyler, The Creator").
ARTIST_SEPARATOR = "; "


@dataclass(frozen=True)
class Row:
    date: str  # YYYY-MM-DD (UTC)
    track_id: str
    name: str
    artists: str  # credited artist names, joined with ARTIST_SEPARATOR
    playcount: Optional[int]


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


def read_history(path: Union[str, Path]) -> List[Row]:
    """Every row of the history file (an empty list when it does not exist yet).

    Raises ValueError for a file that cannot be read as the history.
    """

    file = Path(path)
    if not file.exists():
        return []
    with file.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        try:
            absent = [name for name in FIELDS if name not in (reader.fieldnames or [])]
            if absent:
                raise ValueError(f"{file}: missing column(s): {', '.join(absent)}")
            rows = []
            for record in reader:
                text = (record.get("date") or "").strip()
                if not text:
                    continue
                line = reader.line_num
                try:
                    day = date.fromisoformat(text).isoformat()
                except ValueError:
                    raise ValueError(f"history.csv line {line}: date is not YYYY-MM-DD: {text!r}") from None
                rows.append(
                    Row(
                        date=day,
                        track_id=(record.get("track_id") or "").strip(),
                        name=record.get("name") or "",
                        artists=record.get("artists") or "",
                        playcount=_number(record.get("playcount"), line=line, column="playcount"),
                    )
                )
        except csv.Error as error:
            # DictReader counts a line once its record has been read, so the bad record starts on the next one.
            raise ValueError(f"history.csv line {reader.line_num + 1}: {error}") from None
        return rows


def _cell(value: Optional[int]) -> str:
    return "" if value is None else str(value)


def sort_rows(rows: Iterable[Row]) -> List[Row]:
    return sorted(rows, key=lambda row: (row.date, row.track_id))


def format_history(rows: Iterable[Row]) -> str:
    """The CSV text: header, then rows by date and track id, ``\\n`` line ends."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FIELDS)
    for row in sort_rows(rows):
        writer.writerow([row.date, row.track_id, row.name, row.artists, _cell(row.playcount)])
    return buffer.getvalue()


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
    #: Plays gained since exactly one day earlier.
    daily: Optional[int]
    #: The same figure one day earlier (plays gained between two and one days earlier).
    previous_daily: Optional[int]
    #: Plays gained since exactly seven days earlier.
    weekly: Optional[int]

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


def entries_for_day(rows: Iterable[Row], day: str, track_ids: Optional[Sequence[str]] = None) -> List[Entry]:
    """Each track's row on ``day`` with the streams it gained since exactly 1 and 7 days earlier.

    Only ``track_ids`` are included when given (in that order); tracks without a row on
    ``day`` are left out.
    """

    index = index_rows(rows)
    day_1, day_2, day_7 = days_before(day, 1), days_before(day, 2), days_before(day, 7)
    ids = list(track_ids) if track_ids is not None else sorted(index)
    entries = []
    for track_id in ids:
        days = index.get(track_id, {})
        row = days.get(day)
        if row is None:
            continue
        count_1, count_2, count_7 = (_playcount(days.get(earlier)) for earlier in (day_1, day_2, day_7))
        entries.append(
            Entry(
                row=row,
                daily=streams_between(row.playcount, count_1),
                previous_daily=streams_between(count_1, count_2),
                weekly=streams_between(row.playcount, count_7),
            )
        )
    return entries


def _playcount(row: Optional[Row]) -> Optional[int]:
    return row.playcount if row else None


def latest_day(rows: Iterable[Row], track_ids: Optional[Sequence[str]] = None) -> Optional[str]:
    """The most recent date with a row (for ``track_ids`` only, when given)."""

    wanted = set(track_ids) if track_ids is not None else None
    days = [row.date for row in rows if wanted is None or row.track_id in wanted]
    return max(days) if days else None


def last_rows(rows: Iterable[Row]) -> Dict[str, Row]:
    """Each track's most recent row."""

    out: Dict[str, Row] = {}
    for row in rows:
        current = out.get(row.track_id)
        if current is None or row.date >= current.date:
            out[row.track_id] = row
    return out
