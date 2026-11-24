#!/usr/bin/env python3
"""Download official Vragenuur stenograms from the Tweede Kamer.

Edit START_DATE and END_DATE below, or override either date with --start
or --end (inclusive, YYYY-MM-DD). Each date gets a UTF-8 text file of the
speaker and interruption paragraphs. Each turn starts with a
[[SPEAKER: name]] line using the label from the XML. The original
full-sitting XML is retained by default. Use --skip-xml to save only the
text and CSV index.
The index lists the reports used, their publication status and any gaps.
Requires only the Python standard library.
"""

import argparse
from collections import defaultdict
import csv
from datetime import date, timedelta
import io
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen
from xml.etree import ElementTree as ET

START_DATE = "2021-01-01"
END_DATE = "2026-11-23"

ODATA = "https://gegevensmagazijn.tweedekamer.nl/OData/v5/"
PUBLICATION_PRIORITY = {"Ongecorrigeerd": 0,
                        "Gecorrigeerd": 1, "Gerectificeerd": 2}
SPEECH_TAGS = {"woordvoerder", "interrumpant"}
INDEX_FIELDS = ("date", "status", "activity_numbers", "sections", "text_file",
                "report_statuses", "report_urls", "detail")


def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(url, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if attempt == 2 or error.code not in (408, 429, 500, 502, 503, 504):
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        sleep(2 ** attempt)


def records(entity, **params):
    url = ODATA + entity + "?" + urlencode(params)
    while url:
        page = json.loads(fetch(url))
        yield from page["value"]
        next_link = page.get("@odata.nextLink")
        url = urljoin(url, next_link) if next_link else None


def find_sittings(start, end):
    # Give the UTC query an extra day at the start, then use the Dutch
    # calendar date recorded in each row for the inclusive range.
    window = (f"Datum ge {start - timedelta(days=1)}T00:00:00Z "
              f"and Datum lt {end + timedelta(days=1)}T00:00:00Z")
    activities = defaultdict(list)
    for activity in records("Activiteit", **{
        "$filter": f"Soort eq 'Vragenuur' and Status eq 'Uitgevoerd' and {window}",
        "$select": "Datum,Nummer",
    }):
        day = date.fromisoformat(activity["Datum"][:10])
        if start <= day <= end:
            activities[day].append(activity["Nummer"])

    sittings = defaultdict(list)
    if activities:
        for sitting in records("Vergadering", **{
            "$filter": f"Soort eq 'Plenair' and Kamer eq 'Tweede Kamer' and {window}",
            "$select": "Datum,Aanvangstijd,Titel",
            "$expand": "Verslag($select=Id,Soort,Status,Verwijderd,ContentLength,GewijzigdOp)",
            "$orderby": "Datum,Aanvangstijd",
        }):
            day = date.fromisoformat(sitting["Datum"][:10])
            if day in activities:
                sittings[day].append(sitting)
    return activities, sittings


def preferred_report(sitting):
    published = (
        report for report in sitting["Verslag"]
        if not report["Verwijderd"] and report["ContentLength"]
        and report["Status"] in PUBLICATION_PRIORITY
    )
    return max(published, default=None, key=lambda report: (
        PUBLICATION_PRIORITY[report["Status"]],
        report["Soort"] == "Eindpublicatie",
        report["GewijzigdOp"] or "",
    ))


def format_speech(turn, text_node):
    paragraphs = list(text_node.iterfind(".//{*}alinea"))
    if not paragraphs:
        return ""

    # VLOS puts the printed name in its own inline item, before the first words.
    lead = paragraphs[0].find("{*}alineaitem")
    heading = ""
    if lead is not None and lead.find('.//{*}nadruk[@type="Vet"]') is not None:
        heading = " ".join("".join(lead.itertext()).split())
    texts = [" ".join("".join(paragraph.itertext()).split())
             for paragraph in paragraphs]
    if heading.endswith(":") and texts[0].startswith(heading):
        speaker = heading[:-1].rstrip()
        texts[0] = texts[0][len(heading):].lstrip()
    else:
        speaker = turn.findtext("{*}spreker/{*}verslagnaam", "")
        speaker = " ".join(speaker.split()) or "Unknown speaker"

    speech = "\n\n".join(text for text in texts if text)
    return f"[[SPEAKER: {speaker}]]\n{speech}" if speech else ""


def speech_blocks(node):
    for child in node:
        if child.tag.rsplit("}", 1)[-1] == "tekst":
            if node.tag.rsplit("}", 1)[-1] in SPEECH_TAGS:
                block = format_speech(node, child)
                if block:
                    yield block
        else:
            yield from speech_blocks(child)


def extract_sections(xml, day):
    root = ET.fromstring(xml)
    meeting = root.find("{*}vergadering")
    if meeting is None or meeting.findtext("{*}datum", "")[:10] != day.isoformat():
        raise ValueError(
            "The XML does not describe the expected sitting date.")

    sections = []
    for activity in meeting.findall("{*}activiteit"):
        if activity.get("soort", "").strip().casefold() == "vragenuur":
            blocks = list(speech_blocks(activity))
            if blocks:
                sections.append("\n\n".join(blocks))
    return sections


def write_file(path, content):
    with NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def download_day(day, activity_numbers, sittings, output, keep_xml):
    sections = []
    report_statuses = []
    report_urls = []
    errors = []
    for sitting in sittings:
        report = preferred_report(sitting)
        if report is None:
            continue

        url = f"{ODATA}Verslag/{report['Id']}/resource"
        try:
            xml = fetch(url)
            extracted = extract_sections(xml, day)
            if not extracted:
                continue
            if keep_xml:
                write_file(output / "xml" / f"{day}_{report['Id']}.xml", xml)
        except (OSError, ValueError, ET.ParseError) as error:
            errors.append(f"{url}: {error}")
            continue

        sections.extend(extracted)
        report_statuses.append(report["Status"])
        report_urls.append(url)

    filename = ""
    if sections:
        filename = f"{day}.txt"
        write_file(output / filename,
                   ("\n\n".join(sections) + "\n").encode("utf-8"))
        status = "partial" if errors or len(sections) < len(
            activity_numbers) else "downloaded"
        if len(sections) < len(activity_numbers):
            errors.append(
                f"Found {len(sections)} sections for {len(activity_numbers)} indexed activities.")
    else:
        status = "missing"
        errors.append(
            "No published Vragenuur section with speaker text was found.")

    return dict(
        date=day, status=status, activity_numbers="|".join(
            sorted(activity_numbers)),
        sections=len(sections), text_file=filename, report_statuses="|".join(report_statuses),
        report_urls="|".join(report_urls), detail=" | ".join(errors),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, default=START_DATE,
                        help=f"first date, YYYY-MM-DD (default: {START_DATE})")
    parser.add_argument("--end", type=date.fromisoformat, default=END_DATE,
                        help=f"last date, YYYY-MM-DD (default: {END_DATE})")
    parser.add_argument("--output", type=Path,
                        default=Path("stenograms"), help="output folder")
    xml_options = parser.add_mutually_exclusive_group()
    xml_options.add_argument("--skip-xml", dest="keep_xml", action="store_false",
                             help="skip saving the original full-sitting XML")
    xml_options.add_argument(
        "--keep-xml", action="store_true", help=argparse.SUPPRESS)
    parser.set_defaults(keep_xml=True)
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must be on or before --end")

    try:
        activities, sittings = find_sittings(args.start, args.end)
        args.output.mkdir(parents=True, exist_ok=True)
        if args.keep_xml:
            (args.output / "xml").mkdir(exist_ok=True)

        summary = io.StringIO(newline="")
        index = csv.DictWriter(summary, fieldnames=INDEX_FIELDS)
        index.writeheader()
        incomplete = 0
        for day in sorted(activities):
            row = download_day(
                day, set(activities[day]), sittings[day], args.output, args.keep_xml)
            index.writerow(row)
            incomplete += row["status"] != "downloaded"
            print(f"{day}  {row['status']}  {row['sections']} sections  {row['report_statuses']}".rstrip(),
                  flush=True)

        path = args.output / "stenograms_index.csv"
        write_file(path, summary.getvalue().encode("utf-8"))
        print(f"{len(activities) - incomplete} of {len(activities)} Vragenuur dates complete. Index: {path}")
        return int(bool(incomplete))
    except (OSError, ValueError, KeyError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
