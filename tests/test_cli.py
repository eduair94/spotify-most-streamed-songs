import io
import json
import sys

import pytest
from helpers import BLINDING_LIGHTS, SHAPE_OF_YOU, FakeApi, FakeSession, fake_id, track_object

from tracker import cli
from tracker.cli import Console, main
from tracker.history import Relink, read_history, read_relinks
from tracker.render import README_END, README_START

KEY = "secret-test-key-0123456789"


@pytest.fixture
def repo(tmp_path):
    ids = [BLINDING_LIGHTS, SHAPE_OF_YOU] + [fake_id(i) for i in range(60)]
    (tmp_path / "tracks.txt").write_text(
        "# test list\n" + "\n".join(f"{track_id}  # song" for track_id in ids) + "\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(f"# Readme\n\n{README_START}\nplaceholder\n{README_END}\n", encoding="utf-8")
    return tmp_path


def universe(bump=0, step=0):
    """Every tracked song; ``bump`` plays are added to all, ``step`` more per song number."""

    tracks = [
        track_object(BLINDING_LIGHTS, "Blinding Lights", 5_000_000_000 + bump, ("The Weeknd",)),
        track_object(SHAPE_OF_YOU, "Shape of You", 4_000_000_000 + 2 * bump, ("Ed Sheeran",)),
    ]
    tracks += [track_object(fake_id(i), f"Song {i}", 1_000_000 + i + bump + step * i, (f"Artist {i}",)) for i in range(60)]
    return tracks


def run(repo, *args, env=None, session=None):
    lines = []
    environment = {"SPOTIFY_API_KEY": KEY} if env is None else env
    code = main(["run", "--root", str(repo), "--pause", "0", *args], env=environment, session=session, write=lines.append)
    return code, "\n".join(lines)


def test_without_a_key_it_skips_cleanly(repo):
    code, output = run(repo, env={})
    assert code == 0
    assert "SPOTIFY_API_KEY is not set" in output
    assert not (repo / "data").exists() and not (repo / "LEADERBOARD.md").exists()


def test_the_skip_is_a_notice_in_github_actions(repo):
    code, output = run(repo, env={"GITHUB_ACTIONS": "true"})
    assert code == 0 and output.startswith("::notice::SPOTIFY_API_KEY is not set")


def test_annotations_are_escaped_and_plain_output_is_not():
    lines = []
    Console(lines.append, github=True).warning("50% done\nnext")
    Console(lines.append, github=False).warning("50% done")
    assert lines == ["::warning::50%25 done%0Anext", "warning: 50% done"]


def test_a_run_writes_every_file_and_never_prints_the_key(repo):
    session = FakeSession(FakeApi(universe(), slow={fake_id(7)}))
    code, output = run(repo, "--date", "2026-11-01", session=session)
    assert code == 0, output
    assert KEY not in output
    assert len(session.requests) == 3  # 50 + 12, then the one song whose play count was late
    assert all(r["headers"]["x-rapidapi-key"] == KEY for r in session.requests)

    rows = read_history(repo / "data" / "history.csv")
    assert len(rows) == 62 and {r.date for r in rows} == {"2026-11-01"}
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["songs"][0]["name"] == "Blinding Lights" and latest["songs"][0]["daily_streams"] is None
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert (
        f"| 1 | [Blinding Lights](https://open.spotify.com/track/{BLINDING_LIGHTS}) | The Weeknd | 5,000,000,000 | — | — |"
        in leaderboard
    )
    assert "Daily streams start with the second day of tracking." in leaderboard
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "placeholder" not in readme and "| 2 | [Shape of You]" in readme
    assert "Recorded 62 of 62 tracks for 2026-11-01 with 3 request(s)." in output
    # no track was relinked: the relinks file is just its header
    assert (repo / "data" / "relinks.csv").read_text(encoding="utf-8") == "date,track_id,answered_id\n"
    assert "relinking" not in output


def test_relinked_songs_are_recorded_under_the_listed_id_with_the_answering_one(repo):
    api = FakeApi(universe(), relinked={SHAPE_OF_YOU: fake_id(999)})
    code, output = run(repo, "--date", "2026-11-01", session=FakeSession(api))
    assert code == 0
    ids = {row.track_id for row in read_history(repo / "data" / "history.csv")}
    assert SHAPE_OF_YOU in ids and fake_id(999) not in ids
    assert read_relinks(repo / "data" / "relinks.csv") == [Relink("2026-11-01", SHAPE_OF_YOU, fake_id(999))]
    assert (
        "warning: Spotify answered 1 listed track id(s) with another release of the song (track relinking): the counts "
        f"recorded under those ids are the other releases' counts: {SHAPE_OF_YOU} -> {fake_id(999)} (Shape of You)"
    ) in output
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    shape = next(song for song in latest["songs"] if song["id"] == SHAPE_OF_YOU)
    assert shape["answered_id"] == fake_id(999)


def test_another_release_answering_gives_no_daily_figure_that_day(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    relinked = {SHAPE_OF_YOU: fake_id(999)}
    code, output = run(repo, "--date", "2026-11-02", session=FakeSession(FakeApi(universe(bump=1_000_000), relinked=relinked)))
    assert code == 0
    assert (
        f"warning: 1 song(s) were answered by another track than on 2026-11-01, so they get no daily figure for "
        f"2026-11-02: {SHAPE_OF_YOU} ({SHAPE_OF_YOU} -> {fake_id(999)})"
    ) in output
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    shape = next(song for song in latest["songs"] if song["id"] == SHAPE_OF_YOU)
    assert (shape["answered_id"], shape["daily_streams"], shape["no_daily_reason"]) == (fake_id(999), None, "version_changed")
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert "1 song was answered with another release of the song than on 2026-11-01" in leaderboard

    # The same release answers the next day too: the figure is back.
    code, output = run(repo, "--date", "2026-11-03", session=FakeSession(FakeApi(universe(bump=2_500_000), relinked=relinked)))
    assert code == 0 and "were answered by another track" not in output
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    shape = next(song for song in latest["songs"] if song["id"] == SHAPE_OF_YOU)
    assert (shape["daily_streams"], shape["no_daily_reason"]) == (3_000_000, None)
    assert [r.date for r in read_relinks(repo / "data" / "relinks.csv")] == ["2026-11-02", "2026-11-03"]


def test_a_jump_is_left_out_of_the_daily_figures(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    tracks = universe(bump=1_000_000)
    tracks[0]["playcount"] += 1_000_000_000  # Blinding Lights: +1,001,000,000 in a day
    code, output = run(repo, "--date", "2026-11-02", session=FakeSession(FakeApi(tracks)))
    assert code == 0
    assert (
        "warning: The play count of 1 song(s) moved by more than 5% since 2026-11-01, which streams do not explain, so "
        f"it gives no daily figure for 2026-11-02: {BLINDING_LIGHTS} (+1,001,000,000)"
    ) in output
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    daily_table = leaderboard.split("## Top 50 by daily streams")[1]
    assert "Blinding Lights" not in daily_table.split("\n\n")[2]  # not ranked
    assert "[Blinding Lights](https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b) (+1,001,000,000)" in daily_table
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert (latest["songs"][0]["daily_streams"], latest["songs"][0]["no_daily_reason"]) == (None, "implausible_jump")


def test_a_missing_day_is_reported_as_such(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    code, _ = run(repo, "--date", "2026-11-03", session=FakeSession(FakeApi(universe(bump=2_000_000))))
    assert code == 0
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert "data/history.csv has no play counts of these songs from 2026-11-02 (no run recorded them that day)" in leaderboard
    assert "second day of tracking" not in leaderboard


def test_rerunning_the_same_day_is_idempotent(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    files = ["data/history.csv", "data/latest.json", "LEADERBOARD.md", "README.md"]
    before = {name: (repo / name).read_bytes() for name in files}
    code, output = run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    assert code == 0 and "No file changed." in output
    assert {name: (repo / name).read_bytes() for name in files} == before


def test_a_later_run_the_same_day_keeps_one_row_per_song(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe(bump=7))))
    rows = read_history(repo / "data" / "history.csv")
    assert len(rows) == 62
    assert {r.playcount for r in rows if r.track_id == BLINDING_LIGHTS} == {5_000_000_007}


def test_the_next_days_add_daily_streams_and_their_change(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    code, _ = run(repo, "--date", "2026-11-02", session=FakeSession(FakeApi(universe(bump=1_000_000))))
    assert code == 0
    assert len(read_history(repo / "data" / "history.csv")) == 124
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert "| The Weeknd | 5,001,000,000 | 1,000,000 | — |" in leaderboard
    assert "## Top 50 by daily streams" in leaderboard
    assert "No daily streams" not in leaderboard
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["date"] == "2026-11-02" and latest["songs"][0]["daily_streams"] == 1_000_000
    assert latest["songs"][1]["daily_streams"] == 2_000_000 and latest["songs"][1]["daily_streams_change"] is None

    code, _ = run(repo, "--date", "2026-11-03", session=FakeSession(FakeApi(universe(bump=2_500_000))))
    assert code == 0
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    # Shape of You gained 3,000,000 after 2,000,000 the day before
    assert "| Ed Sheeran | 3,000,000 | +1,000,000 (+50.00%) | 4,005,000,000 |" in leaderboard
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    shape = next(song for song in latest["songs"] if song["id"] == SHAPE_OF_YOU)
    assert (shape["daily_streams"], shape["daily_streams_change"], shape["daily_streams_change_pct"]) == (
        3_000_000,
        1_000_000,
        50.0,
    )


def test_songs_missing_today_are_listed_and_keep_their_history(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    api = FakeApi([t for t in universe() if t["id"] != BLINDING_LIGHTS])
    code, output = run(repo, "--date", "2026-11-02", "--retry-passes", "0", session=FakeSession(api))
    assert code == 0
    assert "1 track(s) got no play count this run" in output
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["not_updated"] == [{"id": BLINDING_LIGHTS, "name": "Blinding Lights", "last_date": "2026-11-01"}]
    assert "1 tracked song has no play count for 2026-11-02" in (repo / "LEADERBOARD.md").read_text(encoding="utf-8")


def test_unknown_tracks_are_reported_apart_and_kept_by_render(repo):
    api = FakeApi(universe(), missing={fake_id(55)})
    code, output = run(repo, "--date", "2026-11-01", session=FakeSession(api))
    assert code == 0
    assert f"Spotify does not know 1 track id(s); remove them from tracks.txt: {fake_id(55)}" in output
    assert "got no play count" not in output
    leaderboard = (repo / "LEADERBOARD.md").read_text(encoding="utf-8")
    assert f"Spotify does not know 1 track id in tracks.txt, so it gets no play count: `{fake_id(55)}`." in leaderboard
    assert "could not read" not in leaderboard
    latest = json.loads((repo / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["unknown_ids"] == [fake_id(55)] and latest["not_updated"] == []

    files = ["data/latest.json", "LEADERBOARD.md", "README.md"]
    before = {name: (repo / name).read_bytes() for name in files}
    code, output = render(repo)
    assert code == 0 and "No file changed." in output
    # a run of the same day that stops before reaching the id (in the second request) keeps it as unknown
    code, output = run(repo, "--date", "2026-11-01", "--max-requests", "1", session=FakeSession(FakeApi(universe())))
    assert code == 0 and "No file changed." in output
    assert {name: (repo / name).read_bytes() for name in files} == before


def test_a_wrong_address_stops_at_the_first_404(repo):
    session = FakeSession(status=404, error_body={"message": "Cannot GET /tracks"})
    env = {"SPOTIFY_API_KEY": KEY, "SPOTIFY_API_BASE": "https://spotify-proxy.checkleaked.cc"}
    code, output = run(repo, env=env, session=session)
    assert code == 1 and len(session.requests) == 1
    assert "Stopped early: the API has no GET /tracks at this address (HTTP 404" in output
    assert "Check SPOTIFY_API_BASE" in output and KEY not in output
    assert not (repo / "data").exists()


def test_refused_requests_stop_the_run_when_none_got_play_counts(tmp_path):
    (tmp_path / "tracks.txt").write_text("\n".join(fake_id(i) for i in range(1000)) + "\n", encoding="utf-8")
    session = FakeSession(status=400, error_body={"code": "INVALID_PARAMETER", "message": "include", "param": "include"})
    code, output = run(tmp_path, session=session)
    assert code == 1 and len(session.requests) == 2
    assert "Stopped early: the API refused 2 requests in a row and returned no play count" in output
    assert "No play counts were fetched with 2 request(s), so nothing was written." in output


def test_a_large_history_file_warns(repo, monkeypatch):
    monkeypatch.setattr(cli, "HISTORY_WARN_BYTES", 1_000)
    code, output = run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    assert code == 0
    assert "GitHub refuses files over 100 MB, so split the history into one file per year" in output


def test_titles_the_console_cannot_show_do_not_end_the_run(monkeypatch):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    cli._print("Pájaros -> 你好")
    stdout.flush()
    assert stdout.buffer.getvalue().decode("cp1252").splitlines() == ["Pájaros -> ??"]


def test_a_refused_key_fails_without_writing(repo):
    session = FakeSession(status=403, error_body={"message": "You are not subscribed to this API."})
    code, output = run(repo, "--retries", "0", session=session)
    assert code == 1
    assert "refused the key" in output and KEY not in output
    assert len(session.requests) == 1
    assert not (repo / "data" / "history.csv").exists()


def test_the_request_limit_comes_from_the_environment(repo):
    session = FakeSession(FakeApi(universe()))
    code, output = run(repo, env={"SPOTIFY_API_KEY": KEY, "TRACKER_MAX_REQUESTS": "1"}, session=session)
    assert code == 0
    assert len(session.requests) == 1
    assert "this run may send 1" in output and "limit of 1 requests" in output


def test_an_outage_spends_few_requests_and_writes_nothing(repo):
    session = FakeSession(status=503, error_body={"message": "Service unavailable"})
    code, output = run(repo, session=session)
    assert code == 1
    assert len(session.requests) == 3  # 2 in the first pass, 1 to check in the next
    assert "No play counts were fetched with 3 request(s), so nothing was written." in output
    assert not (repo / "data").exists()


def test_sdk_retries_are_counted_as_requests(repo, no_sdk_wait):
    session = FakeSession(status=503, error_body={"message": "Service unavailable"})
    code, output = run(repo, "--retries", "2", session=session)
    assert code == 1
    assert len(session.requests) == 9  # 3 requests, each sent 3 times
    assert "with 9 request(s)" in output


def test_sdk_retries_never_go_past_the_request_limit(repo, no_sdk_wait):
    session = FakeSession(status=503, error_body={"message": "Service unavailable"})
    env = {"SPOTIFY_API_KEY": KEY, "TRACKER_MAX_REQUESTS": "5"}
    code, output = run(repo, "--retries", "2", env=env, session=session)
    assert code == 1
    assert len(session.requests) == 5
    assert "Stopped early: the limit of 5 requests per run was reached." in output


def test_a_broken_history_stops_the_run_before_any_request(repo):
    (repo / "data").mkdir()
    (repo / "data" / "history.csv").write_text(
        f"date,track_id,name,artists,playcount\n2026-11-01,{BLINDING_LIGHTS},Blinding Lights,The Weeknd,lots\n",
        encoding="utf-8",
    )
    session = FakeSession(FakeApi(universe()))
    code, output = run(repo, session=session)
    assert code == 1 and session.requests == []
    assert "history.csv line 2: playcount is not a whole number" in output


@pytest.mark.parametrize(
    "env, args",
    [
        ({"SPOTIFY_API_BASE": "not a url"}, []),
        ({"TRACKER_MAX_REQUESTS": "many"}, []),
        ({"TRACKER_MAX_REQUESTS": "0"}, []),
        ({}, ["--time-limit", "0"]),
        ({}, ["--timeout", "-5"]),
        ({}, ["--top", "0"]),
        ({}, ["--date", "2026-13-01"]),
    ],
)
def test_bad_settings_fail_fast(repo, env, args):
    session = FakeSession(FakeApi(universe()))
    code, _ = run(repo, *args, env={"SPOTIFY_API_KEY": KEY, **env}, session=session)
    assert code == 1 and session.requests == []


def test_an_empty_list_fails(repo):
    (repo / "tracks.txt").write_text("# nothing here\n", encoding="utf-8")
    code, output = run(repo, session=FakeSession(FakeApi(universe())))
    assert code == 1 and "lists no tracks" in output


def render(repo, *args):
    lines = []
    code = main(["render", "--root", str(repo), *args], env={}, write=lines.append)
    return code, "\n".join(lines)


def test_render_rebuilds_the_same_files(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    api = FakeApi(universe(bump=10, step=3), relinked={fake_id(5): fake_id(500)})
    run(repo, "--date", "2026-11-02", session=FakeSession(api))
    leaderboard = repo / "LEADERBOARD.md"
    latest = repo / "data" / "latest.json"
    before = leaderboard.read_bytes(), latest.read_bytes()
    leaderboard.unlink()
    latest.unlink()
    code, output = render(repo)
    assert code == 0 and "Rendered 2026-11-02" in output
    assert (leaderboard.read_bytes(), latest.read_bytes()) == before


def test_latest_json_stays_on_the_latest_day(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    run(repo, "--date", "2026-11-02", session=FakeSession(FakeApi(universe(bump=500))))
    latest = repo / "data" / "latest.json"
    leaderboard = repo / "LEADERBOARD.md"
    before = latest.read_bytes()

    code, output = render(repo, "--date", "2026-11-01")
    assert code == 0 and "data/latest.json stays on 2026-11-02, the latest day." in output
    assert "**2026-11-01**" in leaderboard.read_text(encoding="utf-8")
    assert latest.read_bytes() == before

    code, output = render(repo)
    assert code == 0 and "Rendered 2026-11-02" in output
    assert "**2026-11-02**" in leaderboard.read_text(encoding="utf-8")
    assert latest.read_bytes() == before

    # Recording an earlier day keeps the files on the latest one, with the figures recomputed.
    code, output = run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe(bump=100))))
    assert code == 0 and "The files show 2026-11-02, the latest day in data/history.csv." in output
    document = json.loads(latest.read_text(encoding="utf-8"))
    assert document["date"] == "2026-11-02"
    assert document["songs"][0]["daily_streams"] == 400  # against the new 2026-11-01 value


def test_render_refuses_a_day_without_rows(repo):
    run(repo, "--date", "2026-11-01", session=FakeSession(FakeApi(universe())))
    code, output = render(repo, "--date", "2026-10-15")
    assert code == 1 and "data/history.csv has no rows for 2026-10-15." in output


def test_render_without_history_is_a_no_op(repo):
    code, output = render(repo)
    assert code == 0 and "nothing to render" in output


def test_check(repo):
    lines = []
    assert main(["check", "--root", str(repo)], env={}, write=lines.append) == 0
    assert "62 tracks, 2 request(s) per daily run" in lines[-1]
    with (repo / "tracks.txt").open("a", encoding="utf-8") as handle:
        handle.write("https://open.spotify.com/artist/1Xyo4u8uXC1ZmMpatF05PJ\n")
    lines.clear()
    assert main(["check", "--root", str(repo)], env={}, write=lines.append) == 1
    assert any("a Spotify artist link, not a track" in line for line in lines)


def test_check_warns_above_the_default_request_limit(tmp_path):
    (tmp_path / "tracks.txt").write_text("\n".join(fake_id(i) for i in range(1501)) + "\n", encoding="utf-8")
    lines = []
    assert main(["check", "--root", str(tmp_path)], env={}, write=lines.append) == 0
    assert "1501 tracks, 31 request(s) per daily run" in lines[0]
    assert "more than the default limit of 30 requests per run" in lines[1]
