"""Stratified bootstrap confidence intervals for the channel comparison (deliverable 3).

The SQL in sql/q3_channel_quality.sql gives point estimates and analytic intervals.
Those are fine for a mean of a well-behaved variable, but the two quantities the
deck actually needs are harder than that:

  * revenue per install is heavily skewed -- a handful of whales per cell carry a
    large share of the cell mean, which is exactly where a normal approximation
    is least trustworthy;
  * the interesting claim is not "channel A has rate x" but "channel A and channel
    B differ", and the interval on a difference of two independent intervals is
    much wider than the truth.

So this resamples players *within* each platform x country_tier cell, recomputes
the crude and the mix-standardised metric on every replicate, and derives:

  outputs/tables/q3_channel__bootstrap_standardized.csv   point + 95% interval per channel
  outputs/tables/q3_channel__bootstrap_differences.csv    paired difference between channels

Because each replicate reuses the same resampled indices for every channel, the
channel-to-channel difference is paired: the two channels are compared on the
same synthetic population, not on two independently perturbed ones. Resampling
within cells also respects the design -- a player belongs to exactly one cell,
so cells are independent and can be resampled separately.

Seeded, so the intervals are reproducible from a clean checkout.

Usage:
    python code/03_channel_ci.py [--reps 1000] [--seed 20261122]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.duckdb"
OUT = ROOT / "outputs" / "tables"

# metric name -> column in v_install_cohort
METRICS = {
    "d1_retention": "d1",
    "d7_retention": "d7",
    "d14_retention": "d14",
    "d30_retention": "d30",
    "payer_conversion_30": "payer_30",
    "revenue_per_install_30": "revenue_net_30",
    "active_days_30": "active_days_30",
    "ads_completed_per_install_30": "ads_completed_30",
}
CHANNELS = ["organic", "paid_social", "paid_video"]
CONTRASTS = [
    ("paid_video", "organic"),
    ("paid_social", "organic"),
    ("paid_video", "paid_social"),
]


def load() -> pd.DataFrame:
    if not DB.exists():
        sys.exit(f"ERROR: {DB} not found -- run code/01_build_warehouse.py first")
    con = duckdb.connect(str(DB), read_only=True)
    cols = ", ".join(sorted(set(METRICS.values())))
    df = con.execute(
        f"SELECT platform, country_tier, acquisition_channel AS channel, {cols} "
        "FROM v_install_cohort"
    ).df()
    con.close()
    for c in METRICS.values():
        if df[c].dtype == bool:
            df[c] = df[c].astype(float)
    df["stratum"] = df["platform"] + "|" + df["country_tier"].astype(str)
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261122)
    args = ap.parse_args()

    t0 = time.time()
    df = load()
    metrics = list(METRICS)
    cols = [METRICS[m] for m in metrics]
    k = len(metrics)
    n_total = len(df)

    strata = sorted(df["stratum"].unique())
    # weights: each cell's share of all installs in the mature cohort, i.e. the
    # common mix every channel is re-weighted to.
    weight = {s: (df["stratum"] == s).sum() / n_total for s in strata}

    arrays: dict[tuple[str, str], np.ndarray] = {}
    n_cell: dict[tuple[str, str], int] = {}
    for s in strata:
        block = df[df["stratum"] == s]
        for ch in CHANNELS:
            part = block[block["channel"] == ch]
            if len(part):
                arrays[(s, ch)] = np.ascontiguousarray(part[cols].to_numpy(dtype=float))
                n_cell[(s, ch)] = len(part)

    n_channel = {ch: sum(n for (s, c), n in n_cell.items() if c == ch) for ch in CHANNELS}

    # point estimates on the observed data
    crude_point, std_point = {}, {}
    for ch in CHANNELS:
        sub = df[df["channel"] == ch][cols].to_numpy(dtype=float)
        crude_point[ch] = sub.mean(axis=0)
        std_point[ch] = np.sum(
            [weight[s] * arrays[(s, ch)].mean(axis=0) for s in strata if (s, ch) in arrays],
            axis=0,
        )

    rng = np.random.default_rng(args.seed)
    crude_rep = {ch: np.empty((args.reps, k)) for ch in CHANNELS}
    std_rep = {ch: np.empty((args.reps, k)) for ch in CHANNELS}

    print(f"{n_total:,} installs in {len(strata)} cells x {len(CHANNELS)} channels, "
          f"{args.reps} bootstrap replicates ...")
    for r in range(args.reps):
        cell_mean = {}
        for key, X in arrays.items():
            idx = rng.integers(0, X.shape[0], X.shape[0])
            cell_mean[key] = X[idx].mean(axis=0)
        for ch in CHANNELS:
            crude_rep[ch][r] = sum(
                n_cell[(s, ch)] * cell_mean[(s, ch)] for s in strata if (s, ch) in arrays
            ) / n_channel[ch]
            std_rep[ch][r] = sum(
                weight[s] * cell_mean[(s, ch)] for s in strata if (s, ch) in arrays
            )

    def ci(rep: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        lo = np.percentile(rep, 2.5, axis=0)
        hi = np.percentile(rep, 97.5, axis=0)
        return lo, hi, rep.std(axis=0, ddof=1)

    rows = []
    for ch in CHANNELS:
        c_lo, c_hi, c_se = ci(crude_rep[ch])
        s_lo, s_hi, s_se = ci(std_rep[ch])
        for j, m in enumerate(metrics):
            rows.append({
                "channel": ch,
                "metric": m,
                "installs": n_channel[ch],
                "crude": crude_point[ch][j],
                "crude_lo": c_lo[j], "crude_hi": c_hi[j],
                "standardized": std_point[ch][j],
                "standardized_lo": s_lo[j], "standardized_hi": s_hi[j],
                "standardized_boot_se": s_se[j],
                "reps": args.reps,
            })
    std_df = pd.DataFrame(rows)

    diff_rows = []
    for a, b in CONTRASTS:
        for j, m in enumerate(metrics):
            d_crude = crude_rep[a][:, j] - crude_rep[b][:, j]
            d_std = std_rep[a][:, j] - std_rep[b][:, j]
            # two-sided bootstrap p: how often the difference crosses zero
            p_crude = 2 * min((d_crude <= 0).mean(), (d_crude >= 0).mean())
            p_std = 2 * min((d_std <= 0).mean(), (d_std >= 0).mean())
            diff_rows.append({
                "metric": m,
                "contrast": f"{a} minus {b}",
                "crude_diff": crude_point[a][j] - crude_point[b][j],
                "crude_lo": np.percentile(d_crude, 2.5),
                "crude_hi": np.percentile(d_crude, 97.5),
                "standardized_diff": std_point[a][j] - std_point[b][j],
                "standardized_lo": np.percentile(d_std, 2.5),
                "standardized_hi": np.percentile(d_std, 97.5),
                "p_crude": min(1.0, p_crude),
                "p_standardized": min(1.0, p_std),
                "excludes_zero_standardized": bool(
                    np.percentile(d_std, 2.5) > 0 or np.percentile(d_std, 97.5) < 0
                ),
                "reps": args.reps,
            })
    diff_df = pd.DataFrame(diff_rows)

    OUT.mkdir(parents=True, exist_ok=True)
    std_path = OUT / "q3_channel__bootstrap_standardized.csv"
    diff_path = OUT / "q3_channel__bootstrap_differences.csv"
    std_df.round(4).to_csv(std_path, index=False)
    diff_df.round(4).to_csv(diff_path, index=False)

    print(f"\n{'channel':<12}{'metric':<32}{'standardized':>14}{'95% interval':>26}")
    for _, row in std_df[std_df.metric.isin(
            ["d30_retention", "payer_conversion_30", "revenue_per_install_30"])].iterrows():
        print(f"{row.channel:<12}{row.metric:<32}{row.standardized:>14.4f}"
              f"{f'[{row.standardized_lo:.4f}, {row.standardized_hi:.4f}]':>26}")

    print(f"\n{'contrast':<28}{'metric':<32}{'diff':>9}{'95% interval':>24}{'p':>8}")
    for _, row in diff_df[diff_df.metric.isin(
            ["d30_retention", "payer_conversion_30", "revenue_per_install_30"])].iterrows():
        print(f"{row.contrast:<28}{row.metric:<32}{row.standardized_diff:>9.4f}"
              f"{f'[{row.standardized_lo:.4f}, {row.standardized_hi:.4f}]':>24}"
              f"{row.p_standardized:>8.3f}")

    print(f"\nwrote {std_path.name}, {diff_path.name} ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
