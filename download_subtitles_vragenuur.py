#!/usr/bin/env python3
"""Index Debat Direct recordings and collect their subtitle downloads."""

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import urlopen

START_DATE = '2021-01-01'
END_DATE = '2026-11-23'

DEBAT_DIRECT = "https://debatdirect.tweedekamer.nl"
ACTIVITIES = "https://gegevensmagazijn.tweedekamer.nl/OData/v5/Activiteit"
TERMS_URL = "https://www.tweedekamer.nl/applicaties/disclaimer"
CAPTION_HOST = "livestreaming.b67v2.tweedekamer.nl"
DUTCH_CAPTION_TRACKS = ("nl-pol", "nl-nld")
# These debate bounds and track names were verified against the live archive.
VERIFIED_CAPTION_TRACKS = {
    "2023-02-14": ("nl-pol", "14:00:09", "15:07:01", "+01:00"),
    "2023-05-09": ("nl-nld", "14:00:45", "15:02:50", "+02:00"),
    "2025-04-08": ("nl-pol", "14:00:20", "15:06:55", "+02:00"),
    "2025-05-20": ("nl-pol", "14:04:45", "15:15:26", "+02:00"),
}
INDEX_COLUMNS = ("date", "url", "status")
DEFAULT_INDEX = Path("vragenuur_index.csv")
LOG_COLUMNS = ("date", "url", "status", "file", "detail")
SRT_CUE = re.compile(
    rb"(?m)^\d{2,}:\d{2}:\d{2},\d{3} --> \d{2,}:\d{2}:\d{2},\d{3}"
)
SUBTITLE_TIME = r"(?:[0-9]+:)?[0-5][0-9]:[0-5][0-9][,.][0-9]{3}"
SUBTITLE_CUE = re.compile(
    rf"^[ \t]*({SUBTITLE_TIME})[ \t]*-->[ \t]*({SUBTITLE_TIME})(?:[ \t]+.*)?$"
)


def completed_sessions(start, end):
    stop = end + timedelta(days=1)
    query = urlencode({
        "$filter": (
            "Soort eq 'Vragenuur' and Status eq 'Uitgevoerd' "
            f"and Datum ge {start}T00:00:00Z and Datum lt {stop}T00:00:00Z"
        ),
        "$select": "Datum",
    })
    next_url = f"{ACTIVITIES}?{query}"
    sessions = []
    while next_url:
        with urlopen(next_url, timeout=30) as response:
            data = json.load(response)
        for entry in data["value"]:
            day = date.fromisoformat(entry["Datum"][:10])
            if start <= day <= end:
                sessions.append(day.isoformat())
        next_link = data.get("@odata.nextLink")
        next_url = urljoin(next_url, next_link) if next_link else None
    return sorted(set(sessions))


def recording_links(page, day, timeout_error):
    query = urlencode({"van": day, "tot": day})
    page.goto(f"{DEBAT_DIRECT}/search?{query}", wait_until="domcontentloaded")
    cards = page.locator("#search-results article")
    links = cards.locator(f'a[href^="/{day}/"]')
    links.first.wait_for(state="attached", timeout=15_000)
    while True:
        paths = links.evaluate_all(
            """links => links.filter(link =>
                link.getAttribute('href').includes('/plenaire-zaal/') &&
                link.querySelector('h3')?.textContent.trim().toLowerCase() === 'vragenuur'
            ).map(link => link.getAttribute('href'))"""
        )
        if paths:
            return sorted({urljoin(DEBAT_DIRECT, path) for path in paths})

        # The search page fetches more results when its list reaches the bottom.
        card_count = cards.count()
        cards.last.locator("a").first.press("End")
        try:
            cards.nth(card_count).wait_for(state="attached", timeout=4_000)
        except timeout_error:
            return []


