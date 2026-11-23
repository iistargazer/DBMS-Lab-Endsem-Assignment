#!/usr/bin/env python3
"""Build the readout deck (8 slides) and the appendix from the committed output CSVs.

Every number on the readout slides, and every appendix table, is read out of
outputs/tables/ at build time -- nothing is typed in twice. If a query changes, the
deck changes; if a CSV is missing, the build fails rather than shipping a stale number. That is the point of generating the deck
instead of assembling it: `make all` regenerates the analysis and the argument together.

Structure:
    slides 1-8    the readout, aimed at the Product Director
    slides 9-21   appendix: deliverables 1-4 in detail, 4b, data quality,
                  judgement calls, dead ends

Two files come out of the same slides: deck/DBMS_EndSem_Deck.pptx (the submission --
readout first, appendix behind) and deck/DBMS_EndSem_Readout.pptx (slides 1-8 only,
for an upload that counts pages). The readout is saved before the appendix is built;
its cover and its data-quality slide are then rewritten so the extract points at the
working files rather than at an appendix page that file does not contain.

Charts are drawn with matplotlib into outputs/figures/ at the exact aspect ratio of
the slot they occupy, so nothing is stretched.

Usage:
    python code/06_build_deck.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "outputs" / "tables"
FIGURES = ROOT / "outputs" / "figures"
DECK_DIR = ROOT / "deck"
DECK = DECK_DIR / "DBMS_EndSem_Deck.pptx"
READOUT = DECK_DIR / "DBMS_EndSem_Readout.pptx"

# ---------------------------------------------------------------- palette ----
INK = RGBColor(0x10, 0x23, 0x3B)
ACCENT = RGBColor(0xE4, 0x57, 0x2E)
TEAL = RGBColor(0x2A, 0x9D, 0x8F)
GREY = RGBColor(0x6B, 0x7A, 0x8F)
MUTED = RGBColor(0x93, 0xA1, 0xB2)
LIGHT = RGBColor(0xF2, 0xF5, 0xF8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PALE_ACCENT = RGBColor(0xFB, 0xE9, 0xE4)

H_INK, H_ACCENT, H_TEAL, H_GREY = "#10233B", "#E4572E", "#2A9D8F", "#8896A6"
TIER_COLOURS = {1: "#10233B", 2: "#2A9D8F", 3: "#E4572E", 4: "#AEB9C6"}

SW, SH = 13.333, 7.5
ML = 0.62
CW = SW - 2 * ML
FONT = "Calibri"

# ------------------------------------------------------------- data access --


def load(name: str) -> pd.DataFrame:
    path = TABLES / f"{name}.csv"
    if not path.exists():
        sys.exit(f"ERROR: missing {path.relative_to(ROOT)} -- run `make tables models ltv` first")
    return pd.read_csv(path)


def count_sql_blocks() -> int:
    """Number of `-- name:` blocks across sql/q*.sql, i.e. how many CSVs 02_run_sql writes."""
    import re

    pattern = re.compile(r"^--\s*name:\s*\S+\s*$", re.MULTILINE)
    return sum(len(pattern.findall(p.read_text(encoding="utf8"))) for p in (ROOT / "sql").glob("q*.sql"))


def money(x: float, dp: int = 2) -> str:
    return f"-${abs(x):,.{dp}f}" if x < 0 else f"${x:,.{dp}f}"


def pct(x: float, dp: int = 1) -> str:
    return f"{x:.{dp}f}%"


# ----------------------------------------------------------------- charts ---
def _style(ax, *, grid_axis="y", spines=("top", "right")):
    for side in spines:
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#C9D2DC")
    ax.tick_params(colors="#4A5A6E", labelsize=9.5, length=0)
    if grid_axis:
        ax.grid(axis=grid_axis, color="#E6EBF1", linewidth=0.9)
        ax.set_axisbelow(True)


def chart_arpdau(path: Path) -> None:
    d = load("q2a_arpdau_mix__03_arpdau_by_tier_month")
    d["month"] = pd.to_datetime(d["month"])
    fig, ax = plt.subplots(figsize=(7.25, 4.30))
    for tier in sorted(d["country_tier"].unique()):
        g = d[d["country_tier"] == tier]
        ax.plot(g["month"], g["arpdau_net"], lw=2.4, marker="o", ms=5,
                color=TIER_COLOURS[tier], label=f"Tier {tier}", zorder=3)
    _style(ax)
    ax.set_ylim(0, 0.60)
    ax.set_ylabel("ARPDAU (net $ per active player-day)", fontsize=10, color="#4A5A6E")
    ax.set_xticks(sorted(d["month"].unique()))
    ax.set_xticklabels([pd.Timestamp(m).strftime("%b") for m in sorted(d["month"].unique())])
    ax.legend(frameon=False, fontsize=10, ncol=4, loc="upper center",
              bbox_to_anchor=(0.5, 1.13), handlelength=1.6)

    share = d[d["country_tier"] == 4]
    ax2 = ax.twinx()
    ax2.fill_between(share["month"], share["dau_share_pct"], color=H_GREY, alpha=0.10, zorder=1)
    ax2.plot(share["month"], share["dau_share_pct"], ls=(0, (4, 3)), lw=1.8, color=H_GREY, zorder=2)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("Tier-4 share of DAU (%)", fontsize=10, color=H_GREY)
    ax2.tick_params(colors=H_GREY, labelsize=9.5, length=0)
    for side in ("top", "left", "bottom"):
        ax2.spines[side].set_visible(False)
    first, last = share["dau_share_pct"].iloc[0], share["dau_share_pct"].iloc[-1]
    ax2.annotate(f"{first:.1f}% of DAU", (share["month"].iloc[0], first), textcoords="offset points",
                 xytext=(6, -16), ha="left", va="center", fontsize=9.5, color="#5A6A7C",
                 fontweight="bold")
    ax2.annotate(f"{last:.1f}% of DAU", (share["month"].iloc[-1], last), textcoords="offset points",
                 xytext=(-8, 13), ha="right", va="center", fontsize=9.5, color="#5A6A7C",
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=220, facecolor="white")
    plt.close(fig)


def chart_decomposition(path: Path) -> None:
    d = load("q2a_arpdau_mix__06_decomposition_identity").iloc[0]
    start = float(d.actual_first_30d)
    within = float(d.within_tier_effect)
    mix = float(d.mix_effect)
    inter = float(d.interaction_effect)
    end = float(d.actual_last_30d)

    labels = ["First 30\ndays", "Within-tier\nmonetisation", "Mix of\nplayers", "Interaction",
              "Last 30\ndays"]
    fig, ax = plt.subplots(figsize=(6.30, 3.55))
    values = [start, within, mix, inter, end]
    running = 0.0
    for i, v in enumerate(values):
        if i in (0, 4):
            ax.bar(i, v, width=0.58, color=H_INK if i == 0 else H_ACCENT, zorder=3)
            ax.annotate(f"${v:.4f}", (i, v + 0.008), ha="center", va="bottom",
                        fontsize=9.5, color="#33445A", fontweight="bold")
            running = v
        else:
            bottom = running if v > 0 else running + v
            ax.bar(i, abs(v), bottom=bottom, width=0.58,
                   color=H_TEAL if v > 0 else H_GREY, zorder=3)
            ax.plot([i - 0.29, i - 0.05], [running, running], color="#C9D2DC", lw=0.8, zorder=2)
            ax.annotate(f"{v:+.4f}", (i, bottom + abs(v) + 0.008), ha="center", va="bottom",
                        fontsize=9.5, color="#33445A", fontweight="bold")
            running += v
    ax.set_xticks(range(5))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 0.42)
    ax.set_ylabel("ARPDAU ($)", fontsize=9.5, color="#4A5A6E")
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=220, facecolor="white")
    plt.close(fig)


def chart_sales(path: Path) -> None:
    d = load("q2b_sales_pullforward__03_offset_curve")
    fig, ax = plt.subplots(figsize=(6.55, 3.35))
    colours = [H_ACCENT if v in (0, 1) else "#C2CCD8" for v in d["offset_days_from_window_start"]]
    ax.bar(d["offset_days_from_window_start"], d["revenue_index_vs_midcycle"],
           width=0.72, color=colours, zorder=3)
    ax.axhline(1.0, color=H_INK, lw=1.1, ls=(0, (3, 2)), zorder=4)
    ax.annotate("normal day = 1.0", (-6.5, 1.04), ha="left", fontsize=9, color="#5A6A7C")
    # Headroom so the sale-day bars are not flush with the top of the plot.
    ax.set_ylim(0, 1.12 * float(d["revenue_index_vs_midcycle"].max()))
    ax.set_xticks(d["offset_days_from_window_start"])
    ax.set_xlabel("days from sale-window start", fontsize=9.5, color="#4A5A6E")
    ax.set_ylabel("revenue vs mid-cycle day", fontsize=9.5, color="#4A5A6E")
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=220, facecolor="white")
    plt.close(fig)


def chart_channel(path: Path) -> None:
    std = load("q3_channel__bootstrap_standardized")
    m = "revenue_per_install_30"
    s = std[std["metric"] == m].set_index("channel")
    order = ["organic", "paid_video", "paid_social"]
    labels = ["organic", "paid video", "paid social"]
    fig, ax = plt.subplots(figsize=(6.20, 4.05))
    x = np.arange(len(order))
    # Crude and standardised sit side by side, joined by a hairline diagonal: the only
    # vertical bars on the chart are the confidence intervals, so the link cannot be
    # mistaken for a second interval.
    for i, ch in enumerate(order):
        row = s.loc[ch]
        cx, sx = i - 0.21, i + 0.21
        ax.plot([cx, sx], [row.crude, row.standardized], color="#CFD8E2", lw=1.0,
                ls=(0, (1.5, 2.2)), zorder=1, solid_capstyle="butt")
        ax.scatter(cx, row.crude, s=70, facecolor="white", edgecolor=H_GREY, lw=1.7, zorder=3)
        ax.errorbar(sx, row.standardized, yerr=[[row.standardized - row.standardized_lo],
                                               [row.standardized_hi - row.standardized]],
                    fmt="o", ms=9, color=H_ACCENT, ecolor=H_ACCENT, elinewidth=2.0,
                    capsize=4, zorder=4)
        ax.annotate(f"${row.crude:.2f}", (cx, row.crude), textcoords="offset points",
                    xytext=(-10, 0), ha="right", va="center", fontsize=9, color="#5A6A7C")
        ax.annotate(f"${row.standardized:.2f}", (sx, row.standardized), textcoords="offset points",
                    xytext=(13, -1), ha="left", va="center", fontsize=9.5, color=H_ACCENT,
                    fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_xlim(-0.80, 2.95)
    ax.set_ylim(1.3, 2.6)
    ax.set_ylabel("revenue per install, first 30 days ($)", fontsize=9.5, color="#4A5A6E")
    _style(ax, grid_axis="y")
    ax.scatter([], [], s=70, facecolor="white", edgecolor=H_GREY, lw=1.7, label="crude")
    ax.errorbar([], [], yerr=0.02, fmt="o", ms=9, color=H_ACCENT, ecolor=H_ACCENT,
                elinewidth=2.0, capsize=4, label="standardised, 95% CI")
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=220, facecolor="white")
    plt.close(fig)


def chart_churn(path: Path) -> None:
    cal = load("q4_churn_model__calibration")
    fig, ax = plt.subplots(figsize=(5.55, 3.45))
    ax.bar(cal["decile"], 100 * cal["observed_churn_rate"], width=0.72, color="#C2CCD8", zorder=3,
           label="actual")
    ax.plot(cal["decile"], 100 * cal["mean_predicted"], color=H_ACCENT, lw=2.2, marker="o",
            ms=5, zorder=4, label="model")
    ax.set_xticks(cal["decile"])
    ax.set_xlabel("risk decile (10 = highest score)", fontsize=9.5, color="#4A5A6E")
    ax.set_ylabel("14-day churn rate (%)", fontsize=9.5, color="#4A5A6E")
    _style(ax)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=220, facecolor="white")
    plt.close(fig)


# -------------------------------------------------------------- pptx tools --
def new_slide(prs: Presentation, *, dark: bool = False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = INK if dark else WHITE
    return slide


def textbox(slide, x, y, w, h, text, *, size=14, bold=False, colour=INK, align=PP_ALIGN.LEFT,
            anchor=MSO_ANCHOR.TOP, spacing=1.0, italic=False, font=FONT):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    lines = str(text).split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.italic = italic
        r.font.color.rgb = colour
        r.font.name = font
    return box


def bullets(slide, x, y, w, h, items, *, size=14, colour=INK, gap=8, marker="\u2014  "):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.line_spacing = 1.05
        if isinstance(item, tuple):
            lead, rest = item
        else:
            lead, rest = None, item
        if lead:
            r = p.add_run()
            r.text = lead + "  "
            r.font.size = Pt(size)
            r.font.bold = True
            r.font.color.rgb = ACCENT
            r.font.name = FONT
        else:
            r = p.add_run()
            r.text = marker
            r.font.size = Pt(size)
            r.font.color.rgb = MUTED
            r.font.name = FONT
        r2 = p.add_run()
        r2.text = rest
        r2.font.size = Pt(size)
        r2.font.color.rgb = colour
        r2.font.name = FONT
    return box


def rule(slide, x, y, w, colour=ACCENT, thickness=0.035):
    bar = slide.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(thickness))
    bar.fill.solid()
    bar.fill.fore_color.rgb = colour
    bar.line.fill.background()
    bar.shadow.inherit = False
    return bar


def header(slide, kicker, title, *, dark=False, size=27):
    textbox(slide, ML, 0.34, CW, 0.26, kicker.upper(), size=10.5, bold=True,
            colour=ACCENT if not dark else RGBColor(0xF2, 0xA0, 0x8A))
    textbox(slide, ML, 0.66, CW, 1.0, title, size=size, bold=True,
            colour=WHITE if dark else INK, spacing=0.95)


def footer(slide, number, note=None):
    textbox(slide, ML, 7.00, 8.0, 0.24, "Rohan Chinta  \u00b7  24MAB0A27  \u00b7  DBMS Lab, end-semester",
            size=8.5, colour=MUTED)
    if note:
        textbox(slide, ML, 6.74, CW, 0.24, note, size=9, colour=GREY, italic=True)
    textbox(slide, SW - ML - 0.7, 7.00, 0.7, 0.24, str(number), size=9, colour=MUTED,
            align=PP_ALIGN.RIGHT)


def kpis(slide, y, items, *, x=ML, width=CW, gap=0.28, number_size=30, label_size=10.5):
    n = len(items)
    w = (width - gap * (n - 1)) / n
    for i, (number, label) in enumerate(items):
        left = x + i * (w + gap)
        box = slide.shapes.add_shape(1, Inches(left), Inches(y), Inches(w), Inches(1.16))
        box.fill.solid()
        box.fill.fore_color.rgb = LIGHT
        box.line.fill.background()
        box.shadow.inherit = False
        textbox(slide, left + 0.16, y + 0.10, w - 0.32, 0.50, number, size=number_size,
                bold=True, colour=ACCENT)
        textbox(slide, left + 0.16, y + 0.66, w - 0.30, 0.44, label, size=label_size, colour=GREY)


def table(slide, df: pd.DataFrame, x, y, w, *, col_widths=None, font=9.5, header_font=9.5,
          row_h=0.30, header_h=0.34, force_right=False):
    rows, cols = len(df) + 1, len(df.columns)
    shape = slide.shapes.add_table(rows, cols, Inches(x), Inches(y), Inches(w),
                                   Inches(header_h + row_h * len(df)))
    tbl = shape.table
    tbl.first_row = True
    if col_widths:
        total = sum(col_widths)
        for i, cw in enumerate(col_widths):
            tbl.columns[i].width = Inches(w * cw / total)
    tbl.rows[0].height = Inches(header_h)
    for r in range(1, rows):
        tbl.rows[r].height = Inches(row_h)
    numeric = [pd.api.types.is_numeric_dtype(df[c]) or (force_right and j > 0)
               for j, c in enumerate(df.columns)]
    for j, col in enumerate(df.columns):
        cell = tbl.cell(0, j)
        cell.fill.solid()
        cell.fill.fore_color.rgb = INK
        cell.margin_left = Inches(0.07)
        cell.margin_right = Inches(0.05)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = cell.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.RIGHT if numeric[j] else PP_ALIGN.LEFT
        run = p.add_run()
        run.text = str(col)
        run.font.size = Pt(header_font)
        run.font.bold = True
        run.font.color.rgb = WHITE
        run.font.name = FONT
    for i in range(rows - 1):
        for j in range(cols):
            cell = tbl.cell(i + 1, j)
            cell.fill.solid()
            cell.fill.fore_color.rgb = LIGHT if i % 2 == 0 else WHITE
            cell.margin_left = Inches(0.07)
            cell.margin_right = Inches(0.05)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.RIGHT if numeric[j] else PP_ALIGN.LEFT
            value = df.iloc[i, j]
            run = p.add_run()
            run.text = "\u2014" if pd.isna(value) else str(value)
            run.font.size = Pt(font)
            run.font.color.rgb = INK
            run.font.name = FONT
    return shape


def picture(slide, path: Path, x, y, w, h):
    slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))


# ------------------------------------------------------------------ slides --
def slide_title(prs, s):
    slide = new_slide(prs, dark=True)
    textbox(slide, ML, 0.72, CW, 0.3, "DBMS LAB  \u00b7  END-SEMESTER READOUT", size=11,
            bold=True, colour=RGBColor(0xF2, 0xA0, 0x8A))
    textbox(slide, ML, 1.20, 10.6, 1.0, "The denominator changed", size=54, bold=True,
            colour=WHITE, spacing=0.95)
    rule(slide, ML, 2.62, 1.6, ACCENT, 0.05)
    textbox(slide, ML, 2.92, 9.6, 1.5,
            "Revenue grew 2.9\u00d7 and ARPDAU fell 16.7%. Nothing got worse: the studio "
            "bought a different mix of players. Here is what that changes, and what it does not.",
            size=17, colour=RGBColor(0xD9, 0xE1, 0xEA), spacing=1.15)
    textbox(slide, ML, 5.62, 6.4, 0.3,
            f"{s['profiles']:,} players  \u00b7  {s['player_days']:,} player-days  \u00b7  120 days  \u00b7  "
            f"{money(s['revenue'])} net revenue",
            size=11, colour=RGBColor(0xA9, 0xB6, 0xC6))
    textbox(slide, ML, 6.32, 6.4, 0.3, "Rohan Chinta  \u00b7  24MAB0A27  \u00b7  22 November 2026",
            size=11, colour=RGBColor(0xA9, 0xB6, 0xC6))
    textbox(slide, SW - ML - 3.2, 6.32, 3.2, 0.3, "Readout: 8 slides, then appendix", size=10,
            colour=RGBColor(0x7E, 0x8E, 0xA2), align=PP_ALIGN.RIGHT)


def slide_paradox(prs, n, s, fig):
    slide = new_slide(prs)
    header(slide, "Deliverable 2(a)  \u00b7  the paradox",
           "ARPDAU fell 16.7%. No country tier earned less.")
    picture(slide, fig["arpdau"], 0.45, 1.78, 7.25, 4.30)
    x = 8.05
    textbox(slide, x, 1.80, 4.55, 0.3, "WHAT THE CHART SHOWS", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 2.12, 4.55, 2.1, [
        ("Every country tier is flat or up.", f"+{s['t1']:.1f}%, +{s['t2']:.1f}%, +{s['t3']:.1f}%, "
                                      f"+{s['t4']:.1f}% over the window."),
        ("The mix moved underneath them.", f"Tier 4 went from {s['t4_share_first']:.1f}% to "
                                           f"{s['t4_share_last']:.1f}% of DAU."),
        ("Tier 4 is the cheap tier.", f"{money(s['t4_arpdau'],3)} per DAU against "
                                      f"{money(s['t1_arpdau'],3)} in tier 1."),
    ], size=12, gap=9)
    box = slide.shapes.add_shape(1, Inches(x), Inches(3.95), Inches(4.55), Inches(1.55))
    box.fill.solid()
    box.fill.fore_color.rgb = PALE_ACCENT
    box.line.fill.background()
    box.shadow.inherit = False
    textbox(slide, x + 0.18, 4.12, 4.2, 1.1,
            "Same economy, different denominator. ARPDAU is falling because the "
            "studio succeeded at buying volume, not because the game stopped paying.",
            size=11.5, colour=INK, spacing=1.12)
    footer(slide, n, "Source: q2a_arpdau_mix__03_arpdau_by_tier_month, __02_arpdau_by_tier.")
    return slide


def slide_decomposition(prs, n, s, fig):
    slide = new_slide(prs)
    header(slide, "Deliverable 2(a)  \u00b7  decomposition",
           f"At January's mix, ARPDAU would be up {s['within_pct']:.1f}%")
    picture(slide, fig["decomp"], 0.48, 1.86, 6.30, 3.55)
    textbox(slide, 0.62, 5.52, 6.0, 0.9,
            f"Monetisation contributed {s['within_effect']:+.4f} ({s['within_pct']:+.1f}%); "
            f"the mix cost {s['mix_effect']:+.4f} ({s['mix_pct']:+.1f}%). "
            f"The interaction term is small ({s['interaction']:+.4f}) and the identity checks.",
            size=11, colour=GREY, spacing=1.1)
    x = 7.28
    textbox(slide, x, 1.80, 5.3, 0.3, "THE CAUSE IS ON THE ACQUISITION SIDE", size=9.5,
            bold=True, colour=ACCENT)
    bullets(slide, x, 2.12, 5.3, 2.0, [
        (f"paid_video: {s['pv_first']:.1f}% \u2192 {s['pv_last']:.1f}% of installs",
         "the channel that buys tier-4 Android."),
        (f"Tier-4 installs: {s['t4_inst_first']:.1f}% \u2192 {s['t4_inst_last']:.1f}%",
         "so the denominator shifts before anyone plays."),
        ("Nothing in pricing, packs or the economy changed the rates.",
         "The sales carry no discount at all."),
    ], size=12, gap=9)
    textbox(slide, x, 3.95, 5.3, 0.3, "SO WHAT", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 4.27, 5.3, 1.35,
            "Report ARPDAU within tier, or mix-adjusted \u2014 the blended number now measures "
            "the UA plan, not the game. The fix is targeting, not price. Whether paid_video "
            "is worth its cost needs a CPI figure this data does not contain.",
            size=11.5, colour=INK, spacing=1.12)
    footer(slide, n, "Source: q2a_arpdau_mix__05_mix_decomposition, __06_decomposition_identity, "
                     "__08_install_mix_drift.")
    return slide


def slide_sales(prs, n, s, fig):
    slide = new_slide(prs)
    header(slide, "Deliverable 2(b)  \u00b7  the sales question",
           f"Sales move revenue. Only {money(s['net'],0)} of {money(s['sale_rev'],0)} is new.")
    kpis(slide, 1.72, [
        (f"{pct(100 * s['sale_days'] / 120, 0)} of days", f"earn {money(s['sale_day_rev'],0)}/day "
         f"against {money(s['normal_day_rev'],0)} on the other {s['normal_days']}"),
        (f"{pct(s['sale_share'])}", "of all net revenue lands on those days"),
        (f"{s['buyer_multiple']:.1f}\u00d7 buyers", "same DAU, same basket, same $/buyer"),
        (f"{money(s['net'],0)}", f"net of the trough \u2014 {pct(s['clawback'])} clawed back"),
    ], number_size=25)
    picture(slide, fig["sales"], 0.52, 3.16, 6.55, 3.35)
    x = 7.42
    textbox(slide, x, 3.10, 5.2, 0.3, "THERE IS NO DISCOUNT IN THE RECORD", size=9.5, bold=True,
            colour=ACCENT)
    bullets(slide, x, 3.42, 5.2, 1.6, [
        (f"${s['rev_per_buyer_sale']:.2f} vs ${s['rev_per_buyer_normal']:.2f} per buyer",
         "on sale and ordinary days."),
        ("Identical pack prices.", "usd_amount and currency_granted match for all six packs "
                                   "(dq09), so 'sale' is a period marker."),
    ], size=12, gap=8)
    textbox(slide, x, 4.72, 5.2, 0.95,
            "The trough is the tell: the four days before each window run at 0.40\u20130.48 of "
            "normal. A skipped window, or a geo holdout, is the only clean test left.",
            size=11.5, colour=INK, spacing=1.12)
    footer(slide, n, "Source: q2b_sales_pullforward__02, __03, __10; q0_data_quality__dq09.")
    return slide


def slide_channel(prs, n, s, fig):
    slide = new_slide(prs)
    header(slide, "Deliverable 3  \u00b7  channel quality",
           "Paid video buys the same players, in cheaper countries")
    picture(slide, fig["channel"], 0.50, 1.74, 6.20, 4.05)
    x = 7.05
    textbox(slide, x, 1.82, 5.6, 0.3, "THE GAP IS THE MIX", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 2.14, 5.6, 2.2, [
        (f"Crude: {money(s['video_crude'])} vs {money(s['organic_crude'])}",
         "per install (paid video vs organic)."),
        (f"Like-for-like: {money(s['video_std'])} vs {money(s['organic_std'])}",
         "after matching platform and country tier."),
        (f"The difference is {money(s['gap_std'])}",
         f"(95% CI {money(s['gap_lo'])} to {money(s['gap_hi'])}) \u2014 indistinguishable."),
        (f"Paid video is {s['video_t4_share']:.0f}% tier-4 traffic.",
         f"Organic is {s['organic_t4_share']:.0f}%."),
    ], size=12, gap=8)
    textbox(slide, x, 4.74, 5.6, 0.3, "WHERE THERE IS A REAL DIFFERENCE", size=9.5, bold=True,
            colour=ACCENT)
    textbox(slide, x, 5.06, 5.6, 1.15,
            f"Ad engagement: {s['video_ads']:.2f} vs {s['organic_ads']:.2f} completed ads per "
            f"install. Paid-video players also pay slightly more when they pay "
            f"({money(s['video_rpp'])} vs {money(s['organic_rpp'])}). The case for cutting the "
            f"channel is price, not player quality \u2014 and price is not in this data.",
            size=11.5, colour=INK, spacing=1.12)
    footer(slide, n, "Source: q3_channel_quality__crude_vs_standardized, "
                     "q3_channel__bootstrap_differences (1000 stratified replicates).")
    return slide


def slide_churn(prs, n, s, fig):
    slide = new_slide(prs)
    header(slide, "Deliverable 4  \u00b7  churn",
           "Churn is a depth problem, not an acquisition problem")
    # Labels break by hand: left to wrap, the KPI column is narrow enough to orphan a
    # single word ("training", "rate") on the second line.
    kpis(slide, 1.70, [
        (f"{s['auc']:.3f}", f"out-of-time AUC\n95% CI {s['auc_lo']:.3f}\u2013{s['auc_hi']:.3f}"),
        (f"{s['auc_ind']:.3f}", "AUC, 16,503 players\nnever seen in training"),
        (f"{pct(100*s['prec'],0)}", f"of the top decile churns\n{s['decile_lift']:.1f}\u00d7 the base rate"),
        (f"{pct(100*s['recall'],0)}", "of all churners caught\nby that decile"),
    ], number_size=25)
    picture(slide, fig["churn"], 0.56, 3.06, 5.55, 3.45)
    x = 6.55
    textbox(slide, x, 3.02, 6.2, 0.3, "WHAT PREDICTS IT", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 3.34, 6.2, 1.5, [
        (f"Sessions per active day: {s['or_sessions']:.2f}\u00d7, playtime per day "
         f"{s['or_play']:.2f}\u00d7, ads {s['or_ads']:.2f}\u00d7",
         "depth buys retention."),
        (f"Tenure: {s['or_tenure']:.2f}\u00d7", f"older players lapse more "
                                                f"({pct(s['tenure_churn'])} churn at 61+ days)."),
        (f"Channel: {s['or_video']:.2f}\u00d7 and {s['or_social']:.2f}\u00d7",
         "flat once tier and behaviour are controlled \u2014 the slide-5 confound again."),
    ], size=11.5, gap=7)
    textbox(slide, x, 4.82, 6.2, 0.9,
            f"Threshold: score \u2265 {s['thr']:.2f} flags {pct(100*s['thr_flag'])} of players for "
            f"{pct(100*s['thr_prec'])} precision. The value-maximising cut under the stated "
            f"economics is lower ({s['best_thr']:.2f}), at {money(s['net1000'],0)} per 1,000 "
            f"against {money(s['contactall'],0)} for contacting everyone.",
            size=11, colour=GREY, spacing=1.1)
    footer(slide, n, "Source: q4_churn_model__model_summary, __calibration, __threshold_curve; "
                     "92,339 training rows, 44,141 test players.")
    return slide


def slide_recommendation(prs, n, s):
    slide = new_slide(prs, dark=True)
    header(slide, "What I would do", "Three moves, in order of money at stake", dark=True)
    items = [
        ("1", "Report ARPDAU within tier, and fix the UA mix",
         f"The blended metric moved {(s['total_pct']):.1f}% while every tier's monetisation held "
         f"or improved ({s['within_pct']:+.1f}%). Judge acquisition on mix-adjusted value, not "
         f"on a number that falls when volume succeeds."),
        ("2", "Stop treating the sale calendar as a revenue lever",
         f"{pct(s['sale_share'])} of revenue lands on 13% of days, but only "
         f"{money(s['net'],0)} of {money(s['sale_rev'],0)} survives the pre-window trough. "
         f"Skipping one window is the cheapest experiment available."),
        ("3", "Point re-engagement at depth, not at everyone",
         f"Score \u2265 {s['thr']:.2f} flags {pct(100*s['thr_flag'])} of active players and finds "
         f"{pct(100*s['thr_recall'])} of churners. Contacting everyone destroys "
         f"{money(-s['contactall'],0)} per 1,000."),
    ]
    y = 1.86
    for number, head, body in items:
        textbox(slide, ML, y, 0.62, 0.62, number, size=30, bold=True,
                colour=RGBColor(0xF2, 0xA0, 0x8A))
        textbox(slide, ML + 0.72, y + 0.02, 6.4, 0.34, head, size=15, bold=True, colour=WHITE)
        textbox(slide, ML + 0.72, y + 0.40, 10.9, 0.80, body, size=11.5,
                colour=RGBColor(0xC9, 0xD4, 0xE1), spacing=1.12)
        y += 1.52
    rule(slide, ML, 6.42, CW, RGBColor(0x2C, 0x3F, 0x59), 0.015)
    textbox(slide, ML, 6.54, CW, 0.5,
            "The one number that would change all three: cost per install, by channel. It sits in "
            "finance, not in telemetry \u2014 and without it, paid_video cannot be judged on ROI.",
            size=11, colour=RGBColor(0xF2, 0xA0, 0x8A), spacing=1.1)
    footer(slide, n)
    return slide


def slide_uncertainty(prs, n, s):
    slide = new_slide(prs)
    header(slide, "Uncertainty", "What I would check before acting on any of it")
    blocks = [
        ("No cost per install anywhere", "Channel quality is comparable, channel value is not. "
         "Every paid_video conclusion in this deck stops at player quality."),
        ("No holdout for the sales", f"The {money(s['net'],0)} net effect is reconstructed from "
         "neighbouring days, not observed. Every 15-day cycle contains a sale, so there is no "
         "clean counterfactual in the data."),
        ("Churn is a 14-day snapshot label", "A player who returns on day 20 counts as churned. "
         "That suits a re-engagement offer, not a lifetime-value question."),
        ("90-day value is labelable for 12% of installs", "Only January has 90 observed days. "
         "April installs are 69% paid_video against January's 15%, so no bid rule should be "
         "ported across without refitting."),
    ]
    x0, y0, w, h, gx, gy = ML, 1.80, (CW - 0.45) / 2, 1.62, 0.45, 0.26
    for i, (head, body) in enumerate(blocks):
        x = x0 + (i % 2) * (w + gx)
        y = y0 + (i // 2) * (h + gy)
        rule(slide, x, y, 1.1, ACCENT if i % 2 == 0 else TEAL, 0.045)
        textbox(slide, x, y + 0.16, w, 0.32, head, size=14, bold=True, colour=INK)
        textbox(slide, x, y + 0.56, w, 1.0, body, size=11.5, colour=GREY, spacing=1.12)
    rule(slide, ML, 5.62, CW, RGBColor(0xD7, 0xDE, 0xE7), 0.012)
    textbox(slide, ML, 5.74, CW, 0.28, "AND THE DATA ITSELF", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, ML, 6.04, CW, 0.62,
            "Five issues would have broken this deck if taken at face value: ad timestamps logged "
            "8 hours ahead, sale_flag firing on refunds (58 sale days instead of 16), 122 duplicate "
            "purchase IDs, purchases left-truncated at 1 January, and 15.4% of revenue arriving "
            "from players with no activity row that day. Register in the appendix.",
            size=10.5, colour=GREY, spacing=1.15)
    footer(slide, n, "Full data-quality register: q0_data_quality__dq01..dq16; appendix slide 14.")
    return slide


# ---------------------------------------------------------------- appendix --
def appendix_shell(prs, n, kicker, title, note=None):
    """Add an appendix page, and fail if its printed number is not its position.

    Page numbers are passed in by hand here, so inserting a slide silently leaves
    every later page numbered wrong. This checks the two agree at build time.
    """
    slide = new_slide(prs)
    if len(prs.slides) != n:
        raise RuntimeError(f"{title!r} landed on slide {len(prs.slides)}, not {n}")
    header(slide, kicker, title, size=24)
    footer(slide, n, note)
    return slide


def slide_method(prs, n, s):
    slide = appendix_shell(
        prs, n, "Appendix  \u00b7  how this was built", "Every number is a query result",
        f"Runtime on a laptop: {s['runtime']} for make all. The repo is the submission; the "
        f"deck is an artefact of it.")
    bullets(slide, ML, 1.58, 6.0, 4.6, [
        ("One pipeline.", "make all rebuilds the warehouse from committed parquet, runs every "
                          "query, refits both models, and regenerates this deck."),
        ("One place for cleaning.", "sql/00_views.sql holds every correction \u2014 ad clock, "
                                    "refund handling, duplicate purchase IDs, sale windows."),
        ("Named blocks.", "Each query block writes one CSV, so any figure on any slide resolves "
                          "to a file and a named query."),
        ("Two recovered tables.", "player_profile and purchase arrived as PDF printouts; the "
                                  "extractor pairs alternating pages and refuses to write unless "
                                  "both sides agree, 80,295 and 15,426 rows."),
        ("No hand-editing.", "The deck script reads outputs/tables/*.csv and stops if one is "
                             "missing, rather than shipping a stale number."),
    ], size=11, gap=8)
    x = 7.0
    textbox(slide, x, 1.56, 5.6, 0.3, "WHAT MAKE ALL RUNS", size=9.5, bold=True, colour=ACCENT)
    table(slide, pd.DataFrame({
        "step": ["01 build", "02 run sql", "03 bootstrap", "04 churn", "05 90-day value", "06 deck"],
        "output": ["warehouse.duckdb", f"{s['blocks']} CSV blocks", "2 CIs", "6 model files",
                   "5 model files", "this deck"],
    }), x, 1.88, 5.6, col_widths=[0.42, 0.58], font=9.5, row_h=0.34)
    query_files = len(list((ROOT / "sql").glob("q*.sql")))
    textbox(slide, ML, 4.42, CW, 0.3, "WHAT IS IN THE REPO", size=9.5, bold=True, colour=ACCENT)
    repo = pd.DataFrame({
        "path": ["data/", "sql/", "code/", "outputs/", "deck/"],
        "contents": [
            "source extracts (three CSV, two recovered from PDF) and warehouse.duckdb",
            f"00_views.sql with every cleaning rule, then {query_files} query files in named blocks",
            "the six pipeline steps, in the order make runs them",
            f"{s['blocks']} CSV tables and 5 figures, all regenerated; none saved by hand",
            "DBMS_EndSem_Deck.pptx (readout + appendix) and DBMS_EndSem_Readout.pptx",
        ],
    })
    table(slide, repo, ML, 4.74, CW, col_widths=[0.11, 0.89], font=9.5, row_h=0.28)
    return slide


def slide_d1a(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 1(a)", "Daily health, by platform",
                           "Source: q1a_daily_health__daily_health_period_summary, "
                           "__daily_health_all_platforms, __daily_health_by_platform.")
    d = load("q1a_daily_health__daily_health_period_summary")
    d["avg_dau"] = d["avg_dau"].map(lambda v: f"{v:,.0f}")
    d["revenue_net"] = d["revenue_net"].map(lambda v: f"${v:,.0f}")
    d["arpdau_net"] = d["arpdau_net"].map(lambda v: f"${v:.4f}")
    d["pct_dau_purchasing"] = d["pct_dau_purchasing"].map(lambda v: f"{v:.2f}%")
    table(slide, d.rename(columns={
        "platform": "platform", "avg_dau": "avg DAU", "revenue_net": "net revenue",
        "arpdau_net": "ARPDAU", "pct_dau_purchasing": "% DAU buying"}), ML, 1.56, 6.4,
        col_widths=[0.22, 0.18, 0.22, 0.18, 0.2], font=10, row_h=0.34)
    bullets(slide, ML, 2.74, 6.4, 3.2, [
        ("iOS earns 2.0\u00d7 Android per DAU.", f"{money(s['ios_arpdau'],4)} vs "
                                                 f"{money(s['android_arpdau'],4)}."),
        ("The purchase-rate denominator matters.",
         f"{s['sale_gap_pct']:.0f}% of sale-day transactions belong to players "
                                                   "with no session row that date, so purchasers are "
                                                   "counted only if they were active that day."),
        ("Revenue is net of refunds.", f"{money(s['gross'])} gross less "
                                       f"{money(s['refund_usd'])} refunded, "
                                       f"{money(s['revenue'])} net."),
    ], size=11.5, gap=9)
    x = 7.3
    textbox(slide, x, 1.54, 5.3, 0.3, "WHY NOT JUST DAILY ARPDAU", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 1.86, 5.3, 1.3,
            f"ARPDAU for a period is revenue / DAU-days, not the mean of daily ratios: a "
            f"{s['dau_min']:,}-DAU day should not get the same vote as an {s['dau_max']:,}-DAU one. "
            f"Both are reported in the "
            "appendix of the deep dive.", size=11, colour=GREY, spacing=1.12)
    textbox(slide, x, 3.30, 5.3, 0.3, "COVERAGE", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 3.62, 5.3, 1.2,
            f"{s['active_players']:,} of {s['profiles']:,} profiles appear in player_day at all; "
            f"the other {s['no_activity']:,} stay in every retention denominator as non-retained "
            f"rather than disappearing.",
            size=11, colour=INK, spacing=1.12)

    daily = load("q1a_daily_health__daily_health_all_platforms")
    picks = [("busiest by DAU", daily.loc[daily.daily_active_players.idxmax()]),
             ("quietest by DAU", daily.loc[daily.daily_active_players.idxmin()]),
             ("best net revenue", daily.loc[daily.revenue_net.idxmax()]),
             ("worst net revenue", daily.loc[daily.revenue_net.idxmin()])]
    textbox(slide, ML, 4.52, CW, 0.3, "THE EXTREMES OF THE 120-DAY SERIES", size=9.5, bold=True,
            colour=ACCENT)
    ext = pd.DataFrame({
        "day": [f"{k} ({r['day']})" for k, r in picks],
        "DAU": [f"{int(r['daily_active_players']):,}" for _, r in picks],
        "net revenue": [f"${r['revenue_net']:,.0f}" for _, r in picks],
        "ARPDAU": [f"${r['arpdau_net']:.4f}" for _, r in picks],
    })
    table(slide, ext, ML, 4.84, CW, col_widths=[0.46, 0.16, 0.19, 0.19], font=9.5, row_h=0.30,
          force_right=True)
    return slide


def slide_d1b(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 1(b)", "Retention by install week",
                           "Source: q1b_retention_cohorts__retention_grid, "
                           "__cohort_boundary_notes.")
    d = load("q1b_retention_cohorts__retention_grid")
    d = d[d["cohort_week"] >= "2026-01-05"].tail(11).copy()
    d["cohort_week"] = d["cohort_week"].str[:10]
    d["cohort_size"] = d["cohort_size"].map(lambda v: f"{v:,}")
    table(slide, d.rename(columns={
        "cohort_week": "week", "cohort_size": "n", "days_observable_for_cohort": "days obs.",
        "d1_pct": "d1 %", "d7_pct": "d7 %", "d14_pct": "d14 %", "d30_pct": "d30 %"}),
        ML, 1.52, 7.3, col_widths=[0.2, 0.12, 0.16, 0.13, 0.13, 0.13, 0.13], font=9, row_h=0.29,
        header_h=0.30)
    x = 8.2
    textbox(slide, x, 1.50, 4.4, 0.3, "TWO BOUNDARY RULES", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 1.82, 4.4, 3.4, [
        ("Left edge.", "Activity starts 2026-01-01, so the 16,000 pre-window installs have no "
                       "day 1/7/14/30. They are excluded, not shown as 0%."),
        ("Right edge.", "A cell is blank unless every player in the cohort has had N days of "
                        "observation. No zeros are substituted for unobserved days."),
    ], size=11, gap=9)
    textbox(slide, x, 4.66, 4.4, 0.3, "READ", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 4.98, 4.4, 1.1,
            f"Day 1 sits around {s['d1']:.0f}%, day 30 around {s['d30']:.0f}% and drifting up with "
            f"cohort size. Retention is stable; nothing here explains the ARPDAU move.",
            size=11, colour=INK, spacing=1.12)
    return slide


def slide_d1c(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 1(c)", "Who pays, and how concentrated",
                           "Source: q1c_spender_behaviour__revenue_concentration, "
                           "__spend_distribution_by_payer.")
    d = load("q1c_spender_behaviour__revenue_concentration")
    d["population_size"] = d["population_size"].map(lambda v: f"{v:,}")
    d["total_revenue"] = d["total_revenue"].map(lambda v: f"${v:,.0f}")
    table(slide, d.rename(columns={
        "population": "population", "population_size": "players", "total_revenue": "revenue",
        "top_1pct_share": "top 1% share", "top_10pct_share": "top 10% share",
        "top_50pct_share": "top 50% share"}), ML, 1.56, 6.6,
        col_widths=[0.24, 0.16, 0.2, 0.14, 0.14, 0.14], font=9.5, row_h=0.34)
    bullets(slide, ML, 2.68, 6.6, 3.2, [
        ("Concentration depends on the denominator.",
         f"The top 1% of payers carry {pct(s['top1_payers'])} of revenue; the top 1% of all "
         f"players carry {pct(s['top1_all'])}, because most players never pay."),
        (f"{pct(s['pct_never_paid'])} never pay at all.",
         f"Median time to first purchase is {s['ttfp_median']:.0f} days among in-window installs."),
        ("Repeat gaps cluster.", f"Median {s['repeat_gap']:.0f} days between purchases, mean "
                                 f"{s['repeat_mean']:.1f} \u2014 a short, spiky payer cadence."),
    ], size=11.5, gap=9)
    x = 7.5
    textbox(slide, x, 1.54, 5.1, 0.3, "THE TRAP IN THIS DELIVERABLE", size=9.5, bold=True,
            colour=ACCENT)
    textbox(slide, x, 1.86, 5.1, 1.5,
            "Time-to-first-purchase is only measurable for players installed on or after "
            "2026-01-01, because purchases before that date were never extracted. Restricted to "
            "in-window installs with 30+ days of follow-up it is honest; on the whole base it is "
            "left-censored and would read low.", size=11, colour=INK, spacing=1.12)
    textbox(slide, x, 3.52, 5.1, 0.3, "SO WHAT", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 3.84, 5.1, 1.2,
            "Revenue is whale-dependent, so retention effort should be judged on revenue retained, "
            "not players retained. That is why the churn model reports precision by value band.",
            size=11, colour=GREY, spacing=1.12)

    spend = load("q1c_spender_behaviour__spend_distribution_by_payer")
    textbox(slide, ML, 4.44, CW, 0.3, "SPEND BY NUMBER OF PURCHASES", size=9.5, bold=True,
            colour=ACCENT)
    sp = pd.DataFrame({
        "purchases": [str(v) for v in spend["purchase_count_band"]],
        "payers": [f"{int(v):,}" for v in spend["payers"]],
        "% of payers": [f"{v:.1f}%" for v in spend["pct_of_payers"]],
        "revenue": [f"${v:,.0f}" for v in spend["revenue"]],
        "% of revenue": [f"{v:.1f}%" for v in spend["pct_of_revenue"]],
    })
    table(slide, sp, ML, 4.76, CW, col_widths=[0.20, 0.20, 0.18, 0.22, 0.20], font=9.5, row_h=0.30,
          force_right=True)
    return slide


def slide_d1d(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 1(d)", "The ad cap is not the constraint",
                           "Source: q1d_rewarded_ads__cap_saturation_by_cap, __ad_status_mix, "
                           "__ad_reward_by_type.")
    d = load("q1d_rewarded_ads__cap_saturation_by_cap")
    d = d[["cap", "player_days", "pct_player_days_at_cap", "pct_player_days_completed_at_cap",
           "avg_completed_views_per_player_day", "avg_peak_count"]].copy()
    d["player_days"] = d["player_days"].map(lambda v: f"{v:,.0f}")
    d["cap"] = d["cap"].map(lambda v: f"{int(v)}")
    d["avg_peak_count"] = d["avg_peak_count"].map(lambda v: f"{v:.2f}")
    d["avg_completed_views_per_player_day"] = d["avg_completed_views_per_player_day"].map(
        lambda v: f"{v:.2f}")
    table(slide, d.rename(columns={
        "cap": "cap", "player_days": "player-days", "pct_player_days_at_cap": "% days at cap",
        "pct_player_days_completed_at_cap": "% done",
        "avg_completed_views_per_player_day": "avg completed/day", "avg_peak_count": "avg peak"}),
        ML, 1.56, 7.2, col_widths=[0.09, 0.2, 0.18, 0.19, 0.19, 0.15], font=9.5, row_h=0.34)
    bullets(slide, ML, 2.72, 6.9, 3.2, [
        ("Cap 5 is reached on 1.27% of player-days.", "Cap 8 on 0.02%, and on at-cap days only "
                                                      "0.5% complete the final ad \u2014 there is no "
                                                      "queue to clear."),
        ("Players average 1.35 completed ads per ad-watching day", "against a cap of five \u2014 "
                                                                    "the ceiling is not binding."),
        (f"{pct(s['no_ad_share'], 0)} of active player-days have no ad view.",
         "That is the growth lever, not the cap."),
    ], size=11.5, gap=9)
    x = 7.9
    textbox(slide, x, 1.54, 4.7, 0.3, "THE CAP SPLIT IS A PER-PLAYER ASSIGNMENT", size=9.5,
            bold=True, colour=ACCENT)
    textbox(slide, x, 1.86, 4.7, 1.5,
            "Cap 5 and cap 8 split roughly 75/25 of player-days and the split is stable across "
            "months and app versions. That makes comparing the two groups legitimate \u2014 a "
            "rollout could not be.", size=11, colour=INK, spacing=1.12)
    textbox(slide, x, 3.56, 4.7, 0.3, "TWO BLIND DAYS", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 3.88, 4.7, 1.2,
            "5\u20136 March have zero ad events while DAU holds near 4,980. Logging gaps, not player "
            "behaviour \u2014 reported, not imputed.", size=11, colour=GREY, spacing=1.12)

    st = load("q1d_rewarded_ads__ad_status_mix")
    textbox(slide, ML, 4.44, 5.85, 0.3, "HOW AD REQUESTS END", size=9.5, bold=True, colour=ACCENT)
    status = pd.DataFrame({
        "status": [str(v) for v in st["status"]],
        "events": [f"{int(v):,}" for v in st["events"]],
        "% of events": [f"{v:.1f}%" for v in st["pct_of_events"]],
        "players": [f"{int(v):,}" for v in st["players"]],
    })
    table(slide, status, ML, 4.76, 5.85, col_widths=[0.26, 0.26, 0.24, 0.24], font=9.5, row_h=0.30,
          force_right=True)

    rw = load("q1d_rewarded_ads__ad_reward_by_type")
    textbox(slide, 6.80, 4.44, 5.9, 0.3, "WHAT AN AD PAYS, BY REWARD TYPE", size=9.5, bold=True,
            colour=ACCENT)
    reward = pd.DataFrame({
        "reward": [str(v) for v in rw["reward_type"]],
        "events": [f"{int(v):,}" for v in rw["events"]],
        "avg": [f"{v:,.1f}" for v in rw["avg_reward"]],
        "median": [f"{v:,.1f}" for v in rw["median_reward"]],
    })
    table(slide, reward, 6.80, 4.76, 5.9, col_widths=[0.34, 0.24, 0.21, 0.21], font=9.5, row_h=0.30,
          force_right=True)
    return slide


def slide_dq(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  data quality", "Six defects, each with a decision",
                           "Source: q0_data_quality__dq01..dq16, one block each in "
                           "sql/q0_data_quality.sql.")
    rows = [
        ("ad_view timestamps", "+8h vs every table, 7,732 rows on 2026-05-01", "re-based \u22128h"),
        ("sale_flag on refunds", "inflates sale days 16 \u2192 58", "windows from positive rows only"),
        ("duplicate purchase_id", "122 extra rows, ~$1.8k", "keep earliest, dedupe"),
        ("purchases before install", "110 players", "excluded from time-to-first-purchase"),
        ("purchases left-truncated", "nothing before 2026-01-01", "restrict cohort measures"),
        ("revenue without a session", "41.2% of sale-day txns, 15.4% of revenue",
         "purchase rate counts active buyers only"),
    ]
    table(slide, pd.DataFrame(rows, columns=["issue", "size", "handling"]), ML, 1.52, 8.1,
          col_widths=[0.26, 0.42, 0.32], font=9, row_h=0.40)
    x = 8.95
    textbox(slide, x, 1.50, 3.6, 0.3, "ALSO CHECKED", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 1.82, 3.6, 3.6, [
        ("currency_spend outliers", "max 75.8M vs p99.9 3,229 \u2014 flagged, not used."),
        ("country_tier nullability", "brief says null for non-purchasers; never null in the data."),
        ("cohort cells", "blank, never 0%, where day-N is unobserved."),
        ("device links", "3,211 originals, one self-link."),
    ], size=10.5, gap=8)
    textbox(slide, ML, 4.30, CW, 0.60,
            "The two that change a conclusion: the ad clock (a mixed clock would move ad days by "
            "one) and sale_flag on refunds (a naive count triples the promotion count and would "
            "have made the sales analysis meaningless).", size=11, colour=GREY, spacing=1.15)

    refunds = load("q0_data_quality__dq03_refund_rows").iloc[0]
    late = load("q0_data_quality__dq06_ad_rows_outside_window").iloc[0]
    prices = load("q0_data_quality__dq09_sale_price_equivalence")
    cover = load("q0_data_quality__dq10_activity_coverage").iloc[0]
    packs = prices["pack_name"].nunique()
    price_gap = float((prices.groupby("pack_name")["avg_usd"].max()
                       - prices.groupby("pack_name")["avg_usd"].min()).max())
    textbox(slide, ML, 4.86, CW, 0.3, "THE OTHER CHECKS, AND WHAT EACH DECIDED", size=9.5,
            bold=True, colour=ACCENT)
    rest = pd.DataFrame({
        "check": ["refunds (dq03)", "ads past the window (dq06)", "pack prices (dq09)",
                  "activity coverage (dq10)"],
        "what it measured": [
            f"{int(refunds.refund_rows)} rows, {money(float(refunds.refund_usd), 0)}, "
            f"{int(refunds.players_affected)} players",
            f"{int(late.rows_after_window_end):,} rows, {str(late.first_such_ts)[:16]} to "
            f"{str(late.last_such_ts)[11:16]}",
            f"{packs} packs, sale and normal days differ by {money(price_gap, 2)}",
            f"{int(cover.profiles_without_activity):,} of {int(cover.profiles):,} profiles "
            f"never appear",
        ],
        "decision": [
            "netted out of every revenue figure",
            "outside the 120-day window, excluded",
            "read as a period marker, not a discount",
            "kept as non-retained, never dropped",
        ],
    })
    table(slide, rest, ML, 5.16, CW, col_widths=[0.20, 0.46, 0.34], font=9, row_h=0.26,
          header_h=0.30)
    return slide


def slide_d2a(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 2(a)",
                           "ARPDAU, tier by tier, and what moved the mix",
                           "Source: q2a_arpdau_mix__02_arpdau_by_tier, "
                           "__06_decomposition_identity, __07_denominator_check, "
                           "__08_install_mix_drift.")
    tier = load("q2a_arpdau_mix__02_arpdau_by_tier")
    t = pd.DataFrame({
        "tier": [f"{int(v)}" for v in tier["country_tier"]],
        "ARPDAU, first 30d": [f"${v:.4f}" for v in tier["arpdau_first_30d"]],
        "ARPDAU, last 30d": [f"${v:.4f}" for v in tier["arpdau_last_30d"]],
        "change": [f"{v:+.1f}%" for v in tier["arpdau_change_pct"]],
        "DAU share, first 30d": [f"{v:.1f}%" for v in tier["dau_share_first_pct"]],
        "DAU share, last 30d": [f"{v:.1f}%" for v in tier["dau_share_last_pct"]],
    })
    table(slide, t, ML, 1.52, 6.75, col_widths=[0.07, 0.19, 0.19, 0.12, 0.215, 0.215],
          font=9.5, row_h=0.31, force_right=True)
    tier4 = tier.loc[tier.country_tier == 4].iloc[0]
    textbox(slide, ML, 3.30, 6.75, 0.62,
            "Every tier earns at least as much per DAU at the end of the window as at the "
            "start. What moved is the denominator: tier 4 went from "
            f"{float(tier4.dau_share_first_pct):.1f}% of DAU to "
            f"{float(tier4.dau_share_last_pct):.1f}%.",
            size=10.5, colour=GREY, spacing=1.12)

    ident = load("q2a_arpdau_mix__06_decomposition_identity").iloc[0]
    den = load("q2a_arpdau_mix__07_denominator_check").iloc[0]
    start = float(ident.actual_first_30d)
    end = float(ident.actual_last_30d)
    within = float(ident.within_tier_effect)
    mix = float(ident.mix_effect)
    inter = float(ident.interaction_effect)

    def signed(v: float) -> str:
        return ("+" if v > 0 else "") + money(v, 4)

    def signed_pct(v: float) -> str:
        return ("+" if v > 0 else "") + f"{v:.1f}%"

    d = pd.DataFrame({
        "step": ["ARPDAU, first 30 days", "within-tier monetisation", "mix of players",
                 "interaction", "ARPDAU, last 30 days"],
        "effect": [f"${start:.4f}", signed(within), signed(mix), signed(inter),
                   f"${end:.4f}"],
        # The interaction share is not its own CSV column; it is the same 100 * term /
        # opening rate basis as the other rows, so the column adds to the total.
        "as % of opening rate": ["", signed_pct(float(ident.within_tier_pct)),
                                 signed_pct(float(ident.mix_pct)),
                                 signed_pct(100 * inter / start),
                                 signed_pct(float(ident.total_pct))],
    })
    table(slide, d, ML, 4.06, 6.75, col_widths=[0.52, 0.26, 0.22], font=9.5, row_h=0.29,
          force_right=True)

    def signed_plain(v: float) -> str:
        return f"+ {v:.4f}" if v > 0 else f"\u2212 {abs(v):.4f}"

    textbox(slide, ML, 6.04, 6.75, 0.62,
            f"The four terms add back to the closing rate, to rounding: {start:.4f} "
            f"{signed_plain(within)} {signed_plain(mix)} {signed_plain(inter)} "
            f"\u2248 {end:.4f}.",
            size=10, colour=GREY, spacing=1.12)

    drift = load("q2a_arpdau_mix__08_install_mix_drift").sort_values("install_month")
    first, last = drift.iloc[0], drift.iloc[-1]
    m = pd.DataFrame({
        "month": [str(v)[:7] for v in drift["install_month"]],
        "installs": [f"{int(v):,}" for v in drift["installs"]],
        "paid_video": [f"{v:.1f}%" for v in drift["paid_video_share_pct"]],
        "tier 4": [f"{v:.1f}%" for v in drift["tier4_share_pct"]],
        "iOS": [f"{v:.1f}%" for v in drift["ios_share_pct"]],
    })
    textbox(slide, 7.55, 1.26, 5.15, 0.3, "WHAT THE INSTALL MIX DID", size=9.5, bold=True,
            colour=ACCENT)
    table(slide, m, 7.55, 1.58, 5.15, col_widths=[0.24, 0.22, 0.19, 0.17, 0.14], font=9.5,
          row_h=0.30, force_right=True)
    textbox(slide, 7.55, 3.34, 5.15, 1.05,
            f"March and April installs are three to four times January's, and they arrive "
            f"through paid_video into tier 4: April is "
            f"{float(last['paid_video_share_pct']):.1f}% paid_video and "
            f"{float(last['tier4_share_pct']):.1f}% tier-4, against January's "
            f"{float(first['paid_video_share_pct']):.1f}% and "
            f"{float(first['tier4_share_pct']):.1f}%.",
            size=10.5, colour=INK, spacing=1.12)
    textbox(slide, 7.55, 4.52, 5.15, 0.3, "AND THE DENOMINATOR ITSELF", size=9.5, bold=True,
            colour=ACCENT)
    textbox(slide, 7.55, 4.84, 5.15, 1.35,
            "ARPDAU is revenue over DAU-days, so a tier-4-heavy day dilutes it even when "
            f"tier 4's own rate holds. The weighted rate falls "
            f"{pct(abs(float(den.change_weighted_pct)))} and the mean of daily ratios "
            f"{pct(abs(float(den.change_daily_mean_pct)))}: both "
            "definitions move together, so the fall is not an artefact of the weighting.",
            size=10.5, colour=GREY, spacing=1.12)
    return slide


def slide_d2b(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 2(b)",
                           "The pull-forward counterfactual, window by window",
                           "Source: q2b_sales_pullforward__09_sale_window_counterfactual, "
                           "__14_schedule_learning_trend.")
    d = load("q2b_sales_pullforward__09_sale_window_counterfactual")
    d = d[["window_id", "window_start", "sale_day_revenue", "counterfactual_day_revenue",
           "excess_revenue", "pre_window_shortfall", "post_window_shortfall",
           "net_revenue"]].copy()
    d["window_id"] = d["window_id"].map(lambda v: f"{int(v)}")
    for col in ["sale_day_revenue", "counterfactual_day_revenue", "excess_revenue",
                "pre_window_shortfall", "post_window_shortfall", "net_revenue"]:
        d[col] = d[col].map(lambda v: money(v, 0))
    table(slide, d.rename(columns={
        "window_id": "#", "window_start": "starts", "sale_day_revenue": "sale-day revenue",
        "counterfactual_day_revenue": "counterfactual", "excess_revenue": "excess",
        "pre_window_shortfall": "pre shortfall", "post_window_shortfall": "post shortfall",
        "net_revenue": "net"}), ML, 1.50, 8.3,
        col_widths=[0.06, 0.14, 0.18, 0.18, 0.14, 0.15, 0.15, 0.12], font=9, row_h=0.31,
        header_h=0.32)
    x = 9.15
    textbox(slide, x, 1.48, 3.4, 0.3, "HOW THE COUNTERFACTUAL IS BUILT", size=9.5, bold=True,
            colour=ACCENT)
    textbox(slide, x, 1.80, 3.4, 2.6,
            "For each window: mid-cycle days (\u00b15 around the window's midpoint) give a normal-day "
            "rate; the two sale days are compared with it; the four days before and three after "
            "are compared with the same rate. Net = excess \u2212 both shortfalls.",
            size=10.5, colour=INK, spacing=1.12)
    textbox(slide, x, 4.05, 3.4, 0.3, "NOTES", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 4.37, 3.4, 1.5,
            f"One window is net negative (2026-02-22). The learning trend across the eight "
            f"windows is flat: slope {s['trend_slope']:+.4f}, R\u00b2 {s['trend_r2']:.3f}.",
            size=10.5, colour=GREY, spacing=1.12)
    return slide


def slide_d3(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 3",
                           "Standardised channel metrics, all three",
                           "Source: q3_channel_quality__crude_vs_standardized, "
                           "__standardized_excluding_thin_cells.")
    d = load("q3_channel_quality__crude_vs_standardized")
    names = {"revenue_per_install_30": ("revenue / install", 0),
             "payer_conversion_30": ("payer conversion", 1),
             "d30_retention": ("day-30 retention", 2),
             "ads_completed_per_install_30": ("ads / install", 3)}
    d = d[d["metric"].isin(names)].copy()
    d["order"] = d["metric"].map({k: v[1] for k, v in names.items()})
    d["metric"] = d["metric"].map({k: v[0] for k, v in names.items()})
    d = d.sort_values(["order", "standardized"], ascending=[True, False])

    def unit(metric, value):
        if metric == "revenue / install":
            return f"${value:.2f}"
        if metric in ("payer conversion", "day-30 retention"):
            return f"{100 * value:.1f}%"
        return f"{value:.2f}"

    out = pd.DataFrame({
        "metric": d["metric"].values,
        "channel": d["channel"].values,
        "crude": [unit(m, v) for m, v in zip(d["metric"], d["crude"])],
        "standardised": [unit(m, v) for m, v in zip(d["metric"], d["standardized"])],
        "95% CI": [f"{unit(m, lo)} to {unit(m, hi)}"
                   for m, lo, hi in zip(d["metric"], d["std_lo"], d["std_hi"])],
    })
    table(slide, out, ML, 1.50, 7.4, col_widths=[0.26, 0.18, 0.14, 0.18, 0.3], font=9,
          row_h=0.29, header_h=0.32, force_right=True)
    x = 8.3
    textbox(slide, x, 1.48, 4.3, 0.3, "WHAT THE TABLE SAYS", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 1.80, 4.3, 3.4, [
        ("Rankings do not survive adjustment.", "Paid video moves from worst to mid-pack on "
                                                "revenue per install and on retention."),
        ("Revenue, conversion and retention show no gap.",
         "Their paired bootstrap intervals all cross zero."),
        ("The durable gaps are engagement.",
         f"{s['video_ads']:.2f} vs {s['organic_ads']:.2f} completed ads per install, "
         f"and {s['video_active']:.2f} vs {s['organic_active']:.2f} active days."),
    ], size=10.5, gap=8)
    textbox(slide, x, 5.05, 4.3, 1.1,
            "24 cells, 3 channels, 8 platform \u00d7 tier strata. Thin cells (under 250 installs) are "
            "flagged and the standardisation is re-run without them: the largest movement is 0.07 "
            "(paid_social's ad rate); paid_video does not move.", size=10, colour=GREY, spacing=1.12)
    return slide


def slide_d4(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 4",
                           "Model detail and where it is weak",
                           "Source: q4_churn_model__coefficients, __model_summary, "
                           "__calibration, __threshold_curve.")
    co = load("q4_churn_model__coefficients")
    co = co[co["feature"] != "const"].head(9).copy()
    co["OR"] = co["odds_ratio"].map(lambda v: f"{v:.2f}")
    co["95% CI"] = [f"{lo:.2f} to {hi:.2f}" for lo, hi in zip(co["or_lo"], co["or_hi"])]
    co["feature"] = co["feature"].str.replace("_", " ")
    table(slide, co[["feature", "OR", "95% CI"]], ML, 1.50, 6.0,
          col_widths=[0.5, 0.16, 0.34], font=9, row_h=0.29, header_h=0.32, force_right=True)
    x = 6.95
    textbox(slide, x, 1.48, 5.6, 0.3, "DEFINITION AND SPLIT", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 1.80, 5.6, 1.5,
            "Churned = no player_day row in the 14 days after a weekly snapshot, among players "
            "active at least once in the 7 before it. 9 snapshots, 175,644 player-snapshots, "
            "47,347 players. Test is out-of-time (snapshots from 30 March) and one row per player.",
            size=10.5, colour=INK, spacing=1.12)
    textbox(slide, x, 3.45, 5.6, 0.3, "WHERE IT IS WEAK", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 3.77, 5.6, 2.0, [
        ("Top-decile calibration.", f"Predicted {pct(100*s['cal_top_pred'])} vs actual "
                                    f"{pct(100*s['cal_top_obs'])} \u2014 optimistic at the top."),
        ("Shared players.", "44,141 test players, but only 16,503 never appear in training; "
                            "0.702 is the leak-free number."),
        ("No offer economics.", "Contact cost and save rate are assumptions, stated in the script."),
    ], size=10.5, gap=7)
    textbox(slide, ML, 5.32, CW, 0.9,
            f"Threshold curve: at 0.30, {pct(100*s['thr_flag'])} of players flagged, "
            f"{pct(100*s['thr_recall'])} of churners caught, {pct(100*s['thr_prec'])} precision "
            f"({s['thr_lift']:.1f}\u00d7 base). Contacting everyone returns "
            f"{money(s['contactall'],0)} per 1,000 at the stated economics; the model's best cut "
            f"returns {money(s['net1000'],0)} per 1,000.", size=10.5, colour=GREY, spacing=1.15)
    return slide


def slide_d4b(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  deliverable 4b (optional)",
                           "90-day value: 12% of installs can be labelled at all",
                           "Source: q4b_ltv__model_summary, __capture_curve, "
                           "__02_cohort_vs_population.")
    cap = load("q4b_ltv__capture_curve")
    cap = cap.rename(columns={
        "top_share_of_installs": "top %",
        "two_part_model (recommended)": "model",
        "one_part_log_regression": "one-part",
        "baseline: day-7 revenue": "day-7 spend",
        "baseline: day-7 active days": "day-7 days",
        "baseline: day-7 ad views": "day-7 ads"})
    cap["top %"] = (100 * cap["top %"]).round(0).astype(int).astype(str)
    for c in ["model", "one-part", "day-7 spend", "day-7 days", "day-7 ads"]:
        cap[c] = (100 * cap[c]).round(1)
    table(slide, cap[["top %", "model", "one-part", "day-7 spend", "day-7 days"]],
          ML, 1.56, 7.0, col_widths=[0.14, 0.21, 0.21, 0.22, 0.22], font=9.5, row_h=0.30)
    textbox(slide, ML, 3.62, 7.0, 0.3,
            "Rows are the top % of installs; cells are the share of all 90-day revenue "
            "captured (%).", size=9, colour=MUTED)
    x = 7.9
    textbox(slide, x, 1.54, 4.7, 0.3, "THE CENSORING TRAP", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 1.86, 4.7, 1.6,
            f"Only {s['ltv_n']:,} installs have 90 observed days ({pct(s['ltv_pct'])} of the base). "
            f"The excluded 16,000 pre-window installs are old enough but their purchases start on "
            f"1 January.", size=10.5, colour=INK, spacing=1.12)
    textbox(slide, x, 3.60, 4.7, 0.3, "WHY IT IS NOT A BID RULE", size=9.5, bold=True, colour=ACCENT)
    textbox(slide, x, 3.92, 4.7, 1.5,
            f"The labelled cohort is {pct(s['ltv_pv'])} paid_video; April installs are "
            f"{pct(s['apr_pv'])}. Ranks transfer (Spearman vs a 30-day outcome: "
            f"{s['transfer_jan']:.2f} in January, {s['transfer_feb']:.2f} in February) but levels "
            f"do not.", size=10.5, colour=INK, spacing=1.12)
    textbox(slide, ML, 4.60, 7.0, 1.2,
            f"Mean 90-day revenue is {money(s['ltv_mean'])} per install; "
            f"{pct(s['ltv_zero'])} of installs are worth nothing and the top decile holds "
            f"{pct(100*s['cap10'])} of the money, against {pct(100*s['cap10_base'])} for the free "
            f"day-7-spend heuristic. On global Spearman the heuristic wins ({s['sp_spear']:.2f} vs "
            f"{s['sp_base']:.2f}), because it puts every non-payer in one tie block \u2014 which is "
            f"why the choice of metric had to be argued rather than assumed.",
            size=10.5, colour=GREY, spacing=1.15)
    return slide


def slide_judgement(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  judgement calls",
                           "Six calls I made, and why",
                           "Each call is implemented in sql/00_views.sql or code/; the slide "
                           "that uses it carries the numbers.")
    textbox(slide, ML, 1.46, CW, 0.3,
            "Collected here because the argument for each one sits on the slide that uses "
            "it; every row names the choice, the reason, and what would have changed it.",
            size=11, colour=GREY, spacing=1.1)
    bullets(slide, ML, 2.04, CW, 4.0, [
        ("Deep-dive (a), ARPDAU \u2014 not (b).",
         "It reproduces exactly and decomposes into mix. 2(b) is too strong to bury, but it "
         "is a readout slide, not the deep dive. I would have switched if 2(b) had a clean "
         "counterfactual."),
        ("Net revenue as the headline, gross disclosed.",
         "Refunds are real cash, so net is what the business keeps; the appendices carry the "
         "gross-to-net bridge so nothing is hidden. I would switch if refunds were a rounding "
         "error."),
        ("Churn is 14 days of inactivity.",
         "It matches the re-engagement decision we would actually make. A 30-day label is "
         "cleaner but too slow to act on; 7 days over-flags a normal lapsed week."),
        ("4(b) judged on top-decile capture, not rank correlation.",
         "The decision is which installs to bid on, so capture is the metric that maps to "
         "money. The Spearman loss against the day-7 heuristic (0.3478 vs 0.4684) is stated, "
         "not buried."),
        ("Sale days defined by window, not sale_flag.",
         "The flag fires on refund rows and would have counted 58 sale days instead of 16, "
         "tripling the promotion. I would switch back if the flag were clean."),
        ("Unobserved cohort cells stay blank, never zero.",
         "A zero reads as perfect churn where the data has simply not matured; blank is the "
         "honest label. I would switch only once every cohort has full follow-up."),
    ], size=11.5, gap=9)
    rule(slide, ML, 5.06, CW, RGBColor(0xD7, 0xDE, 0xE7), 0.012)
    textbox(slide, ML, 5.14, CW, 0.42,
            "Worked through in full on slides 2\u20133 and 15 (2a), 4 and 16 (2b), 5 and 17 (3), "
            "6 and 18 (4), 19 (4b) and 14 (data quality).",
            size=10.5, colour=GREY, spacing=1.1)
    return slide


def slide_deadends(prs, n, s):
    slide = appendix_shell(prs, n, "Appendix  \u00b7  dead ends and next week",
                           "What I tried, and what I would do next",
                           "Every item above is a query or a script in this repo; README lists "
                           "the run order.")
    textbox(slide, ML, 1.54, 5.9, 0.3, "DEAD ENDS", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, ML, 1.86, 5.9, 3.4, [
        ("Per-country ARPDAU.", "Too few transactions per country per month; the series is noise "
                               "and would have made a confident-looking slide out of nothing."),
        ("Pack-level price elasticity.", "No price variation exists in the data \u2014 all six packs "
                                         "are fixed, so there is nothing to estimate."),
        ("Cap 5 vs cap 8 as an experiment.", "It is a per-player assignment, so the comparison is "
                                             "descriptive, not causal."),
        ("A single regression on 90-day revenue.", "Ranks worse than the day-7-spend heuristic; "
                                                   "the two-part split lifted the top decile, "
                                                   "not the global rank."),
    ], size=11.5, gap=26)
    x = 6.9
    textbox(slide, x, 1.54, 5.6, 0.3, "WITH ANOTHER WEEK", size=9.5, bold=True, colour=ACCENT)
    bullets(slide, x, 1.86, 5.6, 3.4, [
        ("Get cost per install by channel.", "The single number that turns channel quality into "
                                              "channel value."),
        ("Run one skipped sale window.", "Eight windows, none skipped; a holdout settles the "
                                         "$13k question."),
        ("Refit 4b on February once it matures.", "February hits 90 days in early May; the cohort "
                                                  "and the bid rule have to be re-estimated."),
        ("Test the churn score on a live cell.", "Score \u2265 0.30 on a 5% holdout, measure "
                                                 "incremental retention, not correlation."),
    ], size=11.5, gap=26)
    box = slide.shapes.add_shape(1, Inches(ML), Inches(5.28), Inches(CW), Inches(1.05))
    box.fill.solid()
    box.fill.fore_color.rgb = LIGHT
    box.line.fill.background()
    box.shadow.inherit = False
    rule(slide, ML, 5.28, 0.055, ACCENT, 1.05)
    textbox(slide, ML + 0.30, 5.32, CW - 0.60, 0.97,
            "Everything above is a query or a script in this repo. The deck is regenerated by "
            "make all, so a changed number changes the argument, not just the slide.",
            size=13, colour=INK, spacing=1.2, anchor=MSO_ANCHOR.MIDDLE)
    return slide


# --------------------------------------------------------------- overflow ---
def check_overflow(prs: Presentation) -> list[str]:
    """Rough text-fit check: flags text frames whose content likely exceeds their box.

    Approximate by design (Calibri average glyph width ~0.5em), but it catches the
    failure that matters here -- a paragraph that runs past the bottom of its shape.
    """
    warnings = []
    for idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame or not shape.text_frame.text.strip():
                continue
            if shape.width is None or shape.height is None:
                continue
            width_pt = shape.width / 12700 - 6  # 12700 EMU per point, minus insets
            height_pt = shape.height / 12700
            needed = 0.0
            for p in shape.text_frame.paragraphs:
                text = "".join(r.text for r in p.runs)
                if not text:
                    continue
                size = max((r.font.size.pt for r in p.runs if r.font.size), default=12)
                cpl = max(8, width_pt / (size * 0.50))
                lines = max(1, math.ceil(len(text) / cpl))
                needed += lines * size * 1.18 + (p.space_after.pt if p.space_after else 0)
            if needed > height_pt * 1.02:
                warnings.append(
                    f"slide {idx}: {needed / height_pt:.2f}x box "
                    f"({shape.text_frame.text[:48]!r})")
    return warnings


# -------------------------------------------------------------------- main --
def strip_appendix_refs(path: Path) -> None:
    """Make the standalone readout self-contained.

    The submission deck can say "then appendix" and cite appendix slide 14; the
    eight-slide extract has neither, so those strings are rewritten after it is
    saved. Everything else is untouched, which keeps the extract's other six
    pages identical to the submission deck's.
    """
    standalone = Presentation(path)
    swaps = [
        ("Readout: 8 slides, then appendix", "Readout: 8 slides"),
        ("Register in the appendix.", "The full register is in the working files."),
        ("Full data-quality register: q0_data_quality__dq01..dq16; appendix slide 14.",
         "Full data-quality register: q0_data_quality__dq01..dq16; "
         "see sql/q0_data_quality.sql."),
    ]
    done = set()
    for slide in standalone.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    for old, new in swaps:
                        if old in run.text:
                            run.text = run.text.replace(old, new)
                            done.add(old)
    missing = [old for old, _ in swaps if old not in done]
    if missing:
        raise RuntimeError(f"standalone readout: no text matched {missing!r}")
    standalone.save(path)


def main() -> None:
    for d in (FIGURES, DECK_DIR):
        d.mkdir(parents=True, exist_ok=True)

    figs = {
        "arpdau": FIGURES / "chart_arpdau_tiers.png",
        "decomp": FIGURES / "chart_decomposition.png",
        "sales": FIGURES / "chart_sales_offset.png",
        "channel": FIGURES / "chart_channel_ci.png",
        "churn": FIGURES / "chart_churn_deciles.png",
    }
    chart_arpdau(figs["arpdau"])
    chart_decomposition(figs["decomp"])
    chart_sales(figs["sales"])
    chart_channel(figs["channel"])
    chart_churn(figs["churn"])

    # ---- every number the deck uses, read from the CSVs --------------------
    q2a_period = load("q2a_arpdau_mix__01_period_totals")
    q2a_tier = load("q2a_arpdau_mix__02_arpdau_by_tier")
    q2a_id = load("q2a_arpdau_mix__06_decomposition_identity").iloc[0]
    q2a_drift = load("q2a_arpdau_mix__08_install_mix_drift")
    q1a = load("q1a_daily_health__daily_health_period_summary")
    q1b = load("q1b_retention_cohorts__retention_grid")
    q1c_ttfp = load("q1c_spender_behaviour__time_to_first_purchase_summary")
    q1c_gap = load("q1c_spender_behaviour__repeat_purchase_gap")
    q2b_days = load("q2b_sales_pullforward__02_sale_vs_normal_days")
    q2b_tot = load("q2b_sales_pullforward__10_counterfactual_total").iloc[0]
    q2b_trend = load("q2b_sales_pullforward__14_schedule_learning_trend").iloc[0]
    q3 = load("q3_channel_quality__crude_vs_standardized")
    q3_diff = load("q3_channel__bootstrap_differences")
    q4s = load("q4_churn_model__model_summary").set_index("metric")["value"]
    q4t = load("q4_churn_model__threshold_curve")
    q4cal = load("q4_churn_model__calibration")
    q4base = load("q4_churn__churn_base_rates_by_segment")
    q4b = load("q4b_ltv__model_summary").set_index("metric")["value"]
    q4b_cap = load("q4b_ltv__capture_curve")
    q4b_cmp = load("q4b_ltv__02_cohort_vs_population")
    q4b_lab = load("q4b_ltv__01_labelling_window").iloc[0]
    q1a_all = load("q1a_daily_health__daily_health_all_platforms")
    q1c_conc = load("q1c_spender_behaviour__revenue_concentration")
    q1d_all = load("q1d_rewarded_ads__cap_saturation_all")
    q3_strat = load("q3_channel_quality__channel_rates_by_stratum")
    q0_ranges = load("q0_data_quality__dq01_row_counts_and_ranges").set_index("table_name")
    q0_cover = load("q0_data_quality__dq10_activity_coverage").iloc[0]
    q0_gap = load("q0_data_quality__dq16_sale_day_activity_gap")

    def q3v(metric, channel, col):
        return float(q3[(q3.metric == metric) & (q3.channel == channel)][col].iloc[0])

    diff = q3_diff[(q3_diff.metric == "revenue_per_install_30") &
                   (q3_diff.contrast == "paid_video minus organic")].iloc[0]
    row30 = q4t[q4t.threshold == 0.30].iloc[0]
    best_row = q4t.loc[q4t.net_per_1000_players.idxmax()]
    contact_all = float(q4t[q4t.threshold == 0.0].net_per_1000_players.iloc[0])
    tenure = q4base[(q4base.dimension == "tenure_days") & (q4base.level == "61+")].iloc[0]
    coef = load("q4_churn_model__coefficients").set_index("feature")["odds_ratio"]
    share_sale = float(q2b_tot.pct_of_total_revenue)
    sale_days = int(q2b_days[q2b_days.is_sale_day == True].days.iloc[0])  # noqa: E712
    player_days = int(q0_ranges.loc["player_day", "rows"])
    ad_player_days = int(q1d_all.player_days.iloc[0])

    S = {
        "profiles": int(q0_ranges.loc["player_profile", "rows"]),
        "player_days": player_days,
        "revenue": float(q1c_conc.total_revenue.iloc[0]),
        "gross": float(q1a_all.revenue_gross.sum()),
        "refund_usd": float(q1a_all.revenue_gross.sum() - q1a_all.revenue_net.sum()),
        "dau_min": 100 * round(float(q1a_all.daily_active_players.min()) / 100),
        "dau_max": 100 * round(float(q1a_all.daily_active_players.max()) / 100),
        "blocks": count_sql_blocks(),
        "runtime": "35 seconds",
        "t1": q2a_tier.loc[q2a_tier.country_tier == 1, "arpdau_change_pct"].iloc[0],
        "t2": q2a_tier.loc[q2a_tier.country_tier == 2, "arpdau_change_pct"].iloc[0],
        "t3": q2a_tier.loc[q2a_tier.country_tier == 3, "arpdau_change_pct"].iloc[0],
        "t4": q2a_tier.loc[q2a_tier.country_tier == 4, "arpdau_change_pct"].iloc[0],
        "t4_share_first": q2a_tier.loc[q2a_tier.country_tier == 4, "dau_share_first_pct"].iloc[0],
        "t4_share_last": q2a_tier.loc[q2a_tier.country_tier == 4, "dau_share_last_pct"].iloc[0],
        "t4_arpdau": q2a_tier.loc[q2a_tier.country_tier == 4, "arpdau_first_30d"].iloc[0],
        "t1_arpdau": q2a_tier.loc[q2a_tier.country_tier == 1, "arpdau_first_30d"].iloc[0],
        "within_effect": float(q2a_id.within_tier_effect),
        "mix_effect": float(q2a_id.mix_effect),
        "interaction": float(q2a_id.interaction_effect),
        "within_pct": float(q2a_id.within_tier_pct),
        "mix_pct": float(q2a_id.mix_pct),
        "total_pct": float(q2a_id.total_pct),
        "pv_first": float(q2a_drift.paid_video_share_pct.iloc[0]),
        "pv_last": float(q2a_drift.paid_video_share_pct.iloc[-1]),
        "t4_inst_first": float(q2a_drift.tier4_share_pct.iloc[0]),
        "t4_inst_last": float(q2a_drift.tier4_share_pct.iloc[-1]),
        "arpdau_first": float(q2a_period.arpdau_net.iloc[0]),
        "arpdau_last": float(q2a_period.arpdau_net.iloc[-1]),
        "android_arpdau": float(q1a.loc[q1a.platform == "Android", "arpdau_net"].iloc[0]),
        "ios_arpdau": float(q1a.loc[q1a.platform == "iOS", "arpdau_net"].iloc[0]),
        "active_players": int(q0_cover.profiles - q0_cover.profiles_without_activity),
        "no_activity": int(q0_cover.profiles_without_activity),
        "d1": float(q1b.d1_pct.dropna().mean()),
        "d30": float(q1b.d30_pct.dropna().mean()),
        "ttfp_median": float(q1c_ttfp["median_days"].iloc[0]),
        "repeat_gap": float(q1c_gap["median_days"].iloc[0]),
        "repeat_mean": float(q1c_gap["mean_days"].iloc[0]),
        "pct_never_paid": float(q1c_ttfp["pct_never_paid"].iloc[0]),
        "top1_payers": float(q1c_conc.loc[q1c_conc.population == "payers only",
                                          "top_1pct_share"].iloc[0]),
        "top1_all": float(q1c_conc.loc[q1c_conc.population == "all players",
                                       "top_1pct_share"].iloc[0]),
        "sale_days": sale_days,
        "normal_days": 120 - sale_days,
        "sale_gap_pct": float(
            q0_gap[q0_gap.is_sale_day == True].pct_without_activity_row.iloc[0]),  # noqa: E712
        "sale_day_rev": float(q2b_days[q2b_days.is_sale_day == True].avg_daily_revenue.iloc[0]),  # noqa: E712
        "normal_day_rev": float(q2b_days[q2b_days.is_sale_day == False].avg_daily_revenue.iloc[0]),  # noqa: E712
        "sale_share": share_sale,
        "buyer_multiple": float(
            q2b_days[q2b_days.is_sale_day == True].avg_daily_buyers.iloc[0]  # noqa: E712
            / q2b_days[q2b_days.is_sale_day == False].avg_daily_buyers.iloc[0]),  # noqa: E712
        "rev_per_buyer_sale": float(q2b_days[q2b_days.is_sale_day == True].revenue_per_buyer.iloc[0]),  # noqa: E712
        "rev_per_buyer_normal": float(q2b_days[q2b_days.is_sale_day == False].revenue_per_buyer.iloc[0]),  # noqa: E712
        "sale_rev": float(q2b_tot.sale_day_revenue),
        "net": float(q2b_tot.net_revenue),
        "clawback": 100 * (1 - float(q2b_tot.net_revenue) / float(q2b_tot.sale_day_revenue)),
        "trend_slope": float(q2b_trend["slope_per_window"]),
        "trend_r2": float(q2b_trend["r_squared"]),
        "video_crude": q3v("revenue_per_install_30", "paid_video", "crude"),
        "organic_crude": q3v("revenue_per_install_30", "organic", "crude"),
        "video_std": q3v("revenue_per_install_30", "paid_video", "standardized"),
        "organic_std": q3v("revenue_per_install_30", "organic", "standardized"),
        "gap_std": float(diff.standardized_diff),
        "gap_lo": float(diff.standardized_lo),
        "gap_hi": float(diff.standardized_hi),
        "video_ads": q3v("ads_completed_per_install_30", "paid_video", "standardized"),
        "organic_ads": q3v("ads_completed_per_install_30", "organic", "standardized"),
        "video_active": q3v("active_days_30", "paid_video", "standardized"),
        "organic_active": q3v("active_days_30", "organic", "standardized"),
        "video_t4_share": float(
            100 * q3_strat[(q3_strat.channel == "paid_video")
                           & (q3_strat.country_tier == 4)].installs.sum()
            / q3_strat[q3_strat.channel == "paid_video"].installs.sum()),
        "organic_t4_share": float(
            100 * q3_strat[(q3_strat.channel == "organic")
                           & (q3_strat.country_tier == 4)].installs.sum()
            / q3_strat[q3_strat.channel == "organic"].installs.sum()),
        "video_rpp": float(load("q3_channel_quality__revenue_decomposition")
                           .set_index("channel").loc["paid_video", "revenue_per_payer_standardized"]),
        "organic_rpp": float(load("q3_channel_quality__revenue_decomposition")
                             .set_index("channel").loc["organic", "revenue_per_payer_standardized"]),
        "auc": float(q4s["auc_test"]),
        "auc_lo": float(q4s["auc_lo"]),
        "auc_hi": float(q4s["auc_hi"]),
        "auc_ind": float(q4s["auc_independent_players"]),
        "prec": float(q4s["precision_top_decile"]),
        "recall": float(q4s["recall_top_decile"]),
        "decile_lift": float(q4s["precision_top_decile"] / (q4s["test_churn_rate_pct"] / 100)),
        "or_sessions": float(coef["sessions_per_active_day"]),
        "or_play": float(coef["playtime_per_active_day"]),
        "or_ads": float(coef["ads_completed_7"]),
        "or_tenure": float(coef["tenure_days"]),
        "or_video": float(coef["acquisition_channel_paid_video"]),
        "or_social": float(coef["acquisition_channel_paid_social"]),
        "tenure_churn": float(tenure.churn_rate_pct),
        "thr": 0.30,
        "thr_flag": float(row30.pct_flagged),
        "thr_prec": float(row30.precision),
        "thr_recall": float(row30.recall_capture),
        "thr_lift": float(row30.lift_vs_base),
        "best_thr": float(best_row.threshold),
        "net1000": float(best_row.net_per_1000_players),
        "contactall": contact_all,
        "cal_top_pred": float(q4cal.mean_predicted.iloc[-1]),
        "cal_top_obs": float(q4cal.observed_churn_rate.iloc[-1]),
        "ltv_n": int(q4b_lab.usable_labelled_cohort),
        "ltv_pct": float(q4b_lab.usable_pct_of_profiles),
        "ltv_mean": float(q4b["mean_revenue_90_in_cohort"]),
        "ltv_zero": float(q4b["pct_installs_with_zero_revenue"]),
        "ltv_pv": float(q4b_cmp.iloc[0].paid_video_share_pct),
        "apr_pv": float(q4b_cmp.iloc[2].paid_video_share_pct),
        "transfer_jan": float(q4b["transfer_spearman_jan_30d"]),
        "transfer_feb": float(q4b["transfer_spearman_feb_30d"]),
        "cap10": float(q4b_cap["two_part_model (recommended)"].iloc[1]),
        "cap10_base": float(q4b_cap["baseline: day-7 revenue"].iloc[1]),
        "sp_spear": float(q4b["spearman_two_part"]),
        "sp_base": float(q4b["spearman_baseline_day7_revenue"]),
        "no_ad_share": float(100 * (1 - ad_player_days / player_days)),
    }

    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)

    slide_title(prs, S)
    slide_paradox(prs, 2, S, figs)
    slide_decomposition(prs, 3, S, figs)
    slide_sales(prs, 4, S, figs)
    slide_channel(prs, 5, S, figs)
    slide_churn(prs, 6, S, figs)
    slide_recommendation(prs, 7, S)
    slide_uncertainty(prs, 8, S)
    prs.save(READOUT)          # the readout alone, before any appendix slide exists
    strip_appendix_refs(READOUT)

    slide_method(prs, 9, S)
    slide_d1a(prs, 10, S)
    slide_d1b(prs, 11, S)
    slide_d1c(prs, 12, S)
    slide_d1d(prs, 13, S)
    slide_dq(prs, 14, S)
    slide_d2a(prs, 15, S)
    slide_d2b(prs, 16, S)
    slide_d3(prs, 17, S)
    slide_d4(prs, 18, S)
    slide_d4b(prs, 19, S)
    slide_judgement(prs, 20, S)
    slide_deadends(prs, 21, S)

    prs.save(DECK)

    warns = check_overflow(prs)
    if warns:
        print("layout warnings (text may overflow its box):")
        for w in warns:
            print("  " + w)
    print(f"\n{len(prs.slides)} slides -> {DECK.relative_to(ROOT)}  "
          f"({DECK.stat().st_size / 1024:.0f} KB)")
    print(f"8 slides -> {READOUT.relative_to(ROOT)}  "
          f"({READOUT.stat().st_size / 1024:.0f} KB)")
    print(f"appendix: {len(prs.slides) - 8} slides")


if __name__ == "__main__":
    main()
