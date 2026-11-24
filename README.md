# Vragenuur subtitle collection

The supplied `vragenuur_index.csv` covers all 94 completed Vragenuur sessions
recorded by the Tweede Kamer's OData activity list from January 2023 through
December 2025. Each row links to the corresponding Plenaire zaal recording in
Debat Direct. The Tweede Kamer cancelled the Vragenuur on 3 June 2025.

## Setup

Run these commands from the extracted folder.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install playwright
python -m playwright install chromium
```

## Download subtitles

Debat Direct requires acceptance of the [Tweede Kamer licence terms](https://www.tweedekamer.nl/applicaties/disclaimer).
If you agree, use the flag below to let the script tick the site's licence
checkbox and click **Download ondertiteling**. The first command runs a
single-session check. The second downloads the remaining sessions.

```sh
python vragenuur.py download --date 2025-04-08 --accept-license --headed
python vragenuur.py download --accept-license
```

The script saves one `YYYY-MM-DD.srt` file per completed download in
`subtitles/`. It checks for SubRip timestamps before saving a file and skips
valid files on a rerun. The most recent run writes `download_log.csv` in the
same folder. A nonzero exit status indicates at least one failure.

## Rebuild the index

```sh
python vragenuur.py index --index rebuilt_index.csv
```

The index command reads the official [Activiteit records](https://opendata.tweedekamer.nl/documentatie/activiteit),
then searches Debat Direct by session date and checks the displayed title and
room. It records unresolved or ambiguous dates in the CSV. Use `--start` and
`--end` to change the date range.
