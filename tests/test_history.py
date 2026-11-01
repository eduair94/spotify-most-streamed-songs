import pytest
from helpers import BLINDING_LIGHTS, SHAPE_OF_YOU

from tracker.fetch import TrackPlays
from tracker.history import (
    Row,
    change,
    entries_for_day,
    format_history,
    last_rows,
    latest_day,
    read_history,
    rows_for_day,
    split_artists,
    streams_between,
    upsert,
    write_history,
)

HEADER = "date,track_id,name,artists,playcount"


def row(day, track_id, playcount, name="Song", artists="Artist"):
    return Row(day, track_id, name, artists, playcount)


# --- the CSV file -------------------------------------------------------------


def test_round_trip_with_quotes_commas_and_empty_values(tmp_path):
    path = tmp_path / "history.csv"
    rows = [
        row("2026-11-01", BLINDING_LIGHTS, 5659652654, "Blinding Lights", "The Weeknd"),
        row("2026-11-01", SHAPE_OF_YOU, None, 'Shape of "You", Live', "Tyler, The Creator; Ed Sheeran"),
    ]
    assert write_history(path, rows) is True
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == HEADER
    assert "\r" not in text
    assert f'2026-11-01,{SHAPE_OF_YOU},"Shape of ""You"", Live","Tyler, The Creator; Ed Sheeran",' in text
    assert read_history(path) == sorted(rows, key=lambda r: (r.date, r.track_id))


def test_missing_file_reads_as_empty(tmp_path):
    assert read_history(tmp_path / "nope.csv") == []


def test_reads_a_file_saved_with_bom_and_crlf(tmp_path):
    path = tmp_path / "history.csv"
    text = f"{HEADER}\r\n2026-11-01,{BLINDING_LIGHTS},Blinding Lights,The Weeknd,5\r\n"
    path.write_bytes(("\ufeff" + text).encode("utf-8"))
    assert read_history(path) == [row("2026-11-01", BLINDING_LIGHTS, 5, "Blinding Lights", "The Weeknd")]


