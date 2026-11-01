from urllib.parse import parse_qs, urlsplit

import pytest
from helpers import BLINDING_LIGHTS, SHAPE_OF_YOU, UNKNOWN, FakeApi, FakeSession, fake_id, load_fixture, track_object

from tracker.fetch import (
    DIRECT_BASE,
    BatchRejected,
    FatalFetchError,
    RequestBudget,
    RequestLimitReached,
    batched,
    fetch_plays,
    make_client,
    parse_tracks_response,
    requests_needed,
    resolve_api,
    sdk_fetcher,
)

# --- batching ----------------------------------------------------------------


def test_batches_of_fifty_keep_order():
    ids = [fake_id(i) for i in range(120)]
    batches = batched(ids)
    assert [len(batch) for batch in batches] == [50, 50, 20]
    assert [i for batch in batches for i in batch] == ids
    assert [len(batch) for batch in batched(ids[:50])] == [50]
    assert [len(batch) for batch in batched(ids[:51])] == [50, 1]
    assert batched([]) == []


def test_batch_size_must_be_positive():
    with pytest.raises(ValueError):
        batched(["a"], 0)


@pytest.mark.parametrize("count, requests", [(0, 0), (1, 1), (50, 1), (51, 2), (1000, 20), (1400, 28), (1500, 30)])
def test_requests_needed(count, requests):
    assert requests_needed(count) == requests


# --- one response --------------------------------------------------------------


def test_parses_a_real_api_response():
    # A real answer: the first track was asked for as 3w3y8KPTfNeOKPiqUTakBh and relinked.
    body = load_fixture("tracks_playcount.json")
    requested = ["3w3y8KPTfNeOKPiqUTakBh", "32OlwWuMpZ6b0aN2RZOeMS", "5EcG8eMMlwkHRVa4aTR1qd", UNKNOWN]
    result = parse_tracks_response(body, requested)
    assert set(result.plays) == set(requested[:3])
    relinked = result.plays["3w3y8KPTfNeOKPiqUTakBh"]
    assert (relinked.id, relinked.name, relinked.artists, relinked.playcount) == (
        "3w3y8KPTfNeOKPiqUTakBh",
        "Locked out of Heaven",
        ["Bruno Mars"],
        3584428448,
    )
    # the count is the answering track's: its id is kept with it
    assert relinked.answered_id == "5g7sDjBhZ4I3gcFIpkrLuI" and relinked.relinked
    plain = result.plays["32OlwWuMpZ6b0aN2RZOeMS"]
    assert plain.artists == ["Mark Ronson", "Bruno Mars"]
    assert plain.answered_id == "32OlwWuMpZ6b0aN2RZOeMS" and not plain.relinked
    for track in body["tracks"]:
        assert isinstance(track["playcount"], int)
    assert result.missing == [UNKNOWN]  # unknown to Spotify, never retried
    assert result.retry == []


def test_the_answered_id_alone_does_not_count_for_a_relinked_track():
    # Asking for the id the answer carries is fine too, but then the relinked one was not asked for.
    body = load_fixture("tracks_playcount.json")
    result = parse_tracks_response(body, ["5g7sDjBhZ4I3gcFIpkrLuI"])
    assert set(result.plays) == {"5g7sDjBhZ4I3gcFIpkrLuI"}


def test_a_relinked_track_is_recorded_under_the_requested_id():
    answered = fake_id(99)
    body = {
        "tracks": [
            {**track_object(answered, "Song", 1234), "linked_from": {"id": fake_id(1), "type": "track"}},
            track_object(fake_id(2), "Other", 5678),
        ],
        "missing": [],
    }
    result = parse_tracks_response(body, [fake_id(1), fake_id(2)])
    assert set(result.plays) == {fake_id(1), fake_id(2)}
    assert result.plays[fake_id(1)].id == fake_id(1) and result.plays[fake_id(1)].playcount == 1234
    assert result.plays[fake_id(1)].answered_id == answered
    assert result.retry == []


