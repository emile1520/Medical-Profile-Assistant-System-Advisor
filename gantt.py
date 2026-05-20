"""
Gantt chart generator — Medical Profile Assistant & System Advisor (FYP_26_09)
USJ Faculty of Dental Medicine x Tomorrow Services  ·  19 Jan – 23 May 2026

Every task in this chart corresponds to a real workstream described in
Section 1 (Introduction) and Section 5 (Implementation) of the final report.

Requirements:
    pip install matplotlib

Run:
    python gantt.py

Output:
    gantt_chart.png   (saved next to this script, also opened in a window)
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
from datetime import datetime


# ── Project window ───────────────────────────────────────────────────────────
PROJECT_START = datetime(2026, 1, 19)
PROJECT_END   = datetime(2026, 5, 23)
DEADLINE      = datetime(2026, 5, 12)


# ── Workstreams → colours ───────────────────────────────────────────────────
GROUPS = {
    "Planning & Design":     "#2563eb",   # blue
    "Backend Core":          "#16a34a",   # green
    "AI Pipeline":           "#ea580c",   # orange
    "Frontend":              "#9333ea",   # purple
    "SDK Integration":       "#dc2626",   # red
    "Testing & Validation":  "#ca8a04",   # amber
    "Documentation":         "#64748b",   # slate
}


# ── Tasks (top → bottom on the chart) ────────────────────────────────────────
# Each row: (label, start_date, end_date, workstream)
TASKS = [
    ("Requirements analysis & specifications",          "2026-01-19", "2026-02-02", "Planning & Design"),
    ("Architecture design & schema concept",            "2026-01-26", "2026-02-09", "Planning & Design"),

    ("FastAPI backend scaffold & CORS setup",           "2026-02-02", "2026-02-16", "Backend Core"),
    ("Whisper speech-to-text integration",              "2026-02-09", "2026-02-23", "Backend Core"),
    ("SQLite database & audit logging",                 "2026-02-16", "2026-03-02", "Backend Core"),
    ("Ollama + Qwen3-1.7B local model setup",           "2026-02-23", "2026-03-09", "Backend Core"),

    ("Schema-driven Qwen extraction engine",            "2026-03-02", "2026-03-23", "AI Pipeline"),
    ("Five procedure schemas (RCT, extraction, etc.)",  "2026-03-09", "2026-03-23", "AI Pipeline"),
    ("Hybrid procedure-type detector",                  "2026-03-16", "2026-03-30", "AI Pipeline"),

    ("Initial React frontend prototype",                "2026-02-16", "2026-03-16", "Frontend"),
    ("Dynamic schema-driven form renderer",             "2026-03-16", "2026-03-30", "Frontend"),
    ("UI redesign (clinical / dental theme)",           "2026-03-23", "2026-04-06", "Frontend"),

    ("SDK public API design",                           "2026-03-30", "2026-04-13", "SDK Integration"),
    ("JavaScript SDK implementation",                   "2026-04-06", "2026-04-27", "SDK Integration"),
    ("example.html demo & README documentation",        "2026-04-20", "2026-05-04", "SDK Integration"),

    ("End-to-end functional testing",                   "2026-04-13", "2026-05-04", "Testing & Validation"),
    ("Performance & security validation",               "2026-04-27", "2026-05-11", "Testing & Validation"),

    ("Progress report",                                 "2026-03-30", "2026-04-14", "Documentation"),
    ("Final report writing",                            "2026-05-04", "2026-05-18", "Documentation"),
    ("Presentation preparation",                        "2026-05-11", "2026-05-23", "Documentation"),
]


def to_date(s):
    return datetime.strptime(s, "%Y-%m-%d")


# ── Plot ────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(14, 9))
fig.patch.set_facecolor("white")

y_positions = list(range(len(TASKS)))[::-1]    # top of chart = first task
for y, (label, s, e, group) in zip(y_positions, TASKS):
    start, end = to_date(s), to_date(e)
    duration = (end - start).days
    ax.barh(
        y, duration, left=start, height=0.58,
        color=GROUPS[group], edgecolor="white", linewidth=0.7,
        zorder=3,
    )

# Y-axis: task names
ax.set_yticks(y_positions)
ax.set_yticklabels([t[0] for t in TASKS], fontsize=9)

# X-axis: dates
ax.xaxis_date()
ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=2))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
ax.set_xlim(PROJECT_START, PROJECT_END)
plt.setp(ax.get_xticklabels(), rotation=0, ha="center", fontsize=9)

# Deadline marker
ax.axvline(DEADLINE, color="#1e293b", linestyle="--", linewidth=1.4, alpha=0.7, zorder=2)
ax.text(
    DEADLINE, len(TASKS) - 0.4,
    "  Project deadline\n  May 12, 2026",
    fontsize=8, color="#1e293b", va="top",
)

# Grid + spines
ax.grid(axis="x", linestyle=":", linewidth=0.6, alpha=0.7, zorder=1)
ax.set_axisbelow(True)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)

# Title
ax.set_title(
    "Medical Profile Assistant & System Advisor — Project Gantt Chart\n"
    "FYP_26_09 · USJ × Tomorrow Services · 19 Jan – 23 May 2026",
    fontsize=13, fontweight="bold", pad=14,
)
ax.set_xlabel("Timeline", fontsize=10)

# Legend (workstreams)
handles = [Patch(facecolor=c, label=g) for g, c in GROUPS.items()]
ax.legend(handles=handles, loc="lower right", frameon=True, fontsize=9, ncol=2)

plt.tight_layout()
plt.savefig("gantt_chart.png", dpi=300, bbox_inches="tight")
plt.show()

print("Saved: gantt_chart.png")
