# Most-streamed songs on Spotify: daily play counts

[![Daily update](https://github.com/eduair94/spotify-most-streamed-songs/actions/workflows/daily.yml/badge.svg)](https://github.com/eduair94/spotify-most-streamed-songs/actions/workflows/daily.yml)
[![Tests](https://github.com/eduair94/spotify-most-streamed-songs/actions/workflows/ci.yml/badge.svg)](https://github.com/eduair94/spotify-most-streamed-songs/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A daily record of the Spotify **play count** (total streams since release) of 1,000 of the
most-streamed songs, and of the **daily streams** that follow from it, kept as plain CSV and JSON
in this repository. It is also a small tracker you can fork to record the same history for your
own songs.

Unofficial: not affiliated with, endorsed by or sponsored by Spotify.

## Top 10 today

<!-- leaderboard:start -->
Top 10 of the tracked songs by total streams on **2026-11-11** (UTC). Top 100, and the top 50 by daily streams: [LEADERBOARD.md](LEADERBOARD.md).

| # | Song | Artist | Total streams | Daily streams |
|--:|:--|:--|--:|--:|
| 1 | [Blinding Lights](https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b) | The Weeknd | 5,676,262,011 | 1,476,363 |
| 2 | [Shape of You](https://open.spotify.com/track/7qiZfU4dY1lWllzX7mPBI3) | Ed Sheeran | 5,183,026,481 | 1,638,332 |
| 3 | [Sweater Weather](https://open.spotify.com/track/0cQVqPuHQP4KEwc7ZUQmj6) | The Neighbourhood | 5,021,571,319 | 2,530,280 |
| 4 | [Starboy](https://open.spotify.com/track/7MXVkk9YMctZqd1Srtv4MB) | The Weeknd, Daft Punk | 4,860,631,177 | 1,905,627 |
| 5 | [As It Was](https://open.spotify.com/track/4Dvkj6JhhA12EX05fT7y2e) | Harry Styles | 4,709,763,636 | 1,389,336 |
| 6 | [One Dance](https://open.spotify.com/track/1zi7xx7UVEFkmKfv06H8x0) | Drake, Wizkid, Kyla | 4,560,007,941 | 1,595,269 |
| 7 | [Sunflower - Spider-Man: Into the Spider-Verse](https://open.spotify.com/track/0RiRZpuVRbi7oqRdSMwhQY) | Post Malone, Swae Lee | 4,552,431,365 | 1,364,717 |
| 8 | [Perfect](https://open.spotify.com/track/0tgVpDi06FyKpA1z0VMD4v) | Ed Sheeran | 4,180,789,848 | 1,229,415 |
| 9 | [Die With A Smile](https://open.spotify.com/track/7so0lgd0zP2Sbgs2d7a1SZ) | Lady Gaga, Bruno Mars | 4,135,193,002 | 2,467,800 |
| 10 | [STAY (with Justin Bieber)](https://open.spotify.com/track/567e29TDzLwZwfDuEpGTwo) | The Kid LAROI, Justin Bieber | 4,133,576,159 | 1,053,535 |
<!-- leaderboard:end -->

Daily streams are the difference between the play counts of two consecutive days, so they start
with the second day of tracking: the first snapshot is from 2026-11-01, read at about 10:03 UTC,
and the daily run is scheduled for that time of day. The first figures can still be irregular,
since Spotify refreshes the counts on a schedule of its own (see "Good to know" below).

## What is in this repository

| Path | What it is |
| --- | --- |
| [`data/history.csv`](data/history.csv) | One row per song per day (UTC) since tracking started. New days are appended. |
| [`data/latest.json`](data/latest.json) | The latest day for every tracked song, with its daily and 7-day streams. |
| [`data/relinks.csv`](data/relinks.csv) | The days on which Spotify answered a listed track id with another release of the song (see "Good to know"). Only a header until that happens. |
| [`LEADERBOARD.md`](LEADERBOARD.md) | Top 100 by total streams, and the top 50 by daily streams with the change from the day before. |
| [`tracks.txt`](tracks.txt) | The tracked songs. Edit it in your fork. |
| [`tracker/`](tracker) | The Python code: fetch, merge into the history, render. |
| [`.github/workflows/daily.yml`](.github/workflows/daily.yml) | Runs the tracker once a day and commits the result. |

Once a day (scheduled for 10:07 UTC; GitHub sometimes starts it later) a GitHub Actions workflow
asks the API for the current play count of every song in `tracks.txt`, adds one row per song to
`data/history.csv` and regenerates the other files.

## Using the data

The files have stable URLs, so you can read them straight from GitHub:

```python
import pandas as pd

url = "https://raw.githubusercontent.com/eduair94/spotify-most-streamed-songs/main/data/history.csv"
history = pd.read_csv(url, parse_dates=["date"])

# Total streams of Blinding Lights over time, and the change of its count each day
plays = history[history.track_id == "0VjIjW4GlUZAMYd2vXMi3b"].set_index("date")["playcount"].asfreq("D")
daily = plays.diff()  # NaN where a day is missing
```

`plays.diff()` is the plain difference. The daily figures in `LEADERBOARD.md` and
`data/latest.json` leave out a few days that it keeps: see "Good to know" below.

```bash
curl -s https://raw.githubusercontent.com/eduair94/spotify-most-streamed-songs/main/data/latest.json \
  | jq '.songs[:5][] | {name, artists, playcount, daily_streams}'
```

`data/latest.json` lists every tracked song of the latest day by total streams (`position` 1 has
the most) with `playcount`, `daily_streams`, `daily_streams_change` and `daily_streams_change_pct`
(compared with the streams gained the day before) and `streams_7d`. A figure is `null` when there
is no earlier count it can be compared with, and `no_daily_reason` says why `daily_streams` is:
`no_earlier_count` (no count from the day before), `below_1000` (a count of 0, which is what
Spotify shows below 1,000 plays), `version_changed` (another release of the song answered than the
day before) or `implausible_jump` (a change that streams do not explain). `answered_id` is the
track whose count was read: the same as `id` unless Spotify relinked it. Songs the API could not
read that day are in `not_updated`, and ids Spotify does not know are in `unknown_ids`.

### Columns of history.csv

| Column | Meaning |
| --- | --- |
| `date` | The UTC day the play count was read (`YYYY-MM-DD`). |
| `track_id` | Spotify track id, as listed in `tracks.txt`: the song page is `https://open.spotify.com/track/<track_id>`. |
| `name` | The song title on that day, as Spotify lists it. |
| `artists` | The credited artists, separated by `; ` (a name can contain a comma, as in "Tyler, The Creator"). |
| `playcount` | Lifetime plays, as Spotify shows them. Spotify shows 0 until a song passes 1,000 plays. |

Good to know:

- Daily streams are not a column of `history.csv`: they are a song's play count minus its count
  one day earlier, computed from the history for `LEADERBOARD.md` and `data/latest.json` (and by
  the pandas example). 7-day streams are the same over seven days.
- Spotify refreshes play counts roughly once a day, not at the moment this tracker reads them.
  If a count was not refreshed between two runs, that day shows 0 daily streams and the next
  day covers two days. The 7-day figure is less sensitive to this.
- Spotify occasionally lowers a play count, so a daily figure can be negative. No daily figure
  is computed from a count of 0, since the real count is then anywhere below 1,000.
- If a song has no row for a day, the API could not read its count in that run. Nothing is
  estimated or carried forward.
- Spotify can answer a track id with another release of the same song (track relinking), and two
  releases of one song can differ by billions of plays. The count read is then that release's: it
  is recorded under the id in `tracks.txt`, the release that answered goes to `data/relinks.csv`
  (and to `answered_id` in `data/latest.json`), and the run's log warns about it. Counts of two
  different releases are never compared: a day on which another release answered than the day
  before has no daily figure, and a week with different releases at its two ends has no 7-day
  figure.
- A count that moves by more than 5% of the song's play count in one day, and by more than
  1,000,000 plays, is not taken as streams: most likely Spotify replaced or corrected the count,
  or switched releases without saying so. That day has no daily figure, the weeks that include it
  have no 7-day figure, and `LEADERBOARD.md` names the song with the change. Every song in this
  list had 577.6 million plays or more when it was picked, so 5% is at least 28.9 million plays in
  a day. In a fork that tracks new releases, a hit can grow faster than that in its first weeks:
  raise `JUMP_SHARE` in `tracker/history.py` if it does.
- `data/history.csv` grows by about 80 KB a day, about 29 MB a year. GitHub refuses files over
  100 MB, which this file would reach in about three and a half years, so before then the history
  will be split into one file per year. From 50 MB on, every run warns about it.

## Track your own songs

1. **Fork** this repository.
2. **Edit `tracks.txt`**: one song per line, as a Spotify track id, a `spotify:track:` URI or an
   `open.spotify.com/track/` link (`#` starts a comment). To start your own history from
   scratch, also delete `data/history.csv`, `data/relinks.csv` and `data/latest.json`.
3. **Get an API key** (see below).
4. **Add the key as a repository secret**: Settings → Secrets and variables → Actions → New
   repository secret, named `SPOTIFY_API_KEY`. With a direct key (not RapidAPI), also add a secret
   `SPOTIFY_API_BASE` with the value `https://spotify-proxy.checkleaked.cc/spotify-data`.
5. **Enable the workflows**: GitHub turns scheduled workflows off in new forks. Open the Actions
   tab, enable them, then start **Daily update** once by hand with "Run workflow".

The workflow then commits new play counts once a day. Without the secret it skips the update and
says why in its log, so a fork without a key does not fail.

Optional: a repository *variable* `TRACKER_MAX_REQUESTS` (Settings → Secrets and variables →
Actions → Variables) sets the most requests one run may send, retries included. The default is 30.

### Getting an API key

The tracker uses the [Spotify Data API](https://spotify.checkleaked.cc), which reads the play
counts Spotify shows on its public pages. You need a key from one of these:

- **RapidAPI** ([spotify81 on RapidAPI](https://rapidapi.com/airaudoeduardo/api/spotify81)). The
  free Basic plan allows 1,000 requests a month, as a hard limit: requests past it are refused,
  not charged. RapidAPI asks for a payment card even to subscribe to the free plan, because the
  API also has paid plans ([RapidAPI's pricing rules](https://docs.rapidapi.com/docs/api-pricing)).
- **A direct key** from [spotify.checkleaked.cc/pricing](https://spotify.checkleaked.cc/pricing).
  Set `SPOTIFY_API_BASE` as described above.

One request returns up to 50 songs. A run sends at most 30 requests by default, retries included,
so one run a day stays at or under 930 requests in a 31-day month: inside the free plan, with room
for an occasional run started by hand. 30 requests cover 1,500 songs when every answer is
complete; keep your list to about 1,400 songs so that each run has two requests left for asking
again about songs whose counts were not ready.

| Songs in `tracks.txt` | Requests a day | Requests a month (31 days) |
| --: | --: | --: |
| 50 | 1 | 31 |
| 500 | 10 | 310 |
| 1,000 (this list) | 20 | 620 |
| 1,400 | 28 | 868 |
| 1,500 | 30 | 930 |

The table counts one request per 50 songs. Asking again takes one more request per 50 songs
whose counts were not ready. `python -m tracker check` prints the count for your list.

## Run it on your computer

```bash
python -m pip install -r requirements.txt
export SPOTIFY_API_KEY=your-key          # PowerShell: $env:SPOTIFY_API_KEY = "your-key"
# Only with a direct key (not RapidAPI):
export SPOTIFY_API_BASE=https://spotify-proxy.checkleaked.cc/spotify-data
python -m tracker run                    # fetch today's play counts and update the files
python -m tracker render                 # rebuild the generated files from the data files only
python -m tracker check                  # validate tracks.txt

python -m pip install -r requirements-dev.txt
python -m pytest                         # offline tests, no key needed
```

`python -m tracker run --help` lists the options (request limit, retry passes, SDK retries,
timeouts, date, table sizes). Python 3.9 or newer.

## How it works

For each group of 50 songs the tracker sends one request,
`GET /tracks?ids=<ids>&include=playcount`, through the
[checkleaked-spotify-api](https://pypi.org/project/checkleaked-spotify-api/) Python SDK. The
answer has each song's title, artists and lifetime play count. Songs whose count was not ready in
time, and those of a request that failed, are asked for again in up to two more passes. If a whole
pass gets no counts at all, for example while the API is down, the next pass sends one request and
goes on only if that one gets counts, so an outage costs one request per 50 songs, plus one. A
404 ends the run at once, and so do two refused requests in a row before any count came back: both
mean that the address in `SPOTIFY_API_BASE` or another setting is wrong, and every other request
would fail the same way. Every request counts towards the run's limit, and no new request starts
after 20 minutes. Ids that Spotify does not know are reported so you can remove them, and songs
answered by another release are logged. The day's rows are then merged into `data/history.csv`
(and the relinked ids into `data/relinks.csv`): running again on the same day replaces that day's
rows instead of adding duplicates. Finally `LEADERBOARD.md`, `data/latest.json` and the table at
the top of this file are regenerated.

You can get the same numbers for any song from the API directly. The
[Spotify play count API guide](https://spotify.checkleaked.cc/spotify-play-count-api) has the
details; with the SDK:

```python
from spotify_api_sdk import SpotifyApiClient

client = SpotifyApiClient(api_key="your-rapidapi-key")
body = client.tracks.get(["0VjIjW4GlUZAMYd2vXMi3b", "7qiZfU4dY1lWllzX7mPBI3"], include=["playcount"])
for track in body["tracks"]:
    # playcount is null when the count was not ready in time
    print(track["name"], track.get("playcount"))
```

## How the 1,000 songs were picked

On 2026-11-01, from two sources read through the API:

- the 4,139 songs of the weekly Spotify Top 200 charts dated 2026-10-29 for the world and 30
  countries (United States, Canada, Mexico, Brazil, Argentina, Colombia, Chile, Peru, United
  Kingdom, Germany, France, Spain, Italy, Netherlands, Sweden, Poland, Portugal, Turkey, Japan,
  South Korea, India, Indonesia, Philippines, Thailand, Vietnam, Taiwan, Malaysia, South Africa,
  Nigeria and Australia), each with its play count;
- the top tracks (up to ten each, 4,405 songs) of the 500 artists followed by
  [spotify-monthly-listeners](https://github.com/eduair94/spotify-monthly-listeners). Within the
  request budget, play counts were read for the top tracks of the 155 artists with the most
  monthly listeners (29.3 million or more); for the other 345 artists, only their charting songs
  were candidates.

The 1,000 songs with the most plays were kept: 577.6 million plays and up on that day, 632 of them
above one billion. A song listed under several track ids with the same title and artists counts
once, with the id that has the most plays. When Spotify answered a looked-up top track with
another release of the song (track relinking: 119 of the 899 top tracks looked up), the id of the
release that answered was kept, since that is the one the API serves; 86 of the 1,000 listed ids
are such ids. The first snapshot had no relinked song.

So this is not the complete top 1,000 of Spotify: a song that was in none of those charts and is
not a top track of those 155 artists is missing however many plays it has. The leaderboard ranks
the tracked songs only. The list is fixed: a song that passes the cutoff later is not added by
itself.

## Links

- [Website and live demo](https://spotify.checkleaked.cc)
- [Spotify play count API guide](https://spotify.checkleaked.cc/spotify-play-count-api)
- [Pricing for direct keys](https://spotify.checkleaked.cc/pricing)
- [spotify81 on RapidAPI](https://rapidapi.com/airaudoeduardo/api/spotify81)
- [Python SDK on PyPI](https://pypi.org/project/checkleaked-spotify-api/): `pip install checkleaked-spotify-api`
- [Spotify monthly listeners tracker](https://github.com/eduair94/spotify-monthly-listeners): the
  same kind of daily record for the monthly listeners, followers and world rank of 500 artists

## License and data

The code is MIT licensed; see [LICENSE](LICENSE). The data is the play counts Spotify shows on its
public pages, collected through the API, and is provided as is, without any guarantee of accuracy
or completeness; see [DATA_NOTICE.md](DATA_NOTICE.md). This project is unofficial and is not
affiliated with, endorsed by or sponsored by Spotify. Spotify is a trademark of Spotify AB.
