from helpers import BLINDING_LIGHTS, ROOT, SHAPE_OF_YOU

from tracker.tracks import parse_track, parse_tracks, read_tracks

ARTIST = "1Xyo4u8uXC1ZmMpatF05PJ"


def test_accepts_ids_uris_and_urls():
    track = BLINDING_LIGHTS
    assert parse_track(track) == track
    assert parse_track(f"  {track}  ") == track
    assert parse_track(f"spotify:track:{track}") == track
    assert parse_track(f"SPOTIFY:TRACK:{track}") == track
    assert parse_track(f"https://open.spotify.com/track/{track}") == track
    assert parse_track(f"https://open.spotify.com/track/{track}?si=Ab12Cd34") == track
    assert parse_track(f"https://open.spotify.com/intl-es/track/{track}") == track
    assert parse_track(f"http://open.spotify.com/track/{track}/") == track
    assert parse_track(f"open.spotify.com/track/{track}") == track


def test_rejects_anything_that_is_not_a_track():
    assert parse_track(f"https://open.spotify.com/artist/{ARTIST}") is None
    assert parse_track(f"spotify:album:{ARTIST}") is None
    assert parse_track(BLINDING_LIGHTS[:-1]) is None  # 21 characters
    assert parse_track(BLINDING_LIGHTS + "x") is None  # 23 characters
    assert parse_track("Blinding Lights") is None
    assert parse_track(f"https://example.com/track/{BLINDING_LIGHTS}") is None
    assert parse_track(f"https://open.spotify.com/track/{BLINDING_LIGHTS}x") is None
    assert parse_track("") is None


def test_comments_blank_lines_duplicates_and_errors():
    text = "\n".join(
        [
            "# a comment line",
            "",
            f"{BLINDING_LIGHTS}  # The Weeknd - Blinding Lights",
            "   ",
            f"spotify:track:{SHAPE_OF_YOU}",
            f"https://open.spotify.com/track/{BLINDING_LIGHTS}?si=x  # the same song again",
            "not a track",
            f"https://open.spotify.com/artist/{ARTIST}",
        ]
    )
    result = parse_tracks(text)
    assert result.ids == [BLINDING_LIGHTS, SHAPE_OF_YOU]
    assert [line for line, _ in result.warnings] == [6]
    assert "line 3" in result.warnings[0][1]
    assert [line for line, _ in result.errors] == [7, 8]
    assert result.errors[0][1] == "not a Spotify track id, URI or URL: not a track"


def test_links_to_albums_artists_and_playlists_get_a_hint():
    text = "\n".join(
        [
            f"https://open.spotify.com/artist/{ARTIST}",
            f"https://open.spotify.com/intl-de/album/{ARTIST}?si=1",
            f"spotify:playlist:{ARTIST}",
        ]
    )
    messages = [message for _, message in parse_tracks(text).errors]
    assert messages[0].startswith("a Spotify artist link, not a track: ")
    assert messages[1].startswith("a Spotify album link, not a track: ")
    assert messages[2].startswith("a Spotify playlist link, not a track: ")
    assert all(message.endswith("(copy the link of a song instead)") for message in messages)


def test_long_entries_are_shortened_in_messages():
    message = parse_tracks("x" * 200).errors[0][1]
    assert message.endswith("x" * 74 + "...") and len(message) < 130


def test_order_is_kept():
    ids = [SHAPE_OF_YOU, BLINDING_LIGHTS]
    assert parse_tracks("\n".join(ids)).ids == ids


def test_reads_utf8_with_bom_and_crlf(tmp_path):
    path = tmp_path / "tracks.txt"
    path.write_bytes(("\ufeff# list\r\n" + BLINDING_LIGHTS + "  # Blinding Lights\r\n" + SHAPE_OF_YOU + "\r\n").encode("utf-8"))
    assert read_tracks(path).ids == [BLINDING_LIGHTS, SHAPE_OF_YOU]


def test_repository_track_list_reads_without_errors():
    # The same rule as `python -m tracker check`: a fork can track any number of songs,
    # and a repeated song is only a warning.
    result = read_tracks(ROOT / "tracks.txt")
    assert result.errors == []
    assert result.ids
