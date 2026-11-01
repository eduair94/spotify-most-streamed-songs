"""Fetch play counts: GET /tracks?ids=<up to 50 ids>&include=playcount.

One request covers up to 50 tracks. The API looks up the play counts within its own time
budget, so on a busy day a few tracks come back with ``playcount: null``: they are asked for
again, together with the tracks of failed requests, in up to ``retry_passes`` extra passes.
After a pass that got no play counts at all, the next pass goes on only if its first request
gets some, so an outage costs one request per 50 tracks plus one. Tracks the API lists as
``missing`` are unknown to Spotify and are not retried. A 404 stops the run at once (the
address in SPOTIFY_API_BASE is wrong), and so do two refused requests in a row before any play
count came back: the settings are wrong, and every other request would be refused too.

Spotify can answer a track id with another one (track relinking: another release of the same
song, the one playable in the API's market). Such a track carries ``linked_from`` with the id
that was asked for. Its play count is the other release's, and releases of one song can differ
by billions of plays, so it is recorded under the requested id (the one in tracks.txt) together
with the id that answered (``TrackPlays.answered_id``): the history can then tell when the
answering release changes, and gives no daily figure across that change.

Every HTTP request counts towards the run's :class:`RequestBudget`, the SDK's automatic retries
included, so the limit holds on a quota with a hard cap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

#: The most track ids GET /tracks accepts per request.
BATCH_SIZE = 50
#: Refused requests in a row, before any play count came back, that stop the run.
REFUSALS_BEFORE_STOP = 2
RAPIDAPI_BASE = "https://spotify81.p.rapidapi.com"
DIRECT_BASE = "https://spotify-proxy.checkleaked.cc/spotify-data"
USER_AGENT = "spotify-most-streamed-songs-tracker/1.0 (+https://github.com/eduair94/spotify-most-streamed-songs)"

FetchBatch = Callable[[List[str]], Any]
Log = Callable[[str], None]


@dataclass
class TrackPlays:
    """One track's play count as the API returned it, under the id that was asked for."""

    id: str
    name: str
    #: Credited artist names, in Spotify's order.
    artists: List[str]
    #: Lifetime plays. Spotify shows 0 until a track passes 1,000 plays.
    playcount: int
    #: The track that answered: another release of the song when Spotify relinked ``id`` (the
    #: play count is then that release's), else ``id`` itself.
    answered_id: str = ""

    def __post_init__(self) -> None:
        if not self.answered_id:
            self.answered_id = self.id

    @property
    def relinked(self) -> bool:
        return self.answered_id != self.id


class FatalFetchError(Exception):
    """Stop the run: asking again will not help (key refused, quota used up)."""


class RequestLimitReached(FatalFetchError):
    """The run has sent as many requests as it may."""


class BatchRejected(Exception):
    """The API refused this request (a 4xx other than 408/429): do not resend these ids."""


class RequestBudget:
    """The HTTP requests one run may send, counted as they go out.

    :func:`fetch_plays` spends one per batch. :func:`make_client` installs :meth:`spend` as the
    SDK client's ``on_retry`` hook, so each automatic retry is counted too, and a retry past the
    limit is never sent: the hook raises :class:`RequestLimitReached` instead.
    """

    def __init__(self, limit: Optional[int] = None) -> None:
        if limit is not None and limit < 1:
            raise ValueError("the request limit must be at least 1")
        self.limit = limit
        self.sent = 0

    @property
    def used_up(self) -> bool:
        return self.limit is not None and self.sent >= self.limit

    def spend(self, _info: Any = None) -> None:
        """Count one request about to be sent, or raise :class:`RequestLimitReached`."""

        if self.used_up:
            raise RequestLimitReached(f"the limit of {self.limit} requests per run was reached")
        self.sent += 1


