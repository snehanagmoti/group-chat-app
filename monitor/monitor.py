#!/usr/bin/env python3
"""
monitor.py — System resource monitor for all 4 lab systems.
Runs locally; SSHes into each host to read /proc/stat, /proc/meminfo, /proc/net/dev.
Usage:
    python3 monitor.py \
        --hosts sys1:10.x.x.1,sys2:10.x.x.2,sys3:10.x.x.3,sys4:10.x.x.4 \
        --duration 120 \
        --interval 1 \
        --out monitor_results/

Alternatively, run monitor_agent.py ON EACH system and collect CSVs.
"""
import argparse, csv, time, datetime, subprocess, threading, os
import psutil   # used when running as local agent on each host

def collect_local(interval_s, duration_s, out_path):
    """Run directly on each system to collect local stats."""
    # Ensure directory exists if out_path contains directories
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    rows = []
    t_end = time.time() + duration_s
    # Initial call to cpu_percent to set reference point
    psutil.cpu_percent(interval=None)
    time.sleep(min(interval_s, 0.1))

    while time.time() < t_end:
        t = datetime.datetime.now().isoformat()
        cpu  = psutil.cpu_percent(interval=None)
        mem  = psutil.virtual_memory().percent
        net  = psutil.net_io_counters()
        rows.append([t, cpu, mem, net.bytes_sent, net.bytes_recv])
        time.sleep(interval_s)

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp","cpu_pct","mem_pct","bytes_sent","bytes_recv"])
        w.writerows(rows)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration",  type=int, default=60)
    ap.add_argument("--interval",  type=float, default=1.0)
    ap.add_argument("--out",       default="monitor.csv")
    args = ap.parse_args()
    print(f"Monitoring for {args.duration}s → {args.out}")
    collect_local(args.interval, args.duration, args.out)