def test_a_relinked_track_without_an_id_of_its_own_counts_as_the_requested_one():
    body = {"tracks": [{**track_object(fake_id(1), "Song", 5), "id": None, "linked_from": {"id": fake_id(1)}}]}
    plays = parse_tracks_response(body, [fake_id(1)]).plays
    assert plays[fake_id(1)].answered_id == fake_id(1) and not plays[fake_id(1)].relinked


def test_two_requested_ids_answered_by_the_same_track():
    # fake 1 relinks to fake 2, which was asked for too: each requested id gets its own row.
    body = {
        "tracks": [
            {**track_object(fake_id(2), "Song", 10), "linked_from": {"id": fake_id(1)}},
            track_object(fake_id(2), "Song", 10),
        ],
    }
    result = parse_tracks_response(body, [fake_id(1), fake_id(2)])
    assert set(result.plays) == {fake_id(1), fake_id(2)}
    assert {item.answered_id for item in result.plays.values()} == {fake_id(2)}


def test_value_rules():
    body = {
        "tracks": [
            # 0 is a real value: Spotify shows 0 until a track passes 1,000 plays
            {**track_object(fake_id(1), "  Spaced \n Name ", 0, artists=("A  B", "", "C"))},
            # a numeric string is accepted
            track_object(fake_id(2), "B", "123"),
            # null (not ready in time) and junk values mean "ask again"
            track_object(fake_id(3), "C", None),
            track_object(fake_id(4), "D", -5),
            track_object(fake_id(5), "E", True),
            track_object(fake_id(6), "F", 1.5),
            {"id": fake_id(7), "name": "G"},  # no playcount at all
            # a track nobody asked for is ignored, and so is a linked_from that is not an id
            track_object(fake_id(9), "Z", 1),
            {**track_object(fake_id(10), "Y", 1), "linked_from": {"id": ["not", "an", "id"]}},
            "not a track",
        ],
    }
    requested = [fake_id(i) for i in range(1, 9)]
    result = parse_tracks_response(body, requested)
    assert set(result.plays) == {fake_id(1), fake_id(2)}
    first = result.plays[fake_id(1)]
    assert (first.name, first.artists, first.playcount) == ("Spaced Name", ["A B", "C"], 0)
    assert result.plays[fake_id(2)].playcount == 123
    assert result.retry == [fake_id(i) for i in range(3, 9)]  # 8: asked for, not in the answer


def test_a_null_answer_does_not_hide_a_later_one():
    body = {"tracks": [track_object(fake_id(1), "A", None), track_object(fake_id(1), "A", 7)]}
    assert parse_tracks_response(body, [fake_id(1)]).plays[fake_id(1)].playcount == 7


def test_rejects_a_body_without_tracks():
    with pytest.raises(ValueError):
        parse_tracks_response({"error": "nope"}, [BLINDING_LIGHTS])
    with pytest.raises(ValueError):
        parse_tracks_response(["not", "a", "dict"], [BLINDING_LIGHTS])


# --- the fetch loop -----------------------------------------------------------


def _universe(count):
    return [track_object(fake_id(i), f"Song {i}", 1_000_000 + i) for i in range(count)]


def test_retries_only_the_tracks_without_a_play_count():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120), slow={ids[3], ids[60], ids[119]})
    report = fetch_plays(ids, api, pause=0, log=lambda _: None)
    assert set(report.plays) == set(ids)
    assert [len(call) for call in api.calls] == [50, 50, 20, 3]
    assert api.calls[-1] == [ids[3], ids[60], ids[119]]
    assert report.requests == 4 and report.failed == [] and report.stopped is None


def test_relinked_tracks_need_no_retry_and_are_logged():
    ids = [fake_id(i) for i in range(3)]
    api = FakeApi(_universe(3), relinked={ids[1]: fake_id(77)})
    logged = []
    report = fetch_plays(ids, api, pause=0, log=logged.append)
    assert set(report.plays) == set(ids) and len(api.calls) == 1
    assert report.plays[ids[1]].answered_id == fake_id(77)
    assert logged == ["Request 1 (3 tracks): play counts for 3, 1 answered by another track id (relinked)"]


def test_gives_up_after_the_retry_passes():
    ids = [fake_id(i) for i in range(3)]
    api = FakeApi(_universe(3), never_ready={ids[1]})
    report = fetch_plays(ids, api, retry_passes=2, pause=0, log=lambda _: None)
    assert report.failed == [ids[1]]
    assert len(api.calls) == 3  # first pass + 2 retry passes


