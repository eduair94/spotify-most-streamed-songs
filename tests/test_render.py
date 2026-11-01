import json

from helpers import BLINDING_LIGHTS, SHAPE_OF_YOU, fake_id

from tracker.history import Entry, Row, entries_for_day
from tracker.render import (
    README_END,
    README_START,
    build_latest,
    dump_latest,
    escape_markdown,
    format_change,
    format_count,
    rank_by_daily,
    rank_by_streams,
    render_excerpt,
    render_leaderboard,
    replace_section,
)

DAY = "2026-11-01"


def entry(track_id, name, playcount, artists="Artist", daily=None, previous_daily=None, weekly=None):
    return Entry(Row(DAY, track_id, name, artists, playcount), daily, previous_daily, weekly)


def table_rows(markdown, heading=None):
    """The data rows of the first table (after ``heading`` when given)."""

    lines = markdown.splitlines()
    if heading:
        lines = lines[lines.index(heading) :]
    start = next(i for i, line in enumerate(lines) if line.startswith("|--"))
    rows = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return rows


def test_number_formatting():
    assert format_count(5659652654) == "5,659,652,654"
    assert format_count(-1200) == "-1,200"
    assert format_count(None) == "—"
    assert format_change(12345, 2.4119) == "+12,345 (+2.41%)"
    assert format_change(-2000, -0.5) == "-2,000 (-0.50%)"
    assert format_change(0, 0.0) == "0 (0.00%)"
    assert format_change(5, None) == "+5"
    assert format_change(None, None) == "—"


def test_markdown_escaping():
    assert escape_markdown("Psycho (feat. Ty Dolla $ign)") == r"Psycho (feat. Ty Dolla \$ign)"
    assert escape_markdown('What Was I Made For? [From "Barbie"]') == r'What Was I Made For? \[From "Barbie"\]'
    assert escape_markdown("a|b *c* _d_ <e>") == r"a\|b \*c\* \_d\_ \<e\>"
    assert escape_markdown("  two   spaces ") == "two spaces"


def test_ranking_by_total_streams_puts_missing_counts_last():
    ranked = rank_by_streams(
        [entry("a" * 22, "A", None), entry("b" * 22, "b", 5), entry("c" * 22, "C", 9), entry("d" * 22, "a", 5)]
    )
    assert [e.row.name for e in ranked] == ["C", "a", "b", "A"]


def test_ranking_by_daily_streams_leaves_out_songs_without_a_figure():
    ranked = rank_by_daily(
        [
            entry("a" * 22, "A", 100, daily=None),
            entry("b" * 22, "B", 200, daily=10),
            entry("c" * 22, "C", 300, daily=10),
            entry("d" * 22, "D", 50, daily=40),
            entry("e" * 22, "E", 60, daily=-5),
        ]
    )
    assert [e.row.name for e in ranked] == ["D", "C", "B", "E"]


def test_first_day_leaderboard():
    entries = [entry(fake_id(i), f"Song {i}", 1_000_000 + i, artists=f"Artist {i}; Guest") for i in range(150)]
    text = render_leaderboard(entries, day=DAY, tracked=150)
    rows = table_rows(text, "## Top 100 by total streams")
    assert len(rows) == 100
    assert rows[0] == (
        f"| 1 | [Song 149](https://open.spotify.com/track/{fake_id(149)}) | Artist 149, Guest | 1,000,149 | — | — |"
    )
    assert rows[-1].startswith("| 100 | [Song 50]")
    assert "on **2026-11-01** (UTC) of the 150 songs in" in text
    assert "## Top 50 by daily streams" in text
    assert "No daily streams for 2026-11-01 yet: they need the same songs' play counts from 2026-10-31" in text
    assert "Daily streams start with the second day of tracking." in text
    assert "have no play count for" not in text


def test_daily_table_with_changes_and_notes():
    entries = [
        entry(
            BLINDING_LIGHTS,
            "Blinding Lights",
            5_000_000_000,
            "The Weeknd",
            daily=3_000_000,
            previous_daily=2_500_000,
            weekly=20_000_000,
        ),
        entry(SHAPE_OF_YOU, "Shape of | You", 4_000_000_000, "Ed Sheeran", daily=4_000_000, previous_daily=None),
        entry(fake_id(1), "Flat", 3_000_000_000, daily=0, previous_daily=1_000),
        entry(fake_id(2), "Lowered", 2_000_000_000, daily=-500, previous_daily=-100),
        entry(fake_id(3), "New", 1_000_000_000),
    ]
    text = render_leaderboard(entries, day=DAY, tracked=6, not_updated=1, top=2, daily_top=3)
    top = table_rows(text, "## Top 2 by total streams")
    assert top == [
        f"| 1 | [Blinding Lights](https://open.spotify.com/track/{BLINDING_LIGHTS}) | The Weeknd "
        "| 5,000,000,000 | 3,000,000 | 20,000,000 |",
        rf"| 2 | [Shape of \| You](https://open.spotify.com/track/{SHAPE_OF_YOU}) | Ed Sheeran | 4,000,000,000 | 4,000,000 | — |",
    ]
    daily = table_rows(text, "## Top 3 by daily streams")
    assert len(daily) == 3
    assert daily[0].startswith(r"| 1 | [Shape of \| You]") and daily[0].endswith("| 4,000,000 | — | 4,000,000,000 |")
    assert daily[1].endswith("| 3,000,000 | +500,000 (+20.00%) | 5,000,000,000 |")
    assert daily[2].endswith("| 0 | -1,000 (-100.00%) | 3,000,000,000 |")
    assert "Streams gained from 2026-10-31 to 2026-11-01, among all 6 tracked songs." in text
    assert "1 song shows 0 daily streams, which usually means Spotify had not refreshed the play count" in text
    assert "1 song has a lower play count than the day before" in text
    assert "1 song has no daily figure: there is no play count from 2026-10-31" in text
    assert "1 tracked song has no play count for 2026-11-01" in text