def test_rejects_a_file_without_the_expected_columns(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text("date,track\n2026-11-01,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column"):
        read_history(path)


@pytest.mark.parametrize(
    "line, message",
    [
        (f"Nov 1 2026,{BLINDING_LIGHTS},S,A,5", "line 2: date is not YYYY-MM-DD"),
        (f"2026-11-01,{BLINDING_LIGHTS},S,A,lots", "line 2: playcount is not a whole number"),
        (f"2026-11-01,{BLINDING_LIGHTS},S,A,-5", "line 2: playcount is negative"),
        (f"2026-11-01,{BLINDING_LIGHTS},{'x' * 200_000},A,5", "line 2: field larger than field limit"),
    ],
    ids=["date", "number", "negative", "csv"],  # short ids: the long row would not fit in PYTEST_CURRENT_TEST on Windows
)
def test_rejects_rows_it_cannot_read(tmp_path, line, message):
    path = tmp_path / "history.csv"
    path.write_text(HEADER + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_history(path)


def test_rows_are_sorted_by_date_then_track():
    text = format_history([row("2026-11-02", "b" * 22, 1), row("2026-11-01", "b" * 22, 1), row("2026-11-02", "a" * 22, 1)])
    keys = [line.split(",")[:2] for line in text.splitlines()[1:]]
    assert keys == [["2026-11-01", "b" * 22], ["2026-11-02", "a" * 22], ["2026-11-02", "b" * 22]]


def test_artist_names_split_on_the_separator_only():
    assert split_artists("Tyler, The Creator; Kali Uchis") == ["Tyler, The Creator", "Kali Uchis"]
    assert split_artists("The Weeknd") == ["The Weeknd"]
    assert split_artists("") == []


# --- recording a day ----------------------------------------------------------


def test_recording_the_same_day_twice_changes_nothing(tmp_path):
    path = tmp_path / "history.csv"
    day = [row("2026-11-01", BLINDING_LIGHTS, 100), row("2026-11-01", SHAPE_OF_YOU, 90)]
    write_history(path, upsert(read_history(path), day))
    first = path.read_bytes()
    assert write_history(path, upsert(read_history(path), day)) is False
    assert path.read_bytes() == first
    assert len(read_history(path)) == 2


def test_a_rerun_replaces_that_day_and_keeps_the_rest():
    old = [row("2026-10-31", BLINDING_LIGHTS, 99), row("2026-11-01", BLINDING_LIGHTS, 100), row("2026-11-01", SHAPE_OF_YOU, 90)]
    merged = upsert(old, [row("2026-11-01", BLINDING_LIGHTS, 101)])
    assert merged == [
        row("2026-10-31", BLINDING_LIGHTS, 99),
        row("2026-11-01", BLINDING_LIGHTS, 101),
        row("2026-11-01", SHAPE_OF_YOU, 90),
    ]


def test_appending_a_new_day_only_adds_lines_at_the_end(tmp_path):
    path = tmp_path / "history.csv"
    write_history(path, [row("2026-11-01", BLINDING_LIGHTS, 100), row("2026-11-01", SHAPE_OF_YOU, 90)])
    before = path.read_text(encoding="utf-8")
    write_history(
        path, upsert(read_history(path), [row("2026-11-02", SHAPE_OF_YOU, 91), row("2026-11-02", BLINDING_LIGHTS, 101)])
    )
    after = path.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after[len(before) :].splitlines() == [
        f"2026-11-02,{BLINDING_LIGHTS},Song,Artist,101",
        f"2026-11-02,{SHAPE_OF_YOU},Song,Artist,91",
    ]


def test_rows_for_day_from_play_counts():
    plays = [TrackPlays(BLINDING_LIGHTS, "Blinding Lights", ["The Weeknd"], 100), TrackPlays(SHAPE_OF_YOU, "Duet", ["A", "B"], 0)]
    assert rows_for_day("2026-11-01", plays) == [
        Row("2026-11-01", BLINDING_LIGHTS, "Blinding Lights", "The Weeknd", 100),
        Row("2026-11-01", SHAPE_OF_YOU, "Duet", "A; B", 0),
    ]


# --- streams between days -----------------------------------------------------


@pytest.mark.parametrize(
    "current, earlier, expected",
    [
        (1_005_000, 1_000_000, 5_000),
        (1_000_000, 1_000_000, 0),  # not refreshed in between
        (999_000, 1_000_000, -1_000),  # Spotify lowered the count
        (1_500, 0, None),  # 0 = below 1,000 plays: the real count is unknown
        (0, 0, None),
        (None, 1_000, None),
        (1_000, None, None),
    ],
)
def test_streams_between(current, earlier, expected):
    assert streams_between(current, earlier) == expected


@pytest.mark.parametrize(
    "current, previous, expected",
    [
        (110, 100, (10, 10.0)),
        (90, 100, (-10, -10.0)),
        (100, 100, (0, 0.0)),
        (5, 0, (5, None)),
        (5, -10, (15, None)),  # no percentage of a negative day
        (None, 100, (None, None)),
        (100, None, (None, None)),
    ],
)
def test_change(current, previous, expected):
    assert change(current, previous) == expected


def test_daily_weekly_and_previous_daily_compare_exact_days():
    rows = [
        row("2026-10-25", BLINDING_LIGHTS, 1_000),
        row("2026-10-30", BLINDING_LIGHTS, 1_500),
        row("2026-10-31", BLINDING_LIGHTS, 1_600),
        row("2026-11-01", BLINDING_LIGHTS, 1_750),
        row("2026-10-30", SHAPE_OF_YOU, 500),  # two days earlier: not a daily figure
        row("2026-11-01", SHAPE_OF_YOU, 520),
    ]
    lights, shape = entries_for_day(rows, "2026-11-01")
    assert lights.row.track_id == BLINDING_LIGHTS
    assert (lights.daily, lights.previous_daily, lights.weekly) == (150, 100, 750)
    assert lights.daily_change == (50, pytest.approx(50.0))
    assert (shape.daily, shape.previous_daily, shape.weekly, shape.daily_change) == (None, None, None, (None, None))


def test_a_count_below_one_thousand_gives_no_figure():
    rows = [row("2026-10-31", BLINDING_LIGHTS, 0), row("2026-11-01", BLINDING_LIGHTS, 1_200)]
    (entry,) = entries_for_day(rows, "2026-11-01")
    assert entry.daily is None and entry.row.playcount == 1_200


def test_entries_follow_the_given_tracks_and_skip_those_without_a_row():
    rows = [row("2026-11-01", BLINDING_LIGHTS, 1), row("2026-11-01", SHAPE_OF_YOU, 2), row("2026-10-31", "c" * 22, 3)]
    entries = entries_for_day(rows, "2026-11-01", [SHAPE_OF_YOU, "c" * 22, BLINDING_LIGHTS])
    assert [entry.row.track_id for entry in entries] == [SHAPE_OF_YOU, BLINDING_LIGHTS]


def test_latest_day_and_last_rows():
    rows = [row("2026-10-31", BLINDING_LIGHTS, 1), row("2026-11-01", BLINDING_LIGHTS, 2), row("2026-11-02", SHAPE_OF_YOU, 3)]
    assert latest_day(rows) == "2026-11-02"
    assert latest_day(rows, [BLINDING_LIGHTS]) == "2026-11-01"
    assert latest_day([], [BLINDING_LIGHTS]) is None
    assert last_rows(rows)[BLINDING_LIGHTS].playcount == 2
