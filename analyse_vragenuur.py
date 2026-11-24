#!/usr/bin/env python3
"""Count asylum and migration terms in dated Vragenuur SRT or stenogram text files."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from html import unescape
import json
from pathlib import Path
import re
from unicodedata import normalize

try:
    from nltk.corpus import stopwords
    from sklearn.feature_extraction.text import CountVectorizer
except ImportError as error:
    raise ValueError(
        "Install the dependencies with python -m pip install nltk scikit-learn.") from error

SEARCH_TERMS = ("asiel", "migratie")
STRIP_SPEAKER_LABELS_FROM_STENOGRAM_TXT = True
SOURCE_DEFAULTS = {
    "subtitles": (".srt", "analysis"),
    "stenograms": (".txt", "analysis_stenograms"),
}

DATED_INPUT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.(?:srt|txt)", re.IGNORECASE)
SPEAKER_PREFIX = "[[SPEAKER]] "
TIMESTAMP = r"[0-9]{2,}:[0-9]{2}:[0-9]{2}[,.][0-9]{3}"
CUE_TIMING = re.compile(rf"{TIMESTAMP}[ \t]+-->[ \t]+{TIMESTAMP}(?:[ \t]+.*)?")
FORMATTING_TAG = re.compile(r"<[^<>\n]+>")
WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)*")


def tokenize(text):
    return WORD.findall(normalize("NFC", text).casefold())


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
    return tokenize(captions)


def stenogram_words(path, strip_speaker_labels):
    """Read plain text, treating the speaker tag as markup."""
    lines = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith(SPEAKER_PREFIX):
            if strip_speaker_labels:
                continue
            line = line.removeprefix(SPEAKER_PREFIX)
        lines.append(line)
    return tokenize("\n".join(lines))


def vectorize_debates(files_by_date,
                      strip_speaker_labels=STRIP_SPEAKER_LABELS_FROM_STENOGRAM_TXT):
    """Build a bag of words per debate and retain the original word totals."""
    if not files_by_date:
        return {}, {}
    try:
        dutch_stopwords = set(stopwords.words("dutch"))
    except LookupError as error:
        raise ValueError(
            "Download the NLTK stopwords with python -m nltk.downloader stopwords.") from error

    word_counts = {}
    documents = {}
    for day, path in files_by_date.items():
        if path.suffix.casefold() == ".srt":
            words = subtitle_words(path)
        else:
            words = stenogram_words(path, strip_speaker_labels)
        if not words:
            raise ValueError(
                f"{path}: the preprocessed text contains no words.")
        word_counts[day] = len(words)
        kept = " ".join(word for word in words if word not in dutch_stopwords)
        if kept:
            documents[day] = kept

    bags = {}
    if documents:
        vectorizer = CountVectorizer(analyzer=str.split)
        matrix = vectorizer.fit_transform(documents.values())
        vocabulary = vectorizer.get_feature_names_out()
        for index, day in enumerate(documents):
            row = matrix.getrow(index)
            bags[day] = Counter({
                vocabulary[column]: int(count)
                for column, count in zip(row.indices, row.data)
            })
    return word_counts, bags


def summarize_words(bag, total_words):
    """Count matching word forms and normalize against all spoken words."""
    summary = {
        "word_count": total_words or None,
        "filtered_word_count": sum(bag.values()) if total_words else None,
    }
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


def analyse(input_dir, output_dir, index=None,
            strip_speaker_labels=STRIP_SPEAKER_LABELS_FROM_STENOGRAM_TXT, suffix=None):
    if not input_dir.is_dir():
        raise ValueError(f"Input directory not found: {input_dir}")

    files_by_date = {}
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and DATED_INPUT.fullmatch(path.name):
            if suffix and path.suffix.casefold() != suffix:
                continue
            day = date.fromisoformat(path.stem).isoformat()
            if day in files_by_date:
                raise ValueError(
                    f"{day}: found both {files_by_date[day].name} and {path.name}. "
                    "Use separate input folders to analyse these files.")
            files_by_date[day] = path

    if index is None:
        candidates = [input_dir.parent / "vragenuur_index.csv"]
        if suffix != ".srt":
            candidates.insert(0, input_dir / "stenograms_index.csv")
        for candidate in candidates:
            if candidate.is_file():
                index = candidate
                break

    debate_dates = set(files_by_date)
    if index is not None:
        debate_dates.update(read_index_dates(index))
    if not debate_dates:
        raise ValueError(
            f"No debates found in the index or dated .srt or .txt files in {input_dir}.")

    word_counts, bags = vectorize_debates(files_by_date, strip_speaker_labels)
    debates = []
    monthly_words = defaultdict(Counter)
    monthly_word_counts = Counter()
    analysed_by_month = Counter(day[:7] for day in word_counts)
    expected_by_month = Counter(day[:7] for day in debate_dates)
    for day in sorted(debate_dates):
        month = day[:7]
        bag = bags.get(day, Counter())
        total_words = word_counts.get(day, 0)
        monthly_words[month].update(bag)
        monthly_word_counts[month] += total_words

        debates.append({
            "date": day,
            "subtitle_status": "available" if day in word_counts else "missing",
            **summarize_words(bag, total_words),
        })

    # Pool words across available debates. Keep coverage from the full index.
    months = [
        {
            "month": month,
            "debate_count": analysed_by_month[month],
            "expected_debate_count": expected,
            "missing_debate_count": expected - analysed_by_month[month],
            **summarize_words(monthly_words[month], monthly_word_counts[month]),
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
    parser.add_argument("source", nargs="?", default="subtitles",
                        help="subtitles, stenograms, or an input folder (default: subtitles)")
    parser.add_argument("--input-dir", type=Path,
                        help="override the selected source folder")
    parser.add_argument("--output-dir", type=Path,
                        help="output folder (default: analysis or analysis_stenograms)")
    parser.add_argument(
        "--index", type=Path,
        help="CSV of completed debates (default: stenograms_index.csv in the "
             "stenogram folder, or vragenuur_index.csv in the input folder's parent)",
    )
    parser.add_argument(
        "--strip-speaker-labels", action=argparse.BooleanOptionalAction,
        default=STRIP_SPEAKER_LABELS_FROM_STENOGRAM_TXT,
        help="exclude speaker names and roles from stenogram word counts "
             f"(default: {STRIP_SPEAKER_LABELS_FROM_STENOGRAM_TXT})",
    )
    args = parser.parse_args()
    if args.input_dir is not None and args.source not in SOURCE_DEFAULTS:
        parser.error(
            "--input-dir requires a source mode (subtitles or stenograms)")
    suffix, default_output = SOURCE_DEFAULTS.get(
        args.source, (None, "analysis"))
    input_dir = args.input_dir or Path(args.source)
    output_dir = args.output_dir or Path(default_output)

    try:
        analyse(input_dir, output_dir, args.index,
                args.strip_speaker_labels, suffix=suffix)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
