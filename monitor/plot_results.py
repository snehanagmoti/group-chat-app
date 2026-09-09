#!/usr/bin/env python3
"""
plot_results.py — Generate report plots from load generator + monitor CSVs.

Inputs:
  - results/{experiment}_latencies.csv   (raw latencies for CDF)
  - results/{experiment}_timeseries.csv  (per-second RPS)
  - results/comparison.csv or *.json    (dropout / summary comparison)
  - results/sys{1..4}_monitor.csv        (CPU/mem/net per system)

Outputs (PNG files in --out directory):
  1. response_time_cdf.png        — Latency CDF for each experiment
  2. response_time_timeseries.png — Response time / latency trend over time
  3. throughput_timeseries.png    — RPS over time (all experiments)
  4. sys_cpu.png                  — CPU % for all systems over time
  5. sys_mem.png                  — Memory % for all systems over time
  6. sys_net.png                  — Network throughput (KB/s) for all systems
  7. dropout_bar.png              — Dropout % per experiment
"""

import os
import sys
import glob
import json
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Set aesthetic styling
plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
plt.rcParams.update({
    'font.size': 11,
    'axes.labelsize': 12,
    'axes.titlesize': 14,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.titlesize': 16,
    'figure.autolayout': True,
})

def plot_latency_cdf(results_dir, out_dir, experiment=None):
    """Plot Response Time Cumulative Distribution Function (CDF)."""
    pattern = os.path.join(results_dir, f"{experiment}_latencies.csv" if experiment else "*_latencies.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"[WARN] No latency CSV files found matching {pattern}")
        return

    plt.figure(figsize=(9, 5.5))
    plotted = False

    for f in files:
        label = os.path.basename(f).replace("_latencies.csv", "")
        try:
            df = pd.read_csv(f)
            if df.empty:
                continue
            col = df.columns[0]
            latencies = np.sort(df[col].dropna().values)
            if len(latencies) == 0:
                continue
            cdf = np.linspace(0, 1, len(latencies))
            plt.plot(latencies, cdf, label=label, linewidth=2)
            plotted = True
        except Exception as e:
            print(f"[WARN] Failed to read {f}: {e}")

    if plotted:
        plt.title("Response Time CDF Across Experiments")
        plt.xlabel("Latency (ms)")
        plt.ylabel("Cumulative Probability (CDF)")
        plt.ylim(0, 1.05)
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.legend(loc="lower right", frameon=True)
        out_path = os.path.join(out_dir, "response_time_cdf.png")
        plt.savefig(out_path, dpi=300)
        print(f"[OK] Saved -> {out_path}")
    plt.close()


def plot_latency_timeseries(results_dir, out_dir, experiment=None):
    """Plot Response Time trend over sequential requests/time."""
    pattern = os.path.join(results_dir, f"{experiment}_latencies.csv" if experiment else "*_latencies.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        return

    plt.figure(figsize=(10, 5.5))
    plotted = False

    for f in files:
        label = os.path.basename(f).replace("_latencies.csv", "")
        try:
            df = pd.read_csv(f)
            if df.empty:
                continue
            col = df.columns[0]
            series = df[col].dropna()
            if len(series) == 0:
                continue

            # Calculate rolling average for readability
            window = max(10, len(series) // 50)
            rolling = series.rolling(window=window, min_periods=1).mean()
            plt.plot(rolling.values, label=f"{label} (rolling avg w={window})", linewidth=2)
            plotted = True
        except Exception as e:
            print(f"[WARN] Failed to read {f}: {e}")

    if plotted:
        plt.title("Response Time Over Time (Rolling Average)")
        plt.xlabel("Request Sequence #")
        plt.ylabel("Latency (ms)")
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.legend(loc="upper left", frameon=True)
        out_path = os.path.join(out_dir, "response_time_timeseries.png")
        plt.savefig(out_path, dpi=300)
        print(f"[OK] Saved -> {out_path}")
    plt.close()


def plot_throughput_timeseries(results_dir, out_dir, experiment=None):
    """Plot per-second RPS over time for each experiment."""
    pattern = os.path.join(results_dir, f"{experiment}_timeseries.csv" if experiment else "*_timeseries.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"[WARN] No timeseries CSV files found matching {pattern}")
        return

    plt.figure(figsize=(10, 5.5))
    plotted = False

    for f in files:
        label = os.path.basename(f).replace("_timeseries.csv", "")
        try:
            df = pd.read_csv(f)
            if df.empty or "second" not in df.columns or "rps" not in df.columns:
                continue
            plt.plot(df["second"], df["rps"], label=f"{label} RPS", marker="o", markersize=3, linewidth=2)
            plotted = True
        except Exception as e:
            print(f"[WARN] Failed to read {f}: {e}")

    if plotted:
        plt.title("Throughput (RPS) Over Time")
        plt.xlabel("Elapsed Time (seconds)")
        plt.ylabel("Requests / Second (RPS)")
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.legend(loc="best", frameon=True)
        out_path = os.path.join(out_dir, "throughput_timeseries.png")
        plt.savefig(out_path, dpi=300)
        print(f"[OK] Saved -> {out_path}")
    plt.close()


def plot_dropout_bar(results_dir, out_dir):
    """Plot dropout rate per experiment."""
    cmp_path = os.path.join(results_dir, "comparison.csv")
    labels = []
    dropouts = []

    if os.path.exists(cmp_path):
        try:
            df = pd.read_csv(cmp_path)
            if "experiment" in df.columns and "dropout_percent" in df.columns:
                labels = df["experiment"].astype(str).tolist()
                dropouts = df["dropout_percent"].astype(float).tolist()
        except Exception as e:
            print(f"[WARN] Failed to parse {cmp_path}: {e}")

    if not labels:
        # Fallback: scan .json files
        json_files = sorted(glob.glob(os.path.join(results_dir, "*.json")))
        for jf in json_files:
            try:
                with open(jf, "r") as f:
                    data = json.load(f)
                    if "experiment" in data and "dropout_percent" in data:
                        labels.append(data["experiment"])
                        dropouts.append(float(data["dropout_percent"]))
            except Exception:
                pass

    if not labels:
        print("[WARN] No comparison data or JSON files found for dropout plot")
        return

    plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, dropouts, color="#e74c3c", width=0.5, edgecolor="black", alpha=0.85)

    for bar in bars:
        height = bar.get_height()
        plt.annotate(f"{height:.2f}%",
                     xy=(bar.get_x() + bar.get_width() / 2, height),
                     xytext=(0, 3),
                     textcoords="offset points",
                     ha="center", va="bottom", fontsize=10, fontweight="bold")

    plt.title("Message Dropout Rate by Experiment")
    plt.xlabel("Experiment")
    plt.ylabel("Dropout Rate (%)")
    plt.ylim(0, max(max(dropouts) * 1.2 if dropouts else 10, 5))
    plt.grid(axis="y", linestyle="--", alpha=0.6)
    out_path = os.path.join(out_dir, "dropout_bar.png")
    plt.savefig(out_path, dpi=300)
    print(f"[OK] Saved -> {out_path}")
    plt.close()


def load_system_monitor_files(results_dir):
    """Find all sys*_monitor.csv files in results_dir or parent dir."""
    files = glob.glob(os.path.join(results_dir, "sys*_monitor.csv"))
    if not files:
        files = glob.glob(os.path.join(results_dir, "sys*.csv"))
    if not files:
        files = glob.glob("sys*_monitor.csv")
    return sorted(files)


def plot_system_metrics(results_dir, out_dir):
    """Plot CPU %, Memory %, and Network I/O for all systems over time."""
    files = load_system_monitor_files(results_dir)
    if not files:
        print(f"[WARN] No sys*_monitor.csv files found in {results_dir}")
        return

    system_data = {}
    for f in files:
        sys_name = os.path.basename(f).replace("_monitor.csv", "").replace(".csv", "").upper()
        try:
            df = pd.read_csv(f)
            if df.empty or "timestamp" not in df.columns:
                continue
            df["t"] = pd.to_datetime(df["timestamp"])
            df["elapsed_sec"] = (df["t"] - df["t"].iloc[0]).dt.total_seconds()
            system_data[sys_name] = df
        except Exception as e:
            print(f"[WARN] Could not parse system monitor file {f}: {e}")

    if not system_data:
        return

    # 1. CPU plot
    plt.figure(figsize=(10, 5.5))
    for sys_name, df in system_data.items():
        if "cpu_pct" in df.columns:
            plt.plot(df["elapsed_sec"], df["cpu_pct"], label=f"{sys_name} CPU", linewidth=2)
    plt.title("System CPU Utilization Over Time")
    plt.xlabel("Elapsed Time (seconds)")
    plt.ylabel("CPU Utilization (%)")
    plt.ylim(0, 105)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend(loc="best", frameon=True)
    out_cpu = os.path.join(out_dir, "sys_cpu.png")
    plt.savefig(out_cpu, dpi=300)
    print(f"[OK] Saved -> {out_cpu}")
    plt.close()

    # 2. Memory plot
    plt.figure(figsize=(10, 5.5))
    for sys_name, df in system_data.items():
        if "mem_pct" in df.columns:
            plt.plot(df["elapsed_sec"], df["mem_pct"], label=f"{sys_name} Memory", linewidth=2)
    plt.title("System Memory Utilization Over Time")
    plt.xlabel("Elapsed Time (seconds)")
    plt.ylabel("Memory Utilization (%)")
    plt.ylim(0, 105)
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.legend(loc="best", frameon=True)
    out_mem = os.path.join(out_dir, "sys_mem.png")
    plt.savefig(out_mem, dpi=300)
    print(f"[OK] Saved -> {out_mem}")
    plt.close()

    # 3. Network plot (KB/s)
    plt.figure(figsize=(10, 5.5))
    plotted_net = False
    for sys_name, df in system_data.items():
        if "bytes_sent" in df.columns and "bytes_recv" in df.columns:
            total_bytes = df["bytes_sent"] + df["bytes_recv"]
            dt = df["elapsed_sec"].diff().replace(0, np.nan)
            rate_kb_s = (total_bytes.diff() / dt / 1024.0).fillna(0)
            # Clip negative anomalies
            rate_kb_s = rate_kb_s.clip(lower=0)
            plt.plot(df["elapsed_sec"], rate_kb_s, label=f"{sys_name} Net (KB/s)", linewidth=2)
            plotted_net = True

    if plotted_net:
        plt.title("System Network Throughput Over Time")
        plt.xlabel("Elapsed Time (seconds)")
        plt.ylabel("Network Throughput (KB/s)")
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.legend(loc="best", frameon=True)
        out_net = os.path.join(out_dir, "sys_net.png")
        plt.savefig(out_net, dpi=300)
        print(f"[OK] Saved -> {out_net}")
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Generate evaluation report plots.")
    parser.add_argument("--results-dir", default="results", help="Directory containing experiment CSV/JSON outputs")
    parser.add_argument("--out", default="results/plots", help="Directory to save generated PNG plots")
    parser.add_argument("--experiment", default=None, help="Optional experiment filter")

    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print(f"\n📊 Generating Report Plots from '{args.results_dir}' → '{args.out}'...")
    plot_latency_cdf(args.results_dir, args.out, args.experiment)
    plot_latency_timeseries(args.results_dir, args.out, args.experiment)
    plot_throughput_timeseries(args.results_dir, args.out, args.experiment)
    plot_dropout_bar(args.results_dir, args.out)
    plot_system_metrics(args.results_dir, args.out)
    print("✨ Plot generation complete!\n")


if __name__ == "__main__":
    main()
