"""Offline test helpers: a fake API that answers like GET /tracks?include=playcount."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

BLINDING_LIGHTS = "0VjIjW4GlUZAMYd2vXMi3b"
SHAPE_OF_YOU = "7qiZfU4dY1lWllzX7mPBI3"
UNKNOWN = "0000000000000000000000"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fake_id(number: int) -> str:
    """A syntactically valid, made-up track id."""

    return f"fake{number:018d}"


def track_object(track_id: str, name: str, playcount: Optional[int], artists: Sequence[str] = ("Artist",)) -> Dict[str, Any]:
    return {
        "id": track_id,
        "name": name,
        "type": "track",
        "uri": f"spotify:track:{track_id}",
        "artists": [{"id": f"artist{index:016d}", "name": artist, "type": "artist"} for index, artist in enumerate(artists)],
        "album": {"id": "album0000000000000000a", "name": name, "release_date": "2020-01-01"},
        "playcount": playcount,
    }


class FakeApi:
    """Answers GET /tracks for a set of known tracks.

    ``slow`` ids come back with ``playcount: null`` the first time they are asked for,
    ``never_ready`` ids every time, ``missing`` ids are reported as unknown to Spotify, and
    ``relinked`` maps a requested id to the id the answer carries (with ``linked_from``).
    """

    def __init__(
        self,
        tracks: Iterable[Dict[str, Any]],
        *,
        slow: Iterable[str] = (),
        never_ready: Iterable[str] = (),
        missing: Iterable[str] = (),
        relinked: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.tracks = {track["id"]: track for track in tracks}
        self.slow = set(slow)
        self.never_ready = set(never_ready)
        self.missing = set(missing)
        self.relinked = dict(relinked or {})
        self.calls: List[List[str]] = []

    def answer(self, ids: List[str]) -> Dict[str, Any]:
        self.calls.append(list(ids))
        out = []
        for track_id in ids:
            if track_id in self.missing or track_id not in self.tracks:
                continue
            track = json.loads(json.dumps(self.tracks[track_id]))
            if track_id in self.relinked:
                track["id"] = self.relinked[track_id]
                track["linked_from"] = {"id": track_id, "type": "track", "uri": f"spotify:track:{track_id}"}
            if track_id in self.slow or track_id in self.never_ready:
                self.slow.discard(track_id)
                track["playcount"] = None
            out.append(track)
        resolved = sum(1 for track in out if track["playcount"] is not None)
        return {
            "tracks": out,
            "missing": [i for i in ids if i in self.missing],
            "unavailable": [],
            "include_status": {"playcount": {"requested": len(out), "resolved": resolved}},
        }

    def __call__(self, ids: List[str]) -> Dict[str, Any]:
        return self.answer(ids)


class FakeResponse:
    def __init__(self, status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> None:
        self.status_code = status
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")


class FakeSession:
    """Stands in for requests.Session inside the SDK client; records every request."""

    def __init__(self, api: Optional[FakeApi] = None, *, status: int = 200, error_body: Any = None) -> None:
        self.api = api
        self.status = status
        self.error_body = error_body
        self.requests: List[Dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, headers: Any = None, data: Any = None, timeout: Any = None) -> FakeResponse:
        self.requests.append({"method": method, "url": url, "headers": dict(headers or {}), "timeout": timeout})
        if self.status != 200:
            return FakeResponse(self.status, self.error_body or {"message": "error"})
        query = parse_qs(urlsplit(url).query)
        ids = query.get("ids", [""])[0].split(",")
        assert self.api is not None
        return FakeResponse(200, self.api.answer(ids))

    def close(self) -> None:
        self.closed = True