def test_after_a_pass_without_play_counts_the_next_pass_checks_with_one_request():
    ids = [fake_id(i) for i in range(150)]
    api = FakeApi(_universe(150), never_ready=ids[50:])
    logged = []
    report = fetch_plays(ids, api, retry_passes=5, pause=0, log=logged.append)
    assert len(report.plays) == 50 and report.failed == ids[50:]
    # first pass: 3 requests; retry pass 1 gets nothing from 2; retry pass 2 stops after 1
    assert [len(call) for call in api.calls] == [50] * 6
    assert any(
        line.startswith("Retry pass 2: 100 track(s) still without a play count. The last pass got none") for line in logged
    )
    assert logged[-1] == "Still no play counts, so the 100 track(s) left are not asked for again this run."


def test_an_outage_costs_one_pass_and_one_more_request():
    ids = [fake_id(i) for i in range(1000)]
    sent = []

    def fetch(batch):
        sent.append(batch)
        raise ConnectionError("connection refused")

    report = fetch_plays(ids, fetch, retry_passes=2, pause=0, log=lambda _: None)
    assert len(sent) == 21 and report.requests == 21  # 20 in the first pass, then 1 to check
    assert report.plays == {} and report.stopped is None
    assert report.failed == ids


def test_the_next_pass_goes_on_when_the_api_is_back():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120))
    sent = []

    def fetch(batch):
        sent.append(batch)
        if len(sent) <= 3:  # down for the whole first pass
            raise ConnectionError("connection refused")
        return api(batch)

    report = fetch_plays(ids, fetch, pause=0, log=lambda _: None)
    assert set(report.plays) == set(ids) and report.failed == []
    assert len(sent) == 6 and report.requests == 6


def test_unknown_tracks_are_not_retried():
    ids = [fake_id(0), UNKNOWN]
    api = FakeApi(_universe(1), missing={UNKNOWN})
    report = fetch_plays(ids, api, pause=0, log=lambda _: None)
    assert report.missing == [UNKNOWN]
    assert len(api.calls) == 1
    assert report.failed == []


def test_stops_at_the_request_limit():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120))
    report = fetch_plays(ids, api, budget=RequestBudget(2), pause=0, log=lambda _: None)
    assert len(api.calls) == 2 and report.requests == 2
    assert len(report.plays) == 100
    assert report.failed == ids[100:]
    assert "limit of 2 requests" in report.stopped


def test_retry_passes_count_towards_the_limit():
    ids = [fake_id(i) for i in range(60)]
    api = FakeApi(_universe(60), slow={ids[0]})
    report = fetch_plays(ids, api, budget=RequestBudget(2), pause=0, log=lambda _: None)
    assert len(api.calls) == 2
    assert report.failed == [ids[0]]


def test_the_request_budget():
    with pytest.raises(ValueError):
        RequestBudget(0)
    budget = RequestBudget(2)
    budget.spend()
    budget.spend({"attempt": 1})  # also the SDK's on_retry hook
    assert budget.sent == 2 and budget.used_up
    with pytest.raises(RequestLimitReached, match="limit of 2 requests"):
        budget.spend()
    assert budget.sent == 2
    unlimited = RequestBudget()
    for _ in range(1000):
        unlimited.spend()
    assert not unlimited.used_up


def test_stops_starting_requests_at_the_time_limit():
    ids = [fake_id(i) for i in range(150)]
    api = FakeApi(_universe(150))
    now = [0.0]

    def fetch(batch):
        now[0] += 50  # every request takes 50 seconds
        return api(batch)

    report = fetch_plays(ids, fetch, time_limit=90, pause=0, log=lambda _: None, clock=lambda: now[0])
    assert len(api.calls) == 2  # started at 0 and 50 s; at 100 s the limit had passed
    assert len(report.plays) == 100 and report.failed == ids[100:]
    assert report.stopped == "the time limit of 1.5 minutes per run was reached"


