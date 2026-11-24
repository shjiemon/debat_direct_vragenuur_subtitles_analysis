# Asylum and migration in the Vragenuur

This project collects Dutch subtitles from the Tweede Kamer’s weekly question
hour (*Vragenuur*) and counts words containing `asiel` or `migratie`. The
[Tweede Kamer’s activity records](https://opendata.tweedekamer.nl/documentatie/activiteit)
identify completed sessions. [Debat Direct](https://debatdirect.tweedekamer.nl/)
provides the Plenaire zaal recordings and subtitles.

## Setup

Run these commands from the repository folder.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install playwright nltk scikit-learn matplotlib
python -m playwright install chromium
python -m nltk.downloader stopwords
```

## Download subtitles

Review the [Tweede Kamer licence](https://www.tweedekamer.nl/applicaties/disclaimer).
Pass `--accept-license` if you agree to let the script accept it in the Debat
Direct download dialog.

```sh
python download_subtitles_vragenuur.py download --accept-license
```

The default date range comes from `START_DATE` and `END_DATE` at the top of
`download_subtitles_vragenuur.py`. The script creates `vragenuur_index.csv`
when it is missing. On later runs it checks the selected dates for new
sessions, reuses saved recording links, and retries unresolved links within
the selection. A run for a smaller range keeps existing index rows for
other dates.

Use `--start` and `--end` to override the defaults for one run. Both dates
are inclusive. Use `--date` on its own to select a single session.

```sh
python download_subtitles_vragenuur.py download --start 2026-01-01 --end 2026-11-23 --accept-license
python download_subtitles_vragenuur.py download --date 2025-04-08 --accept-license --headed
```

The downloader reuses files with valid SubRip timestamps and saves new
subtitles as UTF-8 `subtitles/YYYY-MM-DD.srt`. It tries the playback captions
when the SRT download is empty or unavailable. Check
`subtitles/download_log.csv` for the result of the most recent run. Some
recordings may have no usable captions.

## Build an index separately

The `index` command looks up recording links for the selected dates and
writes a fresh CSV. It records dates with unresolved or ambiguous links so
they can still be included in the coverage analysis.

```sh
python download_subtitles_vragenuur.py index
python download_subtitles_vragenuur.py index --start 2026-01-01 --end 2026-11-23 --index vragenuur_2026.csv
python download_subtitles_vragenuur.py download --index vragenuur_2026.csv --output subtitles_2026 --accept-license
```

An existing CSV passed explicitly with `--index` supplies the dates when
the download command has no date flags. This is useful for a curated list
of sessions.

## Analyse and plot

```sh
python analyse_vragenuur.py
```

The analysis parses the spoken text, strips cue numbers and timestamps,
removes Dutch stopwords, and builds a bag of words per debate. Substring
matching counts compounds such as `asielopvang` and `immigratie`. The CSVs in
`analysis/` contain raw counts, counts per 1,000 subtitle words, and the
matching word forms, with one file per debate and another aggregated by
month. The analysis uses the index to include sessions with missing
subtitles. Those rows have blank counts to distinguish them from observed
zeros.

The analysis reads every dated SRT in the subtitle folder. To analyse only
the separate 2026 collection in the example above, point it at that folder
and its matching index.

```sh
python analyse_vragenuur.py subtitles_2026 --index vragenuur_2026.csv --output-dir analysis_2026
```

If you have `plot_vragenuur.py` in your checkout, run it after the analysis.

```sh
python plot_vragenuur.py
```

It reads `analysis/vragenuur_by_debate.csv` and writes
`analysis/vragenuur_mentions.png`. Each bar sums the two raw counts for a
debate. Coloured dots show the individual counts, and red crosses mark
dates without subtitles.
