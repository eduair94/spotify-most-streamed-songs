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
<!-- leaderboard:end -->

Daily streams are the difference between two days' play counts, so they start with the second
day of tracking: the first snapshot is from 2026-11-01, and the first daily figures come with the
update of 2026-11-02.

## What is in this repository

| Path | What it is |
| --- | --- |
| [`data/history.csv`](data/history.csv) | One row per song per day (UTC) since tracking started. New days are appended. |
| [`data/latest.json`](data/latest.json) | The latest day for every tracked song, with its daily and 7-day streams. |
| [`LEADERBOARD.md`](LEADERBOARD.md) | Top 100 by total streams, and the top 50 by daily streams with the change from the day before. |
| [`tracks.txt`](tracks.txt) | The tracked songs. Edit it in your fork. |
| [`tracker/`](tracker) | The Python code: fetch, merge into the history, render. |
| [`.github/workflows/daily.yml`](.github/workflows/daily.yml) | Runs the tracker once a day and commits the result. |

Once a day (scheduled for 07:47 UTC; GitHub sometimes starts it later) a GitHub Actions workflow
asks the API for the current play count of every song in `tracks.txt`, adds one row per song to
`data/history.csv` and regenerates the other files.

## Using the data

The files have stable URLs, so you can read them straight from GitHub:

```python
import pandas as pd

url = "https://raw.githubusercontent.com/eduair94/spotify-most-streamed-songs/main/data/history.csv"
history = pd.read_csv(url, parse_dates=["date"])

# Total streams of Blinding Lights over time, and the streams it gained each day
plays = history[history.track_id == "0VjIjW4GlUZAMYd2vXMi3b"].set_index("date")["playcount"].asfreq("D")
daily = plays.diff()  # NaN where a day is missing
```

```bash
curl -s https://raw.githubusercontent.com/eduair94/spotify-most-streamed-songs/main/data/latest.json \
  | jq '.songs[:5][] | {name, artists, playcount, daily_streams}'
```

`data/latest.json` lists every tracked song of the latest day by total streams (`position` 1 has
the most) with `playcount`, `daily_streams`, `daily_streams_change` and `daily_streams_change_pct`
(compared with the streams gained the day before) and `streams_7d`; a figure is `null` until there
is an earlier count to compare with. Songs the API could not read that day are in `not_updated`.

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
- When Spotify answers a track id with another one (track relinking), the count is recorded
  under the id in `tracks.txt`.
- `data/history.csv` grows by about 80 KB a day.

## Track your own songs

1. **Fork** this repository.
2. **Edit `tracks.txt`**: one song per line, as a Spotify track id, a `spotify:track:` URI or an
   `open.spotify.com/track/` link (`#` starts a comment). To start your own history from
   scratch, also delete `data/history.csv` and `data/latest.json`.
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
python -m tracker run                    # fetch today's play counts and update the files
python -m tracker render                 # rebuild the generated files from data/history.csv only
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
goes on only if that one gets counts, so an outage costs one request per 50 songs, plus one. Every
request counts towards the run's limit, and no new request starts after 20 minutes. Ids that
Spotify does not know are reported so you can remove them. The day's rows are then merged into
`data/history.csv`: running again on the same day replaces that day's rows instead of adding
duplicates. Finally `LEADERBOARD.md`, `data/latest.json` and the table at the top of this file are
regenerated.

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
once, with the id that has the most plays.

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