def test_a_fatal_error_stops_and_keeps_what_was_fetched():
    ids = [fake_id(i) for i in range(120)]
    api = FakeApi(_universe(120))

    def fetch(batch):
        if len(api.calls) == 1:
            raise FatalFetchError("the plan's request quota is used up")
        return api(batch)

    report = fetch_plays(ids, fetch, pause=0, log=lambda _: None)
    assert len(report.plays) == 50
    assert report.stopped == "the plan's request quota is used up"
    assert report.failed == ids[50:]
    assert report.requests == 2


def test_a_failed_request_is_asked_again_in_the_next_pass():
    ids = [fake_id(i) for i in range(10)]
    api = FakeApi(_universe(10))
    failures = [ConnectionError("reset by peer")]

    def fetch(batch):
        if failures:
            raise failures.pop()
        return api(batch)

    logged = []
    report = fetch_plays(ids, fetch, pause=0, log=logged.append)
    assert set(report.plays) == set(ids)
    assert report.requests == 2
    assert logged[0] == "Request 1 (10 tracks): failed (ConnectionError: reset by peer)"
    assert logged[-1] == "Request 2 (10 tracks): play counts for 10"


def test_a_refused_batch_is_not_sent_again():
    ids = [fake_id(i) for i in range(60)]
    api = FakeApi(_universe(60))

    def fetch(batch):
        if batch[0] == ids[0]:
            raise BatchRejected("HTTP 400")
        return api(batch)

    report = fetch_plays(ids, fetch, pause=0, log=lambda _: None)
    assert len(report.plays) == 10
    assert report.failed == ids[:50]
    assert report.requests == 2


def test_two_refusals_in_a_row_without_any_play_count_stop_the_run():
    # A refusal for every request (a wrong address, a parameter the server does not take): two are enough.
    ids = [fake_id(i) for i in range(1000)]
    sent = []

    def fetch(batch):
        sent.append(batch)
        raise BatchRejected("HTTP 400 INVALID_PARAMETER: include")

    logged = []
    report = fetch_plays(ids, fetch, pause=0, log=logged.append)
    assert len(sent) == 2 and report.requests == 2
    assert report.plays == {} and sorted(report.failed) == sorted(ids)
    assert report.stopped.startswith("the API refused 2 requests in a row and returned no play count")
    assert "SPOTIFY_API_BASE" in report.stopped
    assert logged[-1].startswith("Request 2 (50 tracks): refused (HTTP 400")


def test_refusals_after_play_counts_do_not_stop_the_run():
    ids = [fake_id(i) for i in range(200)]
    api = FakeApi(_universe(200))

    def fetch(batch):
        if batch[0] != ids[0]:
            raise BatchRejected("HTTP 400")
        return api(batch)

    report = fetch_plays(ids, fetch, pause=0, log=lambda _: None)
    assert report.requests == 4 and report.stopped is None
    assert len(report.plays) == 50 and report.failed == ids[50:]


def test_pauses_between_requests():
    ids = [fake_id(i) for i in range(120)]
    slept = []
    fetch_plays(ids, FakeApi(_universe(120)), pause=1.5, log=lambda _: None, sleep=slept.append)
    assert slept == [1.5, 1.5]


def test_duplicate_ids_are_asked_once():
    api = FakeApi(_universe(2))
    fetch_plays([fake_id(0), fake_id(1), fake_id(0)], api, pause=0, log=lambda _: None)
    assert api.calls == [[fake_id(0), fake_id(1)]]


# --- the SDK client -----------------------------------------------------------


def test_resolve_api():
    assert resolve_api(None) == ("rapidapi", None, None)
    assert resolve_api("  ") == ("rapidapi", None, None)
    assert resolve_api(DIRECT_BASE + "/") == ("proxy", DIRECT_BASE, None)
    assert resolve_api("https://spotify81.p.rapidapi.com") == (
        "rapidapi",
        "https://spotify81.p.rapidapi.com",
        "spotify81.p.rapidapi.com",
    )
    with pytest.raises(ValueError):
        resolve_api("spotify-proxy.checkleaked.cc")


