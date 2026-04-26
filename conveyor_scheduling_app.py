"""Interactive conveyor-scheduling dashboard.

Run with::

    streamlit run conveyor_scheduling_app.py

Edit the tote list in-browser, choose a dispatching rule, see the Gantt chart
and per-rule comparison update in real time.
"""

from __future__ import annotations

import io

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from matplotlib.patches import Patch

from scheduling import RULES, Tote, compare_rules

st.set_page_config(page_title="Conveyor Scheduler", layout="wide", page_icon="🏭")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@st.cache_data
def load_default():
    df = pd.read_excel("conveyor_data.xlsx", sheet_name="Totes")
    config = pd.read_excel("conveyor_data.xlsx", sheet_name="Config").set_index("Parameter")
    return df, int(config.loc["Number of lanes", "Value"])


def df_to_totes(df: pd.DataFrame) -> list[Tote]:
    return [
        Tote(
            tote_id=str(row["tote_id"]),
            release=float(row["release_time_s"]),
            processing=float(row["processing_time_s"]),
            priority=int(row["priority"]),
            grade=str(row["grade"]),
        )
        for _, row in df.iterrows()
    ]


def gantt_figure(schedule, color_by: str = "grade") -> plt.Figure:
    df = schedule.to_dataframe()
    grade_color = {"Standard": "#4c72b0", "Express": "#dd8452", "Fragile": "#c44e52"}
    priority_color = {1: "#4c72b0", 2: "#dd8452", 3: "#c44e52"}
    fig, ax = plt.subplots(figsize=(12, 0.6 * schedule.num_lanes + 1.5))
    for _, row in df.iterrows():
        if color_by == "grade":
            color = grade_color.get(row["grade"], "#888")
        else:
            color = priority_color.get(row["priority"], "#888")
        ax.barh(row["lane"], row["finish"] - row["start"], left=row["start"],
                color=color, edgecolor="white", linewidth=0.4)
    ax.set_yticks(range(1, schedule.num_lanes + 1))
    ax.set_yticklabels([f"Lane {l}" for l in range(1, schedule.num_lanes + 1)])
    ax.set_xlabel("Time (s)")
    ax.set_title(f"{schedule.rule}  ·  makespan {schedule.makespan:.0f} s  ·  "
                  f"mean flow {schedule.mean_flow_time:.1f} s  ·  lane CV {schedule.lane_balance_cv:.3f}")
    ax.grid(alpha=0.25, axis="x")
    if color_by == "grade":
        ax.legend(handles=[Patch(facecolor=c, label=g) for g, c in grade_color.items()],
                   loc="upper right", fontsize=9)
    else:
        ax.legend(handles=[Patch(facecolor=c, label=f"Priority {p}") for p, c in priority_color.items()],
                   loc="upper right", fontsize=9)
    plt.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


st.title("Warehouse Conveyor Scheduler")
st.caption("Tote sequencing across a multi-lane conveyor under release-time and processing-time constraints. Compare FIFO, EFT, SPT, LPT, and WSPT dispatching.")

if "tote_df" not in st.session_state:
    df, n_lanes = load_default()
    st.session_state["tote_df"] = df
    st.session_state["n_lanes"] = n_lanes


with st.sidebar:
    st.header("Workload")
    n_lanes = st.number_input("Number of lanes", min_value=1, max_value=12,
                                value=int(st.session_state["n_lanes"]), step=1)
    if st.button("Reset to bundled workload", use_container_width=True):
        df, n_lanes = load_default()
        st.session_state["tote_df"] = df
        st.session_state["n_lanes"] = n_lanes
        st.rerun()

    st.subheader("Tote list")
    st.caption("Edit any cell. Add or delete rows from the toolbar at the top of the table.")
    tote_df = st.data_editor(
        st.session_state["tote_df"], num_rows="dynamic", key="tote_editor",
        column_config={
            "tote_id": st.column_config.TextColumn(required=True),
            "release_time_s": st.column_config.NumberColumn(min_value=0.0, format="%.1f"),
            "processing_time_s": st.column_config.NumberColumn(min_value=0.1, format="%.1f"),
            "priority": st.column_config.NumberColumn(min_value=1, max_value=10, step=1),
            "grade": st.column_config.SelectboxColumn(options=["Standard", "Express", "Fragile"]),
        },
    )

    st.subheader("Display")
    color_by = st.radio("Colour Gantt bars by", ["grade", "priority"], horizontal=True)


