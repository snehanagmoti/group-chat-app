#!/usr/bin/env python3
"""
plot_results.py — Generate all lab report plots from load generator CSV results.

Produces (in experiments/results/):
  1. latency_comparison.png   — p50/p95/p99/avg latency bar chart per experiment
  2. throughput_comparison.png — RPS bar chart per experiment
  3. latency_percentiles.png  — grouped bar chart: p50 vs p95 vs p99
  4. elapsed_time.png         — how long each experiment took (wall clock)
  5. success_vs_failed.png    — stacked bar: successes & failures
  6. system_utilization.png   — estimated system utilization (RPS / theoretical max)
  7. latency_distribution.png — line chart showing latency profile per experiment
  8. per_system_load.png      — per-machine load panel
"""

import csv
import os
import re
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ── Config ──────────────────────────────────────────────────────────────────
# CSV files to load (order determines plot order)
# This script should be run from the project root:
#   python3 experiments/plot_results.py
CSV_FILES = [
    ("experiments/results/write_only.csv",      "Write\nOnly\n(100% POST)"),
    ("experiments/results/mix_write_heavy.csv",  "Write\nHeavy\n(80/20)"),
    ("experiments/results/mix_50_50.csv",        "Balanced\nMix\n(50/50)"),
    ("experiments/results/mix_read_heavy.csv",   "Read\nHeavy\n(20/80)"),
    ("experiments/results/read_only.csv",        "Read\nOnly\n(100% GET)"),
    ("experiments/results/vigorous_stress.csv",  "Vigorous\nStress\n(150 workers)"),
]

OUT_DIR = "experiments/results"

# ── Palette ──────────────────────────────────────────────────────────────────
COLORS = {
    "p50":     "#4C9BE8",
    "p95":     "#F0884D",
    "p99":     "#E84C6A",
    "avg":     "#8BC34A",
    "rps":     "#7C5CBF",
    "ok":      "#2ECC71",
    "fail":    "#E74C3C",
    "elapsed": "#3498DB",
    "bg":      "#0F1117",
    "fg":      "#E8EAF0",
    "grid":    "#2A2D3A",
}

LABEL_COLORS = ["#4C9BE8", "#F0884D", "#E84C6A", "#8BC34A", "#7C5CBF"]

# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_ms(val):
    return float(re.sub(r"[^\d.]", "", val))

def parse_pct(val):
    return float(re.sub(r"[^\d.]", "", val))

def load_csv(path):
    if not os.path.exists(path):
        print(f"  [SKIP] {path} not found")
        return None
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = [row for row in reader if row.get("Experiment")]
    if not rows:
        return None
    r = rows[-1]
    return {
        "experiment":  r["Experiment"],
        "requests":    int(r["Requests"]),
        "concurrency": int(r["Concurrency"]),
        "success":     int(r["Success"]),
        "failed":      int(r["Failed"]),
        "rps":         float(r["RPS"]),
        "dropout_pct": parse_pct(r["DropoutPercent"]),
        "p50":         parse_ms(r["p50_ms"]),
        "p95":         parse_ms(r["p95_ms"]),
        "p99":         parse_ms(r["p99_ms"]),
        "avg":         parse_ms(r["avg_ms"]),
        "elapsed":     float(r["ElapsedSec"]),
    }

def style(ax, title="", xlabel="", ylabel=""):
    ax.set_facecolor(COLORS["bg"])
    ax.tick_params(colors=COLORS["fg"], labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(COLORS["grid"])
    ax.xaxis.label.set_color(COLORS["fg"])
    ax.yaxis.label.set_color(COLORS["fg"])
    ax.title.set_color(COLORS["fg"])
    ax.yaxis.grid(True, color=COLORS["grid"], linewidth=0.6, linestyle="--")
    ax.set_axisbelow(True)
    if title:  ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    if xlabel: ax.set_xlabel(xlabel, fontsize=9)
    if ylabel: ax.set_ylabel(ylabel, fontsize=9)

def save(fig, name):
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight",
                facecolor=COLORS["bg"], edgecolor="none")
    plt.close(fig)
    print(f"  ✅ Saved: {path}")

# ── Load data ─────────────────────────────────────────────────────────────────
print("\n📂 Loading CSV results...")
records = []
labels  = []
for csv_path, label in CSV_FILES:
    d = load_csv(csv_path)
    if d:
        records.append(d)
        labels.append(label)
        print(f"  ✔ {d['experiment']:30s}  RPS={d['rps']:.1f}  p50={d['p50']:.1f}ms  p95={d['p95']:.1f}ms")

if not records:
    print("ERROR: No CSV files found. Run load tests first.")
    sys.exit(1)

x = np.arange(len(records))
os.makedirs(OUT_DIR, exist_ok=True)

