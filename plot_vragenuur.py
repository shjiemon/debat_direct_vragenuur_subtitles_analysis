#!/usr/bin/env python3
"""Plot per-debate asylum and migration mentions from analyse_vragenuur.py."""

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import dates as mdates
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

ANALYSIS_DIRS = {
    "subtitles": Path("analysis_subtitles"),
    "stenograms": Path("analysis_stenograms"),
}
DEBATE_CSV = "vragenuur_by_debate.csv"
ASIEL = "#087C78"
MIGRATIE = "#9D4D16"
TOTAL = "#B9C7D8"
MISSING = "#AA3747"
NO_DEBATE = "#75879A"
INK = "#14253A"
MUTED = "#4E6070"
GRID = "#E2E8EE"


def read_debates(path):
    columns = {"date", "subtitle_status", "asiel_count", "migratie_count"}
    available, missing = [], []
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        absent = columns.difference(reader.fieldnames or ())
        if absent:
            raise ValueError(f"{path} is missing columns {', '.join(sorted(absent))}.")
        for row in reader:
            day = date.fromisoformat(row["date"])
            if row["subtitle_status"] == "missing":
                missing.append(day)
            elif row["subtitle_status"] == "available":
                available.append((day, int(row["asiel_count"]), int(row["migratie_count"])))
            else:
                raise ValueError(f"{day} has an unfamiliar coverage status.")
    if not available:
        raise ValueError(f"{path} contains no debates with usable text.")
    return sorted(available), sorted(missing)


def weeks_without_debate(debate_dates):
    """Return the Tuesdays of empty Monday–Sunday weeks within the data."""
    occupied = {day - timedelta(days=day.weekday()) for day in debate_dates}
    first, last = min(occupied), max(occupied)
    empty = []
    for offset in range(0, (last - first).days, 7):
        monday = first + timedelta(days=offset)
        if monday not in occupied:
            empty.append(monday + timedelta(days=1))
    return empty


def plot_mentions(analysis_dir, output, source="subtitles"):
    available, missing = read_debates(analysis_dir / DEBATE_CSV)
    days, asiel, migratie = zip(*available)
    debate_dates = (*days, *missing)
    empty_weeks = weeks_without_debate(debate_dates)
    first_day, last_day = min(debate_dates), max(debate_dates)
    start = first_day.replace(day=1)
    end = last_day + timedelta(days=14)
    total_debates = len(available) + len(missing)

    fig, (axis, coverage) = plt.subplots(
        2, 1, figsize=(15, 6.7), sharex=True,
        gridspec_kw={"height_ratios": [12, 1.5]},
    )
    try:
        fig.subplots_adjust(left=0.072, right=0.976, top=0.775, bottom=0.17, hspace=0.16)
        fig.text(0.072, 0.918, "Asylum and migration in the Vragenuur",
                 fontsize=23, fontweight="bold", color=INK)
        period = f"{first_day.day} {first_day:%b %Y} to {last_day.day} {last_day:%b %Y}"
        fig.text(0.072, 0.87, f"{period}  ·  {source.capitalize()} for {len(available)} of {total_debates} debates",
                 fontsize=11, color=MUTED)

        bars = axis.bar(days, [a + m for a, m in zip(asiel, migratie)], width=4.0,
                        color=TOTAL, linewidth=0, label="Combined count")
        # The hollow circle leaves the centre dot visible when both counts coincide.
        asylum = axis.scatter(days, asiel, s=16, marker="o", facecolors="none",
                              edgecolors=ASIEL, linewidths=1.0, zorder=3,
                              label="asiel", clip_on=False)
        migration = axis.scatter(days, migratie, s=7, color=MIGRATIE, linewidths=0,
                                 zorder=4, label="migratie", clip_on=False)
        fig.legend(handles=[bars, asylum, migration], loc="upper left",
                   bbox_to_anchor=(0.064, 0.845), ncol=3, frameon=False,
                   fontsize=10, labelcolor=INK, columnspacing=1.8)

        axis.set_ylabel("Word occurrences per debate", fontsize=10, color=MUTED)
        axis.set_ylim(bottom=0)
        axis.yaxis.set_major_locator(MaxNLocator(nbins=6, integer=True))
        axis.tick_params(axis="y", length=0, pad=7, colors=MUTED, labelsize=9)
        axis.tick_params(axis="x", length=0)
        axis.grid(axis="y", color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)

        coverage.set_yticks([])
        coverage.set_ylim(0, 1)
        coverage.axhline(0.72, color=GRID, linewidth=0.8)
        coverage.axhline(0.28, color=GRID, linewidth=0.8)
        coverage.scatter(missing, [0.72] * len(missing), marker="x", color=MISSING,
                         s=20, linewidths=1.0, zorder=3)
        coverage.bar(empty_weeks, 0.19, bottom=0.185, width=4.5, color=NO_DEBATE,
                     linewidth=0, zorder=3)
        coverage.tick_params(axis="x", length=3, color=TOTAL, pad=6,
                             labelsize=9, labelcolor=MUTED)
        months = (1, 7) if (end - start).days > 730 else (1, 4, 7, 10)
        ticks = mdates.MonthLocator(bymonth=months)
        coverage.xaxis.set_major_locator(ticks)
        coverage.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        coverage.set_xlim(start, end)
        for panel in (axis, coverage):
            for spine in panel.spines.values():
                spine.set_visible(False)

        coverage_legend = [
            Line2D([], [], marker="x", linestyle="none", color=MISSING,
                   label=f"Vragenuur held, {source} unavailable ({len(missing)} debates)"),
            Patch(facecolor=NO_DEBATE, linewidth=0,
                  label=f"No Vragenuur ({len(empty_weeks)} weeks)"),
        ]
        fig.legend(handles=coverage_legend, loc="center left", bbox_to_anchor=(0.064, 0.065),
                   ncol=2, frameon=False, fontsize=9, labelcolor=MUTED)
        fig.text(0.976, 0.065, "Counts include words containing asiel or migratie.",
                 fontsize=9, color=MUTED, ha="right", va="center")

        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=175, facecolor="white")
    finally:
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=ANALYSIS_DIRS, help="which analysis to plot")
    parser.add_argument("--analysis-dir", type=Path, help="override the analysis folder")
    parser.add_argument("--output", type=Path,
                        help="image path (defaults to vragenuur_mentions.png in the analysis directory)")
    args = parser.parse_args()
    analysis_dir = args.analysis_dir or ANALYSIS_DIRS[args.source]

    if not (analysis_dir / DEBATE_CSV).is_file():
        parser.exit(1, f"Missing {analysis_dir / DEBATE_CSV}. "
                    f"Run 'python analyse_vragenuur.py {args.source}' or check --analysis-dir.\n")
    output = args.output or analysis_dir / "vragenuur_mentions.png"
    try:
        plot_mentions(analysis_dir, output, args.source)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Saved {output}")


if __name__ == "__main__":
    main()
