"""Parallel-machine scheduling on a multi-lane warehouse conveyor.

Implements four classical dispatching rules for assigning totes to lanes
under release-time and processing-time constraints:

- **FIFO** (First-In-First-Out) — assign each tote in arrival order to the
  earliest-free lane. Baseline.
- **EFT** (Earliest Finish Time) — among ready totes, pick the lane that
  minimises the candidate's finish time.
- **SPT** (Shortest Processing Time) — among ready totes, dispatch the one
  with the shortest processing time first. Provably optimal for makespan
  on identical parallel machines when all release times are zero.
- **LPT** (Longest Processing Time) — like SPT but reversed. Excellent
  empirical performance on makespan with non-zero release times — the
  long-tail jobs get scheduled early and short jobs fill the gaps.

Each function returns a `Schedule` object with the per-tote assignment
(lane, start, finish) plus aggregate metrics: makespan, mean flow time,
mean lateness vs. release, lane-load balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class Tote:
    tote_id: str
    release: float
    processing: float
    priority: int = 1
    grade: str = "Standard"


@dataclass
class Assignment:
    tote_id: str
    lane: int
    start: float
    finish: float
    release: float
    processing: float
    priority: int
    grade: str

    @property
    def wait(self) -> float:
        return self.start - self.release

    @property
    def flow(self) -> float:
        return self.finish - self.release


@dataclass
class Schedule:
    rule: str
    assignments: list[Assignment] = field(default_factory=list)
    num_lanes: int = 0

    @property
    def makespan(self) -> float:
        return max((a.finish for a in self.assignments), default=0.0)

    @property
    def mean_flow_time(self) -> float:
        if not self.assignments:
            return 0.0
        return sum(a.flow for a in self.assignments) / len(self.assignments)

    @property
    def mean_wait(self) -> float:
        if not self.assignments:
            return 0.0
        return sum(a.wait for a in self.assignments) / len(self.assignments)

    @property
    def lane_loads(self) -> dict[int, float]:
        loads = {l: 0.0 for l in range(1, self.num_lanes + 1)}
        for a in self.assignments:
            loads[a.lane] += a.processing
        return loads

    @property
    def lane_balance_cv(self) -> float:
        loads = list(self.lane_loads.values())
        if not loads:
            return 0.0
        mean = sum(loads) / len(loads)
        if mean == 0:
            return 0.0
        var = sum((l - mean) ** 2 for l in loads) / len(loads)
        return (var ** 0.5) / mean

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "tote_id": a.tote_id, "lane": a.lane,
            "release": round(a.release, 1), "start": round(a.start, 1),
            "finish": round(a.finish, 1), "processing": round(a.processing, 1),
            "wait": round(a.wait, 1), "flow": round(a.flow, 1),
            "priority": a.priority, "grade": a.grade,
        } for a in self.assignments])


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_totes(path: str | Path) -> tuple[list[Tote], int]:
    df = pd.read_excel(path, sheet_name="Totes")
    config = pd.read_excel(path, sheet_name="Config").set_index("Parameter")
    n_lanes = int(config.loc["Number of lanes", "Value"])
    totes = [
        Tote(tote_id=row["tote_id"], release=float(row["release_time_s"]),
             processing=float(row["processing_time_s"]),
             priority=int(row["priority"]), grade=row["grade"])
        for _, row in df.iterrows()
    ]
    return totes, n_lanes


# ---------------------------------------------------------------------------
# Scheduling rules
# ---------------------------------------------------------------------------


def schedule_fifo(totes: list[Tote], num_lanes: int) -> Schedule:
    """Assign each tote in arrival order to the lane that frees up first."""
    lane_free = [0.0] * num_lanes
    order = sorted(totes, key=lambda t: (t.release, t.tote_id))
    out = Schedule(rule="FIFO", num_lanes=num_lanes)
    for t in order:
        lane = min(range(num_lanes), key=lambda l: lane_free[l])
        start = max(t.release, lane_free[lane])
        finish = start + t.processing
        lane_free[lane] = finish
        out.assignments.append(_assignment(t, lane + 1, start, finish))
    return out


def schedule_eft(totes: list[Tote], num_lanes: int) -> Schedule:
    """Earliest Finish Time — at each step, among ready totes pick the (tote, lane) pair
    that minimises finish time."""
    lane_free = [0.0] * num_lanes
    remaining = sorted(totes, key=lambda t: t.release)
    out = Schedule(rule="EFT", num_lanes=num_lanes)
    while remaining:
        # Earliest event time: either the earliest lane_free or the next release
        next_release = min(t.release for t in remaining)
        now = max(min(lane_free), next_release)

        # Among totes already released by `now`, pick the one whose best lane gives
        # the earliest finish.
        ready = [t for t in remaining if t.release <= now]
        if not ready:
            ready = [min(remaining, key=lambda t: t.release)]

        best = None
        for t in ready:
            for l in range(num_lanes):
                start = max(t.release, lane_free[l])
                finish = start + t.processing
                if best is None or finish < best[2]:
                    best = (t, l, finish, start)
        t, lane, finish, start = best
        lane_free[lane] = finish
        out.assignments.append(_assignment(t, lane + 1, start, finish))
        remaining.remove(t)
    return out


def schedule_spt(totes: list[Tote], num_lanes: int) -> Schedule:
    """Shortest Processing Time — among ready totes, dispatch the shortest first."""
    return _length_priority_schedule(totes, num_lanes, longest_first=False, rule="SPT")


def schedule_lpt(totes: list[Tote], num_lanes: int) -> Schedule:
    """Longest Processing Time — among ready totes, dispatch the longest first."""
    return _length_priority_schedule(totes, num_lanes, longest_first=True, rule="LPT")


def _length_priority_schedule(totes: list[Tote], num_lanes: int,
                                longest_first: bool, rule: str) -> Schedule:
    lane_free = [0.0] * num_lanes
    remaining = list(totes)
    out = Schedule(rule=rule, num_lanes=num_lanes)
    while remaining:
        # Earliest available lane
        lane = min(range(num_lanes), key=lambda l: lane_free[l])
        # Tote candidates already released by the lane-free time
        available_at = lane_free[lane]
        ready = [t for t in remaining if t.release <= available_at]
        if ready:
            choice = (max if longest_first else min)(ready, key=lambda t: t.processing)
        else:
            # No tote ready — pick the next-arriving one and let the lane idle.
            choice = min(remaining, key=lambda t: t.release)
        start = max(choice.release, available_at)
        finish = start + choice.processing
        lane_free[lane] = finish
        out.assignments.append(_assignment(choice, lane + 1, start, finish))
        remaining.remove(choice)
    return out


def schedule_wspt(totes: list[Tote], num_lanes: int) -> Schedule:
    """Weighted Shortest Processing Time — minimises sum of weighted completion times.

    Weight = priority (higher priority is more urgent). Pick the ready tote with the
    largest priority/processing ratio (Smith's rule).
    """
    lane_free = [0.0] * num_lanes
    remaining = list(totes)
    out = Schedule(rule="WSPT", num_lanes=num_lanes)
    while remaining:
        lane = min(range(num_lanes), key=lambda l: lane_free[l])
        available_at = lane_free[lane]
        ready = [t for t in remaining if t.release <= available_at]
        if ready:
            choice = max(ready, key=lambda t: t.priority / t.processing)
        else:
            choice = min(remaining, key=lambda t: t.release)
        start = max(choice.release, available_at)
        finish = start + choice.processing
        lane_free[lane] = finish
        out.assignments.append(_assignment(choice, lane + 1, start, finish))
        remaining.remove(choice)
    return out


def _assignment(t: Tote, lane: int, start: float, finish: float) -> Assignment:
    return Assignment(
        tote_id=t.tote_id, lane=lane, start=start, finish=finish,
        release=t.release, processing=t.processing,
        priority=t.priority, grade=t.grade,
    )


RULES: dict[str, Callable[[list[Tote], int], Schedule]] = {
    "FIFO": schedule_fifo,
    "EFT": schedule_eft,
    "SPT": schedule_spt,
    "LPT": schedule_lpt,
    "WSPT": schedule_wspt,
}


def compare_rules(totes: list[Tote], num_lanes: int,
                   rules: list[str] | None = None) -> pd.DataFrame:
    """Run every rule and return a row-per-rule comparison table."""
    rules = rules or list(RULES)
    rows = []
    for r in rules:
        s = RULES[r](totes, num_lanes)
        rows.append({
            "rule": r,
            "makespan_s": round(s.makespan, 1),
            "makespan_h": round(s.makespan / 3600, 2),
            "mean_flow_s": round(s.mean_flow_time, 1),
            "mean_wait_s": round(s.mean_wait, 1),
            "lane_balance_cv": round(s.lane_balance_cv, 3),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    totes, n_lanes = load_totes("conveyor_data.xlsx")
    print(f"Loaded {len(totes)} totes across {n_lanes} lanes\n")
    cmp = compare_rules(totes, n_lanes)
    print(cmp.to_string(index=False))
