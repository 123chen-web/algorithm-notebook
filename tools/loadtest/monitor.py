"""压测期间资源采样：每 5 秒记录目标进程的 CPU%、内存 RSS，并统计
uvicorn 日志里新增的 "database is locked" / 5xx 行数。

用法: python monitor.py <pid> <uvicorn_log> <out_csv> <duration_seconds>
"""
import csv
import sys
import time

import psutil


def count_markers(path, markers):
    counts = {m: 0 for m in markers}
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                for m in markers:
                    if m in line:
                        counts[m] += 1
    except FileNotFoundError:
        pass
    return counts


def main():
    pid = int(sys.argv[1])
    log_path = sys.argv[2]
    out_csv = sys.argv[3]
    duration = int(sys.argv[4])
    markers = ["database is locked", " 500 ", "Internal Server Error"]
    proc = psutil.Process(pid)
    proc.cpu_percent(interval=None)
    start_counts = count_markers(log_path, markers)
    t0 = time.time()
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_s", "cpu_percent", "rss_mb", "threads",
                    "locked_new", "http500_new"])
        while time.time() - t0 < duration:
            time.sleep(5)
            elapsed = int(time.time() - t0)
            try:
                cpu = proc.cpu_percent(interval=None)
                rss = proc.memory_info().rss / 1024 / 1024
                threads = proc.num_threads()
            except psutil.NoSuchProcess:
                cpu, rss, threads = -1, -1, -1
            cur = count_markers(log_path, markers)
            w.writerow([elapsed, round(cpu, 1), round(rss, 1), threads,
                        cur["database is locked"] - start_counts["database is locked"],
                        (cur[" 500 "] + cur["Internal Server Error"])
                        - (start_counts[" 500 "] + start_counts["Internal Server Error"])])
            f.flush()
    print(f"monitor done -> {out_csv}")


if __name__ == "__main__":
    main()
