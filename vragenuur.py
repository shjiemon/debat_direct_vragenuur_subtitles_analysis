#!/usr/bin/env python3
"""Index Debat Direct recordings and collect their subtitle downloads."""

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import urlopen

DEBAT_DIRECT = "https://debatdirect.tweedekamer.nl"
ACTIVITIES = "https://gegevensmagazijn.tweedekamer.nl/OData/v5/Activiteit"
TERMS_URL = "https://www.tweedekamer.nl/applicaties/disclaimer"
TERMS_LABEL = "Ik accepteer de licentievoorwaarden van de Tweede Kamer"
INDEX_COLUMNS = ("date", "url", "status")
LOG_COLUMNS = ("date", "url", "status", "file", "detail")
SRT_CUE = re.compile(
    rb"(?m)^\d{2,}:\d{2}:\d{2},\d{3} --> \d{2,}:\d{2}:\d{2},\d{3}"
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


def build_index(page, args, timeout_error):
    sessions = completed_sessions(args.start, args.end)
    if not sessions:
        raise ValueError("The Tweede Kamer returned no completed Vragenuur sessions.")

    args.index.parent.mkdir(parents=True, exist_ok=True)
    incomplete = 0
    with args.index.open("w", encoding="utf-8", newline="") as output:
        index = csv.DictWriter(output, fieldnames=INDEX_COLUMNS)
        index.writeheader()
        for day in sessions:
            row = dict(date=day, url="", status="unresolved")
            try:
                links = recording_links(page, day, timeout_error)
                if len(links) == 1:
                    row.update(url=links[0], status="indexed")
                elif links:
                    row["status"] = "ambiguous"
                    print(f"{day}  multiple recordings: {', '.join(links)}", file=sys.stderr)
            except timeout_error as error:
                print(f"{day}  {error}", file=sys.stderr)
            incomplete += row["status"] != "indexed"
            index.writerow(row)
            output.flush()
            print(f"{day}  {row['status']}")

    print(f"Indexed {len(sessions) - incomplete} of {len(sessions)} sessions in {args.index}")
    return int(bool(incomplete))


def valid_srt(path):
    return path.is_file() and bool(SRT_CUE.search(path.read_bytes()))


def download_subtitle(page, row, target):
    if valid_srt(target):
        return "already_downloaded"
    url = urlsplit(row["url"])
    if (
        url.scheme != "https"
        or url.netloc != "debatdirect.tweedekamer.nl"
        or not url.path.startswith(f"/{row['date']}/")
        or "/plenaire-zaal/" not in url.path
    ):
        raise ValueError("Expected a Debat Direct Plenaire zaal URL for this date.")

    page.goto(row["url"], wait_until="domcontentloaded")
    heading = page.locator("#debate-metadata h1")
    if heading.inner_text(timeout=15_000).strip().casefold() != "vragenuur":
        raise ValueError("The recording page is no longer titled Vragenuur.")
    page.get_by_role("button", name="Download", exact=True).click()
    agreement = page.locator("#debate-agreement")
    agreement.wait_for(state="attached")
    if not agreement.is_checked():
        page.get_by_text(TERMS_LABEL, exact=True).click()

    subtitle_button = page.get_by_role(
        "button", name=re.compile(r"^download ondertiteling$", re.IGNORECASE)
    )
    with page.expect_download(timeout=45_000) as event:
        subtitle_button.click(timeout=10_000)

    partial = target.with_suffix(".srt.part")
    try:
        event.value.save_as(partial)
        if not valid_srt(partial):
            raise ValueError("The download did not contain SRT cue timestamps.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return "downloaded"


def download_index(page, args, playwright_error):
    with args.index.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    if args.date:
        rows = [row for row in rows if row["date"] == args.date.isoformat()]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        raise ValueError("The index contains no rows for this selection.")

    args.output.mkdir(parents=True, exist_ok=True)
    errors = 0
    with (args.output / "download_log.csv").open("w", encoding="utf-8", newline="") as output:
        log = csv.DictWriter(output, fieldnames=LOG_COLUMNS)
        log.writeheader()
        for row in rows:
            day = date.fromisoformat(row["date"]).isoformat()
            target = args.output / f"{day}.srt"
            entry = dict(date=day, url=row["url"], file="", status="error", detail="")
            try:
                if row["status"] != "indexed" or not row["url"]:
                    raise ValueError("This row has no verified recording URL.")
                entry.update(status=download_subtitle(page, row, target), file=target.name)
            except (OSError, ValueError, playwright_error) as error:
                errors += 1
                entry["detail"] = str(error).splitlines()[0]
            log.writerow(entry)
            output.flush()
            print(f"{day}  {entry['status']} {entry['detail']}".rstrip())

    print(f"{len(rows) - errors} of {len(rows)} subtitles available in {args.output}")
    return int(bool(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="rebuild the date and recording URL index")
    index.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    index.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    download = commands.add_parser("download", help="download subtitles from an index")
    download.add_argument("--output", type=Path, default=Path("subtitles"))
    download.add_argument("--date", type=date.fromisoformat)
    download.add_argument("--limit", type=int)
    download.add_argument(
        "--accept-license", action="store_true",
        help=f"accept the Tweede Kamer licence in each download dialog ({TERMS_URL})",
    )
    for command in (index, download):
        command.add_argument("--index", type=Path, default=Path("vragenuur_index.csv"))
        command.add_argument("--headed", action="store_true", help="show the Chromium window")
    args = parser.parse_args()

    if args.command == "index" and args.start > args.end:
        parser.error("--start must be on or before --end")
    if args.command == "download":
        if not args.accept_license:
            parser.error(f"Review the Tweede Kamer licence at {TERMS_URL} and pass --accept-license if you agree.")
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be at least 1")

    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError:
        parser.error("Install Playwright with python3 -m pip install playwright, then run python3 -m playwright install chromium.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            page = browser.new_page(accept_downloads=True)
            if args.command == "index":
                return build_index(page, args, TimeoutError)
            return download_index(page, args, Error)
    except (OSError, ValueError, Error) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