def test_sdk_request_through_rapidapi():
    session = FakeSession(
        FakeApi([track_object(BLINDING_LIGHTS, "Blinding Lights", 100), track_object(SHAPE_OF_YOU, "Shape of You", 90)])
    )
    fetch = sdk_fetcher(make_client("test-key-123", None, session=session, retries=0))
    body = fetch([BLINDING_LIGHTS, SHAPE_OF_YOU])
    assert [track["id"] for track in body["tracks"]] == [BLINDING_LIGHTS, SHAPE_OF_YOU]
    request = session.requests[0]
    url = urlsplit(request["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "spotify81.p.rapidapi.com", "/tracks")
    assert parse_qs(url.query) == {"ids": [f"{BLINDING_LIGHTS},{SHAPE_OF_YOU}"], "include": ["playcount"]}
    assert request["headers"]["x-rapidapi-key"] == "test-key-123"
    assert request["headers"]["x-rapidapi-host"] == "spotify81.p.rapidapi.com"
    assert "spotify-most-streamed-songs-tracker" in request["headers"]["user-agent"]


def test_sdk_request_through_the_direct_api():
    session = FakeSession(FakeApi([track_object(BLINDING_LIGHTS, "Blinding Lights", 100)]))
    fetch = sdk_fetcher(make_client("direct-key", DIRECT_BASE, session=session, retries=0))
    fetch([BLINDING_LIGHTS])
    request = session.requests[0]
    assert request["url"].startswith(DIRECT_BASE + "/tracks?")
    assert request["headers"]["x-rapidapi-key"] == "direct-key"
    assert "x-rapidapi-host" not in request["headers"]


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (403, {"message": "You are not subscribed to this API."}, FatalFetchError),
        (401, {"message": "Invalid API key."}, FatalFetchError),
        (429, {"message": "You have exceeded the MONTHLY quota for Requests on your current plan, BASIC."}, FatalFetchError),
        (404, {"message": "Cannot GET /tracks"}, FatalFetchError),
        (400, {"code": "INVALID_PARAMETER", "message": "ids", "param": "ids"}, BatchRejected),
    ],
)
def test_sdk_errors_are_classified(status, body, expected):
    fetch = sdk_fetcher(make_client("k", None, session=FakeSession(status=status, error_body=body), retries=0))
    with pytest.raises(expected):
        fetch([BLINDING_LIGHTS])


def test_a_404_points_at_the_address():
    # GET /tracks reports unknown ids in its body: a 404 means SPOTIFY_API_BASE is wrong.
    session = FakeSession(status=404, error_body={"message": "Cannot GET /tracks"})
    fetch = sdk_fetcher(make_client("k", "https://spotify-proxy.checkleaked.cc", session=session, retries=0))
    with pytest.raises(FatalFetchError) as raised:
        fetch([BLINDING_LIGHTS])
    message = str(raised.value)
    assert "HTTP 404" in message and "Check SPOTIFY_API_BASE" in message and DIRECT_BASE in message


def test_a_server_error_is_left_for_the_retry_pass():
    from spotify_api_sdk import HttpError

    session = FakeSession(status=500, error_body={"message": "boom"})
    fetch = sdk_fetcher(make_client("k", None, session=session))
    with pytest.raises(HttpError) as raised:
        fetch([BLINDING_LIGHTS])
    assert not isinstance(raised.value, (FatalFetchError, BatchRejected))
    assert len(session.requests) == 1  # no automatic retries unless asked for


def test_sdk_retries_count_towards_the_request_limit(no_sdk_wait):
    ids = [fake_id(i) for i in range(120)]
    session = FakeSession(status=503, error_body={"message": "Service unavailable"})
    budget = RequestBudget(4)
    fetch = sdk_fetcher(make_client("k", None, session=session, retries=2, budget=budget))
    logged = []
    report = fetch_plays(ids, fetch, budget=budget, pause=0, log=logged.append)
    assert len(session.requests) == 4 and report.requests == 4 and budget.sent == 4
    assert report.stopped == "the limit of 4 requests per run was reached"
    assert report.failed == ids
    assert logged[0].startswith("Request 1 (50 tracks, 2 automatic retries): failed (HTTP 503")
    # request 4 failed too, and its retry would have been the 5th request: it is not sent
    assert logged[1] == "Request 4 (50 tracks): the limit of 4 requests per run was reached"