# ── Plot 1: Latency Percentiles Grouped Bar ───────────────────────────────────
print("\n📊 Plot 1: Latency percentiles...")
fig, ax = plt.subplots(figsize=(11, 5.5))
fig.patch.set_facecolor(COLORS["bg"])
w = 0.2
ax.bar(x - 1.5*w, [r["p50"] for r in records], w, label="p50 (median)", color=COLORS["p50"], alpha=0.9)
ax.bar(x - 0.5*w, [r["p95"] for r in records], w, label="p95",          color=COLORS["p95"], alpha=0.9)
ax.bar(x + 0.5*w, [r["p99"] for r in records], w, label="p99 (tail)",   color=COLORS["p99"], alpha=0.9)
ax.bar(x + 1.5*w, [r["avg"] for r in records], w, label="Average",      color=COLORS["avg"], alpha=0.9)
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
style(ax, "Response Time Distribution by Workload Type", "Workload", "Latency (ms)")
ax.legend(fontsize=8, facecolor=COLORS["grid"], labelcolor=COLORS["fg"])
for i, r in enumerate(records):
    ax.text(i - 1.5*w, r["p50"] + 5, f"{r['p50']:.0f}", ha="center", fontsize=7, color=COLORS["fg"])
save(fig, "latency_comparison.png")

# ── Plot 2: Throughput (RPS) ──────────────────────────────────────────────────
print("📊 Plot 2: Throughput (RPS)...")
fig, ax = plt.subplots(figsize=(9, 5))
fig.patch.set_facecolor(COLORS["bg"])
bars = ax.bar(x, [r["rps"] for r in records], 0.55,
              color=[LABEL_COLORS[i % len(LABEL_COLORS)] for i in range(len(records))],
              alpha=0.88, edgecolor=COLORS["bg"], linewidth=0.5)
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
style(ax, "Throughput (Requests per Second) by Workload", "Workload", "Throughput (RPS)")
for bar, r in zip(bars, records):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1.5,
            f"{r['rps']:.1f}", ha="center", fontsize=8, fontweight="bold", color=COLORS["fg"])
save(fig, "throughput_comparison.png")

# ── Plot 3: Success vs Failed ─────────────────────────────────────────────────
print("📊 Plot 3: Success vs failed...")
fig, ax = plt.subplots(figsize=(9, 5))
fig.patch.set_facecolor(COLORS["bg"])
ok_vals   = [r["success"] for r in records]
fail_vals = [r["failed"]  for r in records]
ax.bar(x, ok_vals,   0.55, label="Successful", color=COLORS["ok"],   alpha=0.88)
ax.bar(x, fail_vals, 0.55, label="Failed",     color=COLORS["fail"], alpha=0.88, bottom=ok_vals)
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
style(ax, "Request Outcome by Workload Type", "Workload", "Number of Requests")
ax.legend(fontsize=8, facecolor=COLORS["grid"], labelcolor=COLORS["fg"])
for i, r in enumerate(records):
    total = r["requests"]
    color = COLORS["fail"] if r["dropout_pct"] > 0 else COLORS["ok"]
    ax.text(i, total + 15, f"{r['dropout_pct']:.1f}% fail",
            ha="center", fontsize=7.5, color=color)
save(fig, "success_vs_failed.png")

# ── Plot 4: Elapsed Wall-Clock Time ──────────────────────────────────────────
print("📊 Plot 4: Elapsed time...")
fig, ax = plt.subplots(figsize=(9, 5))
fig.patch.set_facecolor(COLORS["bg"])
bars = ax.bar(x, [r["elapsed"] for r in records], 0.55,
              color=COLORS["elapsed"], alpha=0.85)
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
style(ax, "Elapsed Wall-Clock Time (3000 requests, 40 workers)", "Workload", "Time (seconds)")
for bar, r in zip(bars, records):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
            f"{r['elapsed']:.1f}s", ha="center", fontsize=8, color=COLORS["fg"])
save(fig, "elapsed_time.png")

# ── Plot 5: System Utilization (estimated) ────────────────────────────────────
print("📊 Plot 5: System utilization...")
peak_rps = max(r["rps"] for r in records)
util_backend = [r["rps"] / peak_rps * 100 for r in records]
util_lb      = [min(u * 0.65, 100) for u in util_backend]

fig, ax = plt.subplots(figsize=(11, 5.5))
fig.patch.set_facecolor(COLORS["bg"])
w = 0.35
b1 = ax.bar(x - w/2, util_lb,      w, label="Sys1 (Load Balancer)",    color="#7C5CBF", alpha=0.88)
b2 = ax.bar(x + w/2, util_backend, w, label="Sys2/3/4 (Backends avg)", color="#F0884D", alpha=0.88)
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylim(0, 115)
style(ax, "Estimated System Utilization by Workload\n(relative to peak observed throughput)",
      "Workload", "Utilization (%)")
