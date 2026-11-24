#!/usr/bin/env python3
"""Count asylum and migration terms in dated Vragenuur subtitle files."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from html import unescape
import json
from pathlib import Path
import re
from unicodedata import normalize

SEARCH_TERMS = ("asiel", "migratie")
DATED_SRT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.srt")
TIMESTAMP = r"[0-9]{2,}:[0-9]{2}:[0-9]{2}[,.][0-9]{3}"
CUE_TIMING = re.compile(rf"{TIMESTAMP}[ \t]+-->[ \t]+{TIMESTAMP}(?:[ \t]+.*)?")
FORMATTING_TAG = re.compile(r"<[^<>\n]+>")
WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)*")


def subtitle_words(path):
    """Read the spoken text from the subtitle blocks and tokenize it."""
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError(f"{path}: the subtitle file is empty.")

    caption_lines = []
    for number, block in enumerate(re.split(r"\n(?:[ \t]*\n)+", text), start=1):
        lines = block.splitlines()
        # An SRT block starts with an optional cue number and a timing line.
        timing_index = int(lines[0].strip().isdecimal())
        if len(lines) <= timing_index or not CUE_TIMING.fullmatch(lines[timing_index].strip()):
            raise ValueError(
                f"{path}: expected a timing line in cue {number}.")
        caption_lines.extend(lines[timing_index + 1:])

    captions = "\n".join(caption_lines)
    captions = unescape(FORMATTING_TAG.sub("", captions))
    return WORD.findall(normalize("NFC", captions).casefold())


def summarize_words(bag):
    """Count matching word forms and normalize against all spoken words."""
    total_words = sum(bag.values())
    summary = {"word_count": total_words or None}
    for term in SEARCH_TERMS:
        forms = {word: count for word, count in bag.items() if term in word}
        matches = sum(forms.values())
        summary[f"{term}_count"] = matches if total_words else None
        summary[f"{term}_per_1000"] = round(
            1_000 * matches / total_words, 6) if total_words else None
        summary[f"{term}_forms"] = json.dumps(
            forms, ensure_ascii=False, sort_keys=True) if total_words else None
    return summary


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def read_index_dates(index):
    """Read the dates of completed debates, including those with no download."""
    with index.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if "date" not in (reader.fieldnames or []):
            raise ValueError(f"{index}: expected a date column.")
        return {date.fromisoformat(row["date"]).isoformat() for row in reader}


def analyse(subtitles, output_dir, index=None):
    if not subtitles.is_dir():
        raise ValueError(f"Subtitle directory not found: {subtitles}")

    files_by_date = {
        date.fromisoformat(path.stem).isoformat(): path
        for path in subtitles.iterdir()
        if path.is_file() and DATED_SRT.fullmatch(path.name)
    }
    if index is None:
        default_index = subtitles.parent / "vragenuur_index.csv"
        if default_index.is_file():
            index = default_index

    debate_dates = set(files_by_date)
    if index is not None:
        debate_dates.update(read_index_dates(index))
    if not debate_dates:
        raise ValueError(
            f"No debates found in the index or YYYY-MM-DD.srt files in {subtitles}.")

    debates = []
    monthly_words = defaultdict(Counter)
    analysed_by_month = Counter()
    expected_by_month = Counter(day[:7] for day in debate_dates)
    for day in sorted(debate_dates):
        month = day[:7]
        path = files_by_date.get(day)
        bag = Counter()
        if path is not None:
            bag.update(subtitle_words(path))
            if not bag:
                raise ValueError(f"{path}: the captions contain no words.")
            monthly_words[month].update(bag)
            analysed_by_month[month] += 1

        debates.append({
            "date": day,
            "subtitle_status": "available" if path is not None else "missing",
            **summarize_words(bag),
        })

    # Pool words across available debates. Keep coverage from the full index.
    months = [
        {
            "month": month,
            "debate_count": analysed_by_month[month],
            "expected_debate_count": expected,
            "missing_debate_count": expected - analysed_by_month[month],
            **summarize_words(monthly_words[month]),
        }
        for month, expected in sorted(expected_by_month.items())
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "vragenuur_by_debate.csv", debates)
    write_csv(output_dir / "vragenuur_by_month.csv", months)
    print(
        f"Analysed {len(files_by_date)} of {len(debates)} debates across {len(months)} months. "
        f"CSV files: {output_dir}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subtitles", nargs="?", type=Path,
                        default=Path("subtitles"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis"))
    parser.add_argument(
        "--index", type=Path,
        help="CSV of completed debates (defaults to vragenuur_index.csv next to the subtitle directory)",
    )
    args = parser.parse_args()

    try:
        analyse(args.subtitles, args.output_dir, args.index)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