# Validate
tote_df = tote_df.dropna(subset=["tote_id", "release_time_s", "processing_time_s"]).reset_index(drop=True)
if len(tote_df) == 0:
    st.warning("Add at least one tote.")
    st.stop()

totes = df_to_totes(tote_df)

# Headline metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Totes", len(totes))
c2.metric("Lanes", n_lanes)
total_work = tote_df["processing_time_s"].sum()
c3.metric("Total work", f"{total_work:,.0f} s",
          delta=f"≈ {total_work / n_lanes:,.0f} s/lane perfect-balance lower bound", delta_color="off")
c4.metric("Arrival window", f"0 → {tote_df['release_time_s'].max():,.0f} s")

st.divider()

# Comparison table
st.subheader("Rule comparison")
summary = compare_rules(totes, n_lanes)
fmt = summary.copy()
fmt["makespan_s"] = fmt["makespan_s"].map("{:,.1f}".format)
fmt["mean_flow_s"] = fmt["mean_flow_s"].map("{:,.1f}".format)
fmt["mean_wait_s"] = fmt["mean_wait_s"].map("{:,.1f}".format)
fmt["lane_balance_cv"] = fmt["lane_balance_cv"].map("{:.3f}".format)
st.dataframe(fmt, hide_index=True, use_container_width=True)

best_rule = summary.loc[summary["makespan_s"].idxmin(), "rule"]
st.caption(f"**{best_rule}** delivers the lowest makespan on this workload.")

st.divider()

# Gantt chart for chosen rule
st.subheader("Gantt — schedule per rule")
chosen_rule = st.radio("Rule", list(RULES), horizontal=True,
                        index=list(RULES).index(best_rule))
schedule = RULES[chosen_rule](totes, n_lanes)
st.pyplot(gantt_figure(schedule, color_by=color_by))

with st.expander("Per-tote schedule"):
    sched_df = schedule.to_dataframe()
    st.dataframe(sched_df, hide_index=True, use_container_width=True)
    buf = io.BytesIO()
    sched_df.to_csv(buf, index=False)
    buf.seek(0)
    st.download_button(f"Download {chosen_rule} schedule as CSV", buf,
                        file_name=f"schedule_{chosen_rule}.csv", mime="text/csv")

st.divider()

# Side-by-side Gantt for all rules
st.subheader("Side-by-side comparison")
fig, axes = plt.subplots(len(RULES), 1, figsize=(12, 1.6 * len(RULES)), sharex=True)
grade_color = {"Standard": "#4c72b0", "Express": "#dd8452", "Fragile": "#c44e52"}
priority_color = {1: "#4c72b0", 2: "#dd8452", 3: "#c44e52"}
for ax, (rule, fn) in zip(axes, RULES.items()):
    s = fn(totes, n_lanes)
    df = s.to_dataframe()
    for _, row in df.iterrows():
        if color_by == "grade":
            color = grade_color.get(row["grade"], "#888")
        else:
            color = priority_color.get(row["priority"], "#888")
        ax.barh(row["lane"], row["finish"] - row["start"], left=row["start"],
                color=color, edgecolor="white", linewidth=0.3)
    ax.set_yticks(range(1, n_lanes + 1))
    ax.set_ylabel(rule, fontsize=9)
    ax.set_title(f"makespan {s.makespan:.0f} s  ·  mean flow {s.mean_flow_time:.1f} s",
                  fontsize=9, loc="right")
    ax.grid(alpha=0.25, axis="x")
axes[-1].set_xlabel("Time (s)")
plt.tight_layout()
st.pyplot(fig)
