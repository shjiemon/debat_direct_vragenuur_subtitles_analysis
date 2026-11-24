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
    summary = {"word_count": total_words}
    for term in SEARCH_TERMS:
        forms = {word: count for word, count in bag.items() if term in word}
        matches = sum(forms.values())
        summary[f"{term}_count"] = matches
        summary[f"{term}_per_1000"] = round(1_000 * matches / total_words, 6)
        summary[f"{term}_forms"] = json.dumps(
            forms, ensure_ascii=False, sort_keys=True)
    return summary


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def analyse(subtitles, output_dir):
    if not subtitles.is_dir():
        raise ValueError(f"Subtitle directory not found: {subtitles}")

    paths = sorted(path for path in subtitles.iterdir()
                   if DATED_SRT.fullmatch(path.name))
    if not paths:
        raise ValueError(f"No YYYY-MM-DD.srt files found in {subtitles}.")

    debates = []
    monthly_words = defaultdict(Counter)
    monthly_sessions = Counter()
    for path in paths:
        day = date.fromisoformat(path.stem).isoformat()
        words = subtitle_words(path)
        if not words:
            raise ValueError(f"{path}: the captions contain no words.")
        bag = Counter(words)
        debates.append({"date": day, **summarize_words(bag)})

        # Pool counts and words across all available debates in each month.
        month = day[:7]
        monthly_words[month].update(bag)
        monthly_sessions[month] += 1

    months = [
        {"month": month,
            "debate_count": monthly_sessions[month], **summarize_words(bag)}
        for month, bag in sorted(monthly_words.items())
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "vragenuur_by_debate.csv", debates)
    write_csv(output_dir / "vragenuur_by_month.csv", months)
    print(
        f"Analysed {len(debates)} debate(s) in {len(months)} month(s). CSV files: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subtitles", nargs="?", type=Path,
                        default=Path("subtitles"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args()

    try:
        analyse(args.subtitles, args.output_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
