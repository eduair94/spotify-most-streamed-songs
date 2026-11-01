"""Read the track list (tracks.txt).

One song per line, written as a 22-character Spotify track id, a ``spotify:track:<id>`` URI or
an ``https://open.spotify.com/track/<id>`` URL (``?si=`` and ``intl-xx/`` are fine). ``#``
starts a comment, on its own line or after the track. Blank lines are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

_ID = r"[0-9A-Za-z]{22}"
ID_RE = re.compile(rf"^{_ID}$")
_URI_RE = re.compile(rf"^(?i:spotify:track:)({_ID})$")
_URL_RE = re.compile(rf"^(?:https?://)?(?i:open\.spotify\.com/)(?:intl-[A-Za-z-]+/)?(?i:track/)({_ID})(?:[/?].*)?$")
# A Spotify link or URI to something that is not a song: worth a more helpful message.
_OTHER_RE = re.compile(
    r"^(?:(?:https?://)?(?i:open\.spotify\.com/)(?:intl-[A-Za-z-]+/)?|(?i:spotify:))"
    r"(?i:(album|artist|playlist|episode|show|user))[/:]"
)


def parse_track(entry: str) -> Optional[str]:
    """The track id in one entry (id, URI or URL), or None if it is not one."""

    value = entry.strip()
    if ID_RE.match(value):
        return value
    match = _URI_RE.match(value) or _URL_RE.match(value)
    return match.group(1) if match else None


@dataclass
class TrackList:
    """The ids in file order, plus what was wrong with the other lines."""

    ids: List[str] = field(default_factory=list)
    #: (line number, message) for lines that are not a track: they are skipped.
    errors: List[Tuple[int, str]] = field(default_factory=list)
    #: (line number, message) for repeated tracks: the first line counts.
    warnings: List[Tuple[int, str]] = field(default_factory=list)


def _not_a_track(entry: str) -> str:
    shown = entry if len(entry) <= 80 else entry[:77] + "..."
    other = _OTHER_RE.match(entry)
    if other:
        kind = other.group(1).lower()
        return f"a Spotify {kind} link, not a track: {shown} (copy the link of a song instead)"
    return f"not a Spotify track id, URI or URL: {shown}"


def parse_tracks(text: str) -> TrackList:
    """Parse the contents of tracks.txt."""

    result = TrackList()
    first_line: Dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        track_id = parse_track(entry)
        if track_id is None:
            result.errors.append((number, _not_a_track(entry)))
            continue
        if track_id in first_line:
            result.warnings.append((number, f"{track_id} is already on line {first_line[track_id]}"))
            continue
        first_line[track_id] = number
        result.ids.append(track_id)
    return result


def read_tracks(path: Union[str, Path]) -> TrackList:
    """Read and parse a track list file (UTF-8, with or without a BOM)."""

    return parse_tracks(Path(path).read_text(encoding="utf-8-sig"))