def batched(items: Sequence[str], size: int = BATCH_SIZE) -> List[List[str]]:
    """Split ``items`` into lists of at most ``size``, keeping the order."""

    if size < 1:
        raise ValueError("size must be at least 1")
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def requests_needed(count: int, size: int = BATCH_SIZE) -> int:
    """How many requests ``count`` tracks take when nothing has to be retried."""

    return -(-count // size) if count > 0 else 0


def _count(value: Any) -> Optional[int]:
    """A non-negative whole number from a JSON value, else None."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _text(value: Any) -> str:
    """A name on one line: runs of whitespace (newlines included) become one space."""

    return " ".join(str(value).split()) if value is not None else ""


def _artist_names(value: Any) -> List[str]:
    names = []
    for artist in value if isinstance(value, list) else []:
        if isinstance(artist, dict):
            name = _text(artist.get("name"))
            if name:
                names.append(name)
    return names


def _id_list(value: Any) -> List[str]:
    ids = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            item = item.get("id")
        if isinstance(item, str) and item:
            ids.append(item)
    return ids


def requested_id(track: Dict[str, Any], wanted: Set[str]) -> Optional[str]:
    """The id in ``wanted`` that ``track`` answers: its ``linked_from`` id when it was relinked, else its own."""

    linked = track.get("linked_from")
    linked_id = linked.get("id") if isinstance(linked, dict) else None
    if isinstance(linked_id, str) and linked_id in wanted:
        return linked_id
    track_id = track.get("id")
    return track_id if isinstance(track_id, str) and track_id in wanted else None


@dataclass
class BatchResult:
    plays: Dict[str, TrackPlays]
    #: Requested ids without a play count this time (not ready, or "unavailable"): ask again.
    retry: List[str]
    #: Requested ids Spotify does not know: remove them from tracks.txt.
    missing: List[str]


def parse_tracks_response(body: Any, requested: Sequence[str]) -> BatchResult:
    """Read one GET /tracks?include=playcount response.

    A track is recorded under the id that was asked for (see :func:`requested_id`), with the id
    that answered, when its ``playcount`` is a whole number, 0 included. A track whose
    ``playcount`` is null, or that is not in the response at all, is retried.
    """

    if not isinstance(body, dict) or not isinstance(body.get("tracks"), list):
        raise ValueError("unexpected response: no 'tracks' list")
    wanted = set(requested)
    plays: Dict[str, TrackPlays] = {}
    for track in body["tracks"]:
        if not isinstance(track, dict):
            continue
        track_id = requested_id(track, wanted)
        if track_id is None or track_id in plays:
            continue
        playcount = _count(track.get("playcount"))
        if playcount is None:
            continue
        answered = track.get("id")
        plays[track_id] = TrackPlays(
            id=track_id,
            name=_text(track.get("name")),
            artists=_artist_names(track.get("artists")),
            playcount=playcount,
            answered_id=answered if isinstance(answered, str) and answered else track_id,
        )
    missing = [i for i in dict.fromkeys(_id_list(body.get("missing"))) if i in wanted and i not in plays]
    retry = [i for i in dict.fromkeys(requested) if i not in plays and i not in missing]
    return BatchResult(plays=plays, retry=retry, missing=missing)


@dataclass
class FetchReport:
    plays: Dict[str, TrackPlays] = field(default_factory=dict)
    #: Ids Spotify does not know.
    missing: List[str] = field(default_factory=list)
    #: Ids still without a play count after every pass (or skipped when the run stopped early).
    failed: List[str] = field(default_factory=list)
    #: HTTP requests sent, the SDK's automatic retries included.
    requests: int = 0
    #: Why the run stopped before asking for every track, if it did.
    stopped: Optional[str] = None


def _describe(error: BaseException) -> str:
    status = getattr(error, "status", None)
    code = getattr(error, "code", None)
    parts = [f"HTTP {status}" if status else type(error).__name__]
    if code and str(code) != str(status):
        parts.append(str(code))
    message = str(error).strip().splitlines()[0][:200] if str(error).strip() else ""
    return " ".join(parts) + (f": {message}" if message else "")


def _minutes(seconds: float) -> str:
    minutes = seconds / 60
    return f"{minutes:g} minute{'' if minutes == 1 else 's'}"


def _label(number: int, size: int, retries: int) -> str:
    """``Request 3 (50 tracks)``, plus the SDK's automatic retries of that request."""

    extra = f", {retries} automatic retr{'y' if retries == 1 else 'ies'}" if retries else ""
    return f"Request {number} ({size} track{'' if size == 1 else 's'}{extra})"


def fetch_plays(
    ids: Iterable[str],
    fetch_batch: FetchBatch,
    *,
    batch_size: int = BATCH_SIZE,
    retry_passes: int = 2,
    budget: Optional[RequestBudget] = None,
    time_limit: Optional[float] = None,
    pause: float = 1.0,
    log: Log = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> FetchReport:
    """Fetch play counts for ``ids``, ``batch_size`` per request, retrying the ones that lack one.

    ``fetch_batch(ids)`` returns the decoded response body. It raises :class:`FatalFetchError`
    to stop the run, :class:`BatchRejected` to drop a batch, and anything else for a failure
    worth retrying in the next pass. ``budget`` caps the requests of the whole run, retry passes
    included (no cap without one). No request is started once ``time_limit`` seconds have
    passed. After a pass without any play counts, the next pass stops at its first request that
    gets none either. The run stops after REFUSALS_BEFORE_STOP refused requests in a row while
    it has no play count at all.
    """

    budget = RequestBudget() if budget is None else budget
    started = clock()
    report = FetchReport()
    pending = list(dict.fromkeys(ids))
    rejected: List[str] = []
    dry = False  # the last pass got no play counts at all
    given_up = False
    refused = 0  # requests refused in a row
    for pass_number in range(max(0, retry_passes) + 1):
        if not pending or report.stopped or given_up:
            break
        if pass_number:
            log(
                f"Retry pass {pass_number}: {len(pending)} track(s) still without a play count"
                + (". The last pass got none, so this one goes on only if its first request gets some." if dry else "")
            )
        plays_before = len(report.plays)
        batches = batched(pending, batch_size)
        pending = []
        for index, batch in enumerate(batches):
            if budget.used_up:
                report.stopped = f"the limit of {budget.limit} requests per run was reached"
            elif time_limit is not None and clock() - started >= time_limit:
                report.stopped = f"the time limit of {_minutes(time_limit)} per run was reached"
            if report.stopped:
                for rest in batches[index:]:
                    pending.extend(rest)
                break
            if budget.sent and pause > 0:
                sleep(pause)
            budget.spend()
            number = budget.sent
            plays_so_far = len(report.plays)
            try:
                result = parse_tracks_response(fetch_batch(batch), batch)
            except FatalFetchError as error:  # also RequestLimitReached from an SDK retry
                report.stopped = outcome = str(error)
                for rest in batches[index:]:
                    pending.extend(rest)
            except BatchRejected as error:
                outcome = f"refused ({error}); these tracks are skipped this run"
                rejected.extend(batch)
                refused += 1
                if refused >= REFUSALS_BEFORE_STOP and not report.plays:
                    # Every request so far was refused: the settings are wrong, not the tracks.
                    report.stopped = (
                        f"the API refused {refused} requests in a row and returned no play count, so the settings "
                        f"are most likely wrong. Check SPOTIFY_API_BASE: leave it unset for RapidAPI; for a direct "
                        f"key it must be {DIRECT_BASE}"
                    )
                    for rest in batches[index + 1 :]:
                        pending.extend(rest)
            except Exception as error:  # network trouble, 5xx, odd body
                outcome = f"failed ({_describe(error)})"
                pending.extend(batch)
                refused = 0
            else:
                refused = 0
                report.plays.update(result.plays)
                report.missing.extend(result.missing)
                pending.extend(result.retry)
                notes = [f"play counts for {len(result.plays)}"]
                relinked = sum(1 for item in result.plays.values() if item.relinked)
                if relinked:
                    notes.append(f"{relinked} answered by another track id (relinked)")
                if result.retry:
                    notes.append(f"{len(result.retry)} to ask again")
                if result.missing:
                    notes.append(f"{len(result.missing)} unknown to Spotify")
                outcome = ", ".join(notes)
            log(f"{_label(number, len(batch), budget.sent - number)}: {outcome}")
            if report.stopped:
                break
            if len(report.plays) > plays_so_far:
                dry = False
            elif dry:
                # Nothing in the whole last pass and nothing now (an outage, or counts that never
                # get ready): asking again would only spend requests.
                given_up = True
                for rest in batches[index + 1 :]:
                    pending.extend(rest)
                log(f"Still no play counts, so the {len(pending)} track(s) left are not asked for again this run.")
                break
        dry = len(report.plays) == plays_before
    report.requests = budget.sent
    report.failed = list(dict.fromkeys(pending + rejected))
    return report


# --- the API client -----------------------------------------------------------


def resolve_api(base_url: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """``(provider, base_url, host)`` for the SDK client from SPOTIFY_API_BASE.

    Empty means RapidAPI (spotify81). A ``*.rapidapi.com`` URL is RapidAPI with that host
    header; any other URL is the direct API.
    """

    if base_url is None or not base_url.strip():
        return "rapidapi", None, None
    base = base_url.strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"SPOTIFY_API_BASE must be a full http(s) URL such as {DIRECT_BASE}")
    host = parts.hostname.lower()
    if host == "rapidapi.com" or host.endswith(".rapidapi.com"):
        return "rapidapi", base, host
    return "proxy", base, None


def make_client(
    api_key: str,
    base_url: Optional[str] = None,
    *,
    timeout: float = 90.0,
    retries: int = 0,
    budget: Optional[RequestBudget] = None,
    session: Any = None,
) -> Any:
    """A ``spotify_api_sdk.SpotifyApiClient`` for RapidAPI or the direct API.

    ``retries`` are the SDK's automatic retries of a request that failed with 429/5xx or a
    network error. There are none by default: :func:`fetch_plays` asks for those tracks again
    in its next pass. Each retry is spent from ``budget`` when one is given.
    """

    import spotify_api_sdk
    from spotify_api_sdk import SpotifyApiClient

    provider, base, host = resolve_api(base_url)
    return SpotifyApiClient(
        api_key,
        provider=provider,
        base_url=base,
        host=host,
        timeout=timeout,
        retries=retries,
        on_retry=budget.spend if budget is not None else None,
        session=session,
        user_agent=f"{USER_AGENT} checkleaked-spotify-api-python/{spotify_api_sdk.__version__}",
    )


#: Error codes that mean the key itself cannot be used.
_KEY_CODES = frozenset({"MISSING_API_KEY", "INVALID_API_KEY", "KEY_REVOKED", "KEY_EXPIRED"})


def sdk_fetcher(client: Any) -> FetchBatch:
    """``fetch_batch`` for :func:`fetch_plays` backed by the SDK client (see :func:`make_client`)."""

    from spotify_api_sdk import AuthError, HttpError

    def fetch(ids: List[str]) -> Any:
        try:
            return client.tracks.get(ids, include=["playcount"])
        except HttpError as error:
            status = error.status or 0
            if isinstance(error, AuthError) or status == 401 or error.code in _KEY_CODES:
                raise FatalFetchError(
                    f"the API refused the key ({_describe(error)}). Check the SPOTIFY_API_KEY secret: "
                    "a RapidAPI key must be subscribed to the spotify81 API, and a direct key also "
                    f"needs SPOTIFY_API_BASE={DIRECT_BASE}"
                ) from None
            if status == 402 or error.code == "QUOTA_EXCEEDED" or (status == 429 and not error.retryable):
                raise FatalFetchError(f"the plan's request quota is used up ({_describe(error)})") from None
            if status == 404:
                # GET /tracks answers unknown ids in its body ("missing"), never with a 404: the
                # address is wrong, and every other request would get the same answer.
                raise FatalFetchError(
                    f"the API has no GET /tracks at this address ({_describe(error)}). Check SPOTIFY_API_BASE: "
                    f"leave it unset for RapidAPI; for a direct key it must be {DIRECT_BASE}"
                ) from None
            if 400 <= status < 500 and status not in (408, 429):
                raise BatchRejected(_describe(error)) from None
            raise

    return fetch