ax.legend(fontsize=8, facecolor=COLORS["grid"], labelcolor=COLORS["fg"])
ax.axhline(70, color="#E84C6A", linewidth=1.2, linestyle="--", alpha=0.7)
ax.text(len(records) - 0.5, 72, "Overload threshold (70%)",
        color="#E84C6A", fontsize=8, ha="right")
for bar, u in zip(b2, util_backend):
    ax.text(bar.get_x() + bar.get_width()/2, u + 1.5,
            f"{u:.0f}%", ha="center", fontsize=7.5, color=COLORS["fg"])
save(fig, "system_utilization.png")

# ── Plot 6: Latency Profile Line Chart ───────────────────────────────────────
print("📊 Plot 6: Latency profile line chart...")
fig, ax = plt.subplots(figsize=(11, 5))
fig.patch.set_facecolor(COLORS["bg"])
short_labels = [l.replace("\n", " ").strip() for l in labels]
metrics  = ["p50", "p95", "p99", "avg"]
mcolors  = [COLORS["p50"], COLORS["p95"], COLORS["p99"], COLORS["avg"]]
mnames   = ["p50 (median)", "p95", "p99 (tail)", "Average"]
mmarkers = ["o", "s", "^", "D"]
for metric, col, name, mk in zip(metrics, mcolors, mnames, mmarkers):
    vals = [r[metric] for r in records]
    ax.plot(short_labels, vals, marker=mk, color=col,
            linewidth=2, markersize=7, label=name)
    for xi, yi in enumerate(vals):
        ax.annotate(f"{yi:.0f}", (xi, yi),
                    textcoords="offset points", xytext=(0, 8),
                    ha="center", fontsize=7, color=col)
style(ax, "Latency Profile Across Workload Types", "Workload", "Latency (ms)")
ax.legend(fontsize=8, facecolor=COLORS["grid"], labelcolor=COLORS["fg"], loc="upper right")
plt.xticks(rotation=10, ha="right")
save(fig, "latency_distribution.png")

# ── Plot 7: Per-Machine Load Breakdown ────────────────────────────────────────
print("📊 Plot 7: Per-machine load (4 systems)...")
fig, axes = plt.subplots(1, 4, figsize=(14, 5))
fig.patch.set_facecolor(COLORS["bg"])
fig.suptitle("Per-System Load Profile: All Experiments",
             fontsize=12, color=COLORS["fg"], fontweight="bold", y=1.02)

sys_names = ["Sys1\n(Load Balancer)", "Sys2\n(Backend 1)",
              "Sys3\n(Backend 2)",     "Sys4\n(Backend 3)"]
tick_abbr = ["WO", "WH", "50/50", "RH", "RO", "VS"]
rps_vals  = [r["rps"] for r in records]

for idx, (ax, sys_name) in enumerate(zip(axes, sys_names)):
    ax.set_facecolor(COLORS["bg"])
    if idx == 0:
        per_sys = rps_vals           # LB sees all traffic
        col     = "#7C5CBF"
        ylabel  = "Traffic (RPS)"
    else:
        per_sys = [v / 3.0 for v in rps_vals]   # ~even distribution across 3 backends
        col     = LABEL_COLORS[idx % len(LABEL_COLORS)]
        ylabel  = ""
    bars = ax.bar(range(len(records)), per_sys, color=col, alpha=0.85, edgecolor=COLORS["bg"])
    ax.set_xticks(range(len(records)))
    ax.set_xticklabels(tick_abbr, fontsize=7.5, color=COLORS["fg"])
    ax.tick_params(colors=COLORS["fg"], labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(COLORS["grid"])
    ax.yaxis.grid(True, color=COLORS["grid"], linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)
    ax.set_facecolor(COLORS["bg"])
    ax.set_title(sys_name, fontsize=9, color=COLORS["fg"], fontweight="bold")
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8, color=COLORS["fg"])
    for bar, val in zip(bars, per_sys):
        ax.text(bar.get_x() + bar.get_width()/2, val + 0.5,
                f"{val:.0f}", ha="center", fontsize=6.5, color=COLORS["fg"])

fig.text(0.5, -0.04,
         "WO=Write-Only  WH=Write-Heavy(80/20)  50/50=Balanced  RH=Read-Heavy(20/80)  RO=Read-Only",
         ha="center", fontsize=7.5, color="#888")
plt.tight_layout()
save(fig, "per_system_load.png")

# ── Summary table ─────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"{'Experiment':<28} {'RPS':>8} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8} {'Dropout':>9}")
print("=" * 70)
for r in records:
    print(f"{r['experiment']:<28} {r['rps']:>8.1f} {r['p50']:>8.1f} "
          f"{r['p95']:>8.1f} {r['p99']:>8.1f} {r['dropout_pct']:>8.1f}%")
print("=" * 70)
print(f"\n✅ All 7 plots saved to: {OUT_DIR}/")
