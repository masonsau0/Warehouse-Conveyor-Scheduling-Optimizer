"""Build a synthetic warehouse-tote dataset.

Generates a realistic order list — totes arriving on a 4-lane warehouse
conveyor system with stochastic release times and varying processing times
— and writes it to `conveyor_data.xlsx`.

Edit the constants below to scale the workload up or down or simulate
different demand patterns (uniform vs. bursty).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Workload size & shape — modelled as a "rush hour" where the lanes are
# congested enough that dispatching rules actually differ. Tune the constants
# below to simulate a calmer or busier shift.
NUM_TOTES = 80
NUM_LANES = 4
SEED = 42

# Processing-time parameters (seconds per tote, lognormal so a long-tail
# of slow totes shows up — e.g. heavy / fragile items needing extra handling).
P_TIME_MEAN = 60.0     # mean processing time (s)
P_TIME_SIGMA = 0.55    # lognormal sigma — higher → longer tail

# Release-time parameters: simulate a congested hour where 80 totes arrive
# over ~25 min (1500 s) — total workload >> arrival window, so lanes queue.
SHIFT_SECONDS = 1500
ARRIVAL_BURSTINESS = 1.5     # > 1 = bursty arrivals


def _lognormal_processing_times(n: int, rng: np.random.Generator) -> np.ndarray:
    mu = np.log(P_TIME_MEAN) - 0.5 * P_TIME_SIGMA ** 2
    return rng.lognormal(mean=mu, sigma=P_TIME_SIGMA, size=n)


def _bursty_release_times(n: int, total_seconds: int, burstiness: float,
                           rng: np.random.Generator) -> np.ndarray:
    # Sample inter-arrival times from a Gamma with shape = 1/burstiness; the
    # smaller the shape, the more clumped the arrivals.
    shape = 1.0 / burstiness
    raw = rng.gamma(shape=shape, scale=1.0, size=n)
    raw = np.cumsum(raw)
    # Scale so the last arrival lands ~80 % through the shift, leaving headroom.
    raw = raw * (total_seconds * 0.80) / raw[-1]
    return raw


def main(out_path: str = "conveyor_data.xlsx") -> None:
    rng = np.random.default_rng(SEED)
    p_times = _lognormal_processing_times(NUM_TOTES, rng).round(1)
    rel_times = _bursty_release_times(NUM_TOTES, SHIFT_SECONDS, ARRIVAL_BURSTINESS, rng).round(1)
    weights = rng.choice([1, 2, 3], size=NUM_TOTES, p=[0.6, 0.3, 0.1])
    grades = rng.choice(["Standard", "Express", "Fragile"],
                         size=NUM_TOTES, p=[0.65, 0.25, 0.10])
    sku = [f"SKU-{i:04d}" for i in range(1, NUM_TOTES + 1)]

    df = pd.DataFrame({
        "tote_id": [f"T{i:03d}" for i in range(1, NUM_TOTES + 1)],
        "sku": sku,
        "release_time_s": rel_times,
        "processing_time_s": p_times,
        "priority": weights,
        "grade": grades,
    }).sort_values("release_time_s").reset_index(drop=True)

    config = pd.DataFrame({
        "Parameter": ["Number of lanes", "Shift length (seconds)", "Shift length (hours)"],
        "Value": [NUM_LANES, SHIFT_SECONDS, SHIFT_SECONDS / 3600],
    })

    out = Path(out_path)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Totes", index=False)
        config.to_excel(writer, sheet_name="Config", index=False)
    print(f"Wrote {out_path}: {NUM_TOTES} totes across {NUM_LANES} lanes")
    print(f"  Processing time:  mean {df['processing_time_s'].mean():.1f} s, "
          f"max {df['processing_time_s'].max():.1f} s")
    print(f"  Release window:   0 to {df['release_time_s'].max():.0f} s "
          f"({df['release_time_s'].max() / 3600:.1f} h)")


if __name__ == "__main__":
    main()