def resolve_recordings(page, sessions, timeout_error):
    rows = {}
    for day in sessions:
        row = dict(date=day, url="", status="unresolved")
        try:
            links = recording_links(page, day, timeout_error)
            if len(links) == 1:
                row.update(url=links[0], status="indexed")
            elif links:
                row["status"] = "ambiguous"
                print(f"{day}  multiple recordings: {', '.join(links)}",
                      file=sys.stderr)
        except timeout_error as error:
            print(f"{day}  {error}", file=sys.stderr)
        rows[day] = row
        print(f"{day}  {row['status']}")
    return rows


def read_index(path):
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not set(INDEX_COLUMNS).issubset(reader.fieldnames or ()):
                raise ValueError(
                    f"{path}: expected the columns {', '.join(INDEX_COLUMNS)}.")
            return {date.fromisoformat(row["date"]).isoformat(): row for row in reader}
    except FileNotFoundError:
        return {}


def write_index(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            writer = csv.DictWriter(
                output, fieldnames=INDEX_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows[day] for day in sorted(rows))
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def build_index(page, args, timeout_error):
    sessions = completed_sessions(args.start, args.end)
    if not sessions:
        raise ValueError(
            "The Tweede Kamer returned no completed Vragenuur sessions.")
    path = args.index or DEFAULT_INDEX
    rows = resolve_recordings(page, sessions, timeout_error)
    write_index(path, rows)
    incomplete = sum(row["status"] != "indexed" for row in rows.values())
    print(
        f"Indexed {len(sessions) - incomplete} of {len(sessions)} sessions in {path}")
    return int(bool(incomplete))


def subtitle_as_srt(data):
    if data.startswith((b"WEBVTT", b"\xef\xbb\xbfWEBVTT")):
        text = data.decode("utf-8-sig")
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    elif b"\0" in data[:256]:
        # Timestamps are ASCII, so NUL bytes reveal the byte order in UTF-16.
        sample = data[:256]
        encoding = "utf-16-le" if sample[1::2].count(
            0) > sample[::2].count(0) else "utf-16-be"
        text = data.decode(encoding)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    webvtt = text.startswith(("WEBVTT\n", "WEBVTT ", "WEBVTT\t"))
    cues = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.splitlines()
        if not lines:
            continue
        first_line = lines[0].strip()
        if webvtt and (
            first_line.startswith(("WEBVTT", "NOTE ", "NOTE\t"))
            or first_line in {"NOTE", "STYLE", "REGION"}
        ):
            continue
        # SubRip normally has a numeric cue ID. WebVTT allows arbitrary IDs.
        timing_index = 0
        times = SUBTITLE_CUE.fullmatch(lines[0])
        if not times and len(lines) >= 2 and (webvtt or first_line.isdecimal()):
            timing_index = 1
            times = SUBTITLE_CUE.fullmatch(lines[1])
        if not times or timing_index + 1 >= len(lines):
            continue

        timestamps = []
        for timestamp in times.groups():
            if timestamp.count(":") == 1:
                timestamp = f"00:{timestamp}"
            timestamps.append(timestamp.replace(".", ",").zfill(12))
        timing = " --> ".join(timestamps)
        cue_text = "\n".join(lines[timing_index + 1:])
        cues.append(f"{len(cues) + 1}\n{timing}\n{cue_text}")

    if not cues:
        raise ValueError("No SubRip or WebVTT cues found.")
    return ("\n\n".join(cues) + "\n").encode("utf-8")


def verified_caption_url(row):
    day = row["date"]
    expected = f"{DEBAT_DIRECT}/{day}/overig/plenaire-zaal/vragenuur-14-01"
    if row["url"] != expected or day not in VERIFIED_CAPTION_TRACKS:
        return None

    language, start, end, offset = VERIFIED_CAPTION_TRACKS[day]
    bounds = urlencode({
        "start": f"{day}T{start}.0000000{offset}",
        "end": f"{day}T{end}.0000000{offset}",
    })
    return f"https://{CAPTION_HOST}/{day}/plenairezaal/subtitles/{language}/vod.vtt?{bounds}"


def is_debate_caption_request(request, day):
    url = urlsplit(request.url)
    bounds = parse_qs(url.query)
    paths = {
        f"/{day}/plenairezaal/subtitles/{language}/vod.vtt"
        for language in DUTCH_CAPTION_TRACKS
    }
    return (
        url.scheme == "https"
        and url.hostname == CAPTION_HOST
        and url.path.casefold() in paths
        and all(bounds.get(key, [""])[0].startswith(f"{day}T") for key in ("start", "end"))
    )


def playback_caption_url(context, row):
    playback = context.new_page()
    try:
        with playback.expect_request(
            lambda request: is_debate_caption_request(request, row["date"]),
            timeout=35_000,
        ) as event:
            playback.goto(row["url"], wait_until="domcontentloaded")
            captions_off = playback.get_by_role(
                "button", name="Ondertiteling inschakelen", exact=True
            )
            captions_on = playback.get_by_role(
                "button", name="Ondertiteling uitschakelen", exact=True
            )
            captions_off.or_(captions_on).wait_for(
                state="visible", timeout=15_000)
            if captions_off.is_visible():
                captions_off.click()
            play_button = playback.locator(
                '[aria-label="Videospeler"] button[aria-labelledby="controlbar-play"]'
            )
            play_button.press("Enter", timeout=10_000)
        return event.value.url
    finally:
        playback.close()


def open_official_subtitle_download(page, row):
    page.goto(row["url"], wait_until="domcontentloaded")
    metadata = page.locator("#debate-metadata")
    # The heading can exist before Debat Direct has loaded the recording title.
    metadata.get_by_role("heading", name="Vragenuur", level=1, exact=True).wait_for(
        state="visible", timeout=20_000
    )
    metadata.get_by_role("button", name="Download", exact=True).click()
    page.locator("#debate-agreement").check(timeout=10_000)

    subtitle_button = page.get_by_role(
        "button", name=re.compile(r"^download ondertiteling$", re.IGNORECASE)
    )
    with page.expect_download(timeout=45_000) as event:
        subtitle_button.click(timeout=10_000)
    return event.value


def download_subtitle(page, row, target, timeout_error):
    if target.is_file() and SRT_CUE.search(target.read_bytes()):
        return "already_downloaded"
    url = urlsplit(row["url"])
    if (
        url.scheme != "https"
        or url.netloc != "debatdirect.tweedekamer.nl"
        or not url.path.startswith(f"/{row['date']}/")
        or "/plenaire-zaal/" not in url.path
    ):
        raise ValueError(
            "Expected a Debat Direct Plenaire zaal URL for this date.")

    caption_url = verified_caption_url(row)
    data = b""
    filename = "playback.vtt"
    result = "downloaded_from_playback"
    partial = target.with_suffix(".srt.part")
    try:
        if caption_url is None:
            try:
                download = open_official_subtitle_download(page, row)
            except timeout_error:
                print(
                    f"{row['date']}  SRT download unavailable. Trying the playback caption track.")
            else:
                download.save_as(partial)
                data = partial.read_bytes()
                if data:
                    filename = Path(download.suggested_filename).name
                    result = "downloaded"
                else:
                    print(
                        f"{row['date']}  Empty SRT download. Trying the playback caption track.")

        if not data:
            caption_url = caption_url or playback_caption_url(
                page.context, row)
            with urlopen(caption_url, timeout=30) as response:
                data = response.read()
            partial.write_bytes(data)

        try:
            srt = subtitle_as_srt(data)
        except ValueError as error:
            suffix = Path(filename).suffix
            if not re.fullmatch(r"\.[a-zA-Z0-9]{1,10}", suffix):
                suffix = ".bin"
            original = target.with_suffix(f".original{suffix}")
            partial.replace(original)
            raise ValueError(
                f"{error} Received {filename!r} ({len(data):,} bytes). "
                f"Original saved as {original}."
            ) from error
        partial.write_bytes(srt)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return result


def download_rows(page, args, timeout_error):
    path = args.index or DEFAULT_INDEX
    cached = read_index(path)
    if args.date:
        day = args.date.isoformat()
        sessions = [day] if day in cached else completed_sessions(
            args.date, args.date)
    elif args.index is not None and cached and args.start is None and args.end is None:
        # An explicit, existing index can specify a custom set of dates.
        sessions = sorted(cached)
    else:
        start = args.start or date.fromisoformat(START_DATE)
        end = args.end or date.fromisoformat(END_DATE)
        sessions = completed_sessions(start, end)
    if not sessions:
        raise ValueError(
            "The Tweede Kamer returned no completed Vragenuur sessions for this selection.")

    sessions = sessions[:args.limit]
    needs_url = [
        day for day in sessions
        if day not in cached or cached[day]["status"] != "indexed" or not cached[day]["url"]
    ]
    if needs_url:
        print(f"Resolving {len(needs_url)} recording links for {path}.")
        cached.update(resolve_recordings(page, needs_url, timeout_error))
        write_index(path, cached)
    return [cached[day] for day in sessions]


def download_index(page, args, playwright_error, timeout_error):
    rows = download_rows(page, args, timeout_error)
    args.output.mkdir(parents=True, exist_ok=True)
    errors = 0
    with (args.output / "download_log.csv").open("w", encoding="utf-8", newline="") as output:
        log = csv.DictWriter(output, fieldnames=LOG_COLUMNS)
        log.writeheader()
        for row in rows:
            day = date.fromisoformat(row["date"]).isoformat()
            target = args.output / f"{day}.srt"
            entry = dict(date=day, url=row["url"],
                         file="", status="error", detail="")
            try:
                if row["status"] != "indexed" or not row["url"]:
                    raise ValueError("This row has no verified recording URL.")
                entry.update(status=download_subtitle(
                    page, row, target, timeout_error), file=target.name)
            except (OSError, ValueError, playwright_error) as error:
                errors += 1
                entry["detail"] = str(error)
            log.writerow(entry)
            output.flush()
            summary = entry["detail"].partition("\n")[0]
            print(f"{day}  {entry['status']} {summary}".rstrip())

    print(f"{len(rows) - errors} of {len(rows)} subtitles available in {args.output}")
    return int(bool(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser(
        "index", help="rebuild the date and recording URL index")
    index.add_argument("--start", type=date.fromisoformat,
                       default=date.fromisoformat(START_DATE))
    index.add_argument("--end", type=date.fromisoformat,
                       default=date.fromisoformat(END_DATE))
    download = commands.add_parser(
        "download", help="update the index and download subtitles")
    download.add_argument("--start", type=date.fromisoformat,
                          help=f"first date to download (default: {START_DATE})")
    download.add_argument("--end", type=date.fromisoformat,
                          help=f"last date to download (default: {END_DATE})")
    download.add_argument("--output", type=Path, default=Path("subtitles"))
    download.add_argument("--date", type=date.fromisoformat)
    download.add_argument("--limit", type=int)
    download.add_argument(
        "--accept-license", action="store_true",
        help=f"accept the Tweede Kamer licence in each download dialog ({TERMS_URL})",
    )
    for command in (index, download):
        command.add_argument("--index", type=Path,
                             help=f"index CSV path (default: {DEFAULT_INDEX})")
        command.add_argument("--headed", action="store_true",
                             help="show the Chromium window")
    args = parser.parse_args()

    if args.command == "index" and args.start > args.end:
        parser.error("--start must be on or before --end")
    if args.command == "download":
        if args.date and (args.start is not None or args.end is not None):
            parser.error("--date cannot be combined with --start or --end")
        if (args.start or date.fromisoformat(START_DATE)) > (args.end or date.fromisoformat(END_DATE)):
            parser.error("--start must be on or before --end")
        if not args.accept_license:
            parser.error(
                f"Review the Tweede Kamer licence at {TERMS_URL} and pass --accept-license if you agree.")
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be at least 1")

    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError:
        parser.error(
            "Install Playwright with python3 -m pip install playwright, then run python3 -m playwright install chromium.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            context = browser.new_context(
                accept_downloads=True, locale="nl-NL")
            page = context.new_page()
            if args.command == "index":
                return build_index(page, args, TimeoutError)
            return download_index(page, args, Error, TimeoutError)
    except (OSError, ValueError, Error) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