def test_no_notes_when_every_song_has_a_positive_figure():
    text = render_leaderboard([entry(BLINDING_LIGHTS, "B", 10, daily=5)], day=DAY, tracked=1)
    assert "## Top 1 by daily streams" in text
    for phrase in ("0 daily streams", "lower play count", "no daily figure", "No daily streams"):
        assert phrase not in text


def test_latest_json():
    entries = [
        entry(SHAPE_OF_YOU, "Shape of You", 90, "Ed Sheeran", daily=5, previous_daily=3, weekly=30),
        entry(BLINDING_LIGHTS, "Blinding Lights", 100, "The Weeknd; Guest"),
    ]
    last = Row("2026-10-30", fake_id(1), "Gone Quiet", "Someone", 5)
    document = build_latest(entries, day=DAY, tracked=4, not_updated=[(fake_id(1), last), (fake_id(2), None)])
    text = dump_latest(document)
    parsed = json.loads(text)
    assert parsed == document
    assert text.endswith("}\n")
    assert parsed["date"] == DAY and parsed["tracked_songs"] == 4 and parsed["songs_with_data"] == 2
    first, second = parsed["songs"]
    assert (first["position"], first["id"], first["artists"], first["playcount"]) == (
        1,
        BLINDING_LIGHTS,
        ["The Weeknd", "Guest"],
        100,
    )
    assert (first["daily_streams"], first["daily_streams_change"], first["streams_7d"]) == (None, None, None)
    assert (second["daily_streams"], second["daily_streams_change"], second["daily_streams_change_pct"]) == (5, 2, 66.6667)
    assert second["streams_7d"] == 30
    assert second["url"] == f"https://open.spotify.com/track/{SHAPE_OF_YOU}"
    assert parsed["not_updated"] == [
        {"id": fake_id(1), "name": "Gone Quiet", "last_date": "2026-10-30"},
        {"id": fake_id(2), "name": None, "last_date": None},
    ]
    # one song per line, so a day's diff reads song by song
    assert sum(1 for line in text.splitlines() if line.lstrip().startswith('{"position"')) == 2


def test_latest_json_keeps_unicode_readable():
    text = dump_latest(build_latest([entry(BLINDING_LIGHTS, "Pájaros", 1, "ROSÉ")], day=DAY, tracked=1))
    assert "Pájaros" in text and "ROSÉ" in text


def test_readme_excerpt_between_markers():
    readme = f"# Title\n\nIntro\n\n{README_START}\nold table\n{README_END}\n\nMore text\n"
    excerpt = render_excerpt([entry(BLINDING_LIGHTS, "Blinding Lights", 100, "The Weeknd", daily=7)], day=DAY)
    updated = replace_section(readme, excerpt)
    assert "old table" not in updated
    assert updated.startswith(
        "# Title\n\nIntro\n\n" + README_START + "\nTop 1 of the tracked songs by total streams on **2026-11-01**"
    )
    assert updated.endswith(README_END + "\n\nMore text\n")
    assert f"| 1 | [Blinding Lights](https://open.spotify.com/track/{BLINDING_LIGHTS}) | The Weeknd | 100 | 7 |" in updated
    assert replace_section(updated, excerpt) == updated


def test_readme_without_markers_is_left_alone():
    assert replace_section("# Title\n", "table") == "# Title\n"
    assert replace_section(f"{README_END} before {README_START}", "table") == f"{README_END} before {README_START}"


def test_rows_to_leaderboard_end_to_end():
    rows = [
        Row("2026-10-30", BLINDING_LIGHTS, "Blinding Lights", "The Weeknd", 1_000),
        Row("2026-10-31", BLINDING_LIGHTS, "Blinding Lights", "The Weeknd", 1_100),
        Row(DAY, BLINDING_LIGHTS, "Blinding Lights", "The Weeknd", 1_250),
    ]
    text = render_leaderboard(entries_for_day(rows, DAY, [BLINDING_LIGHTS]), day=DAY, tracked=1)
    assert table_rows(text)[0].endswith("| The Weeknd | 1,250 | 150 | — |")
    assert table_rows(text, "## Top 1 by daily streams")[0].endswith("| 150 | +50 (+50.00%) | 1,250 |")
