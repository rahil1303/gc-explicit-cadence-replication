#!/usr/bin/env python3
"""
parse_gc_log.py

Parses a JVM -Xlog:gc* log file and classifies each GC event as
natural or explicit (System.gc()-attributed), reporting counts,
durations, and heap before/after sizes for each category.

Usage:
    python3 parse_gc_log.py gc-h2-test.log
    python3 parse_gc_log.py gc-h2-test.log --csv out.csv
"""

import argparse
import re
import sys
import csv

# Matches lines like:
# [3.880s][info][gc             ] GC(15) Pause Full (System.gc()) 220M->60M(217M) 42.307ms
# [0.547s][info][gc          ] GC(0) Pause Young (Normal) (G1 Evacuation Pause) 24M->5M(130M) 4.654ms
EVENT_RE = re.compile(
    r"\[(?P<timestamp>[\d.]+)s\]\[info\]\[gc[^]]*\]\s+"
    r"GC\((?P<gc_id>\d+)\)\s+"
    r"(?P<cause>Pause (?:Young|Full).*?)\s+"
    r"(?P<before>\d+)(?P<before_unit>[KMG])->"
    r"(?P<after>\d+)(?P<after_unit>[KMG])"
    r"\((?P<capacity>\d+)(?P<capacity_unit>[KMG])\)\s+"
    r"(?P<duration_ms>[\d.]+)ms\s*$"
)


def classify(cause: str) -> str:
    cause = cause.strip()
    if "System.gc()" in cause:
        return "explicit"
    if "Pause Young" in cause:
        return "natural_young"
    if "Pause Full" in cause:
        return "natural_full"
    return "unknown"


def to_mb(value: int, unit: str) -> float:
    unit = unit.upper()
    if unit == "K":
        return value / 1024.0
    if unit == "M":
        return float(value)
    if unit == "G":
        return value * 1024.0
    return float(value)


SHENANDOAH_SUMMARY_RE = re.compile(
    r"(?P<count>\d+)\s+successful concurrent GCs"
)
SHENANDOAH_EXPLICIT_RE = re.compile(
    r"(?P<count>\d+)\s+invoked explicitly"
)
SHENANDOAH_IMPLICIT_RE = re.compile(
    r"(?P<count>\d+)\s+invoked implicitly"
)
SHENANDOAH_FULL_RE = re.compile(
    r"(?P<count>\d+)\s+Full GCs"
)
SHENANDOAH_TRIGGER_RE = re.compile(
    r"\[(?P<timestamp>[\d.]+)s\]\[info\]\[gc\s*\]\s+Trigger:\s+(?P<reason>.+)$"
)


def parse_shenandoah_summary(path: str):
    """
    Shenandoah/ZGC logs don't emit a single-line before->after(capacity)
    duration summary per event like G1/Serial/Parallel do. Instead they
    print each cycle as multiple phase lines plus a final aggregate
    summary block. We use that summary block (successful concurrent
    GCs / invoked explicitly / invoked implicitly / Full GCs), and the
    per-cycle "Trigger:" lines for a simple timeline of what caused
    each cycle.
    """
    concurrent_total = explicit_count = implicit_count = full_count = None
    triggers = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = SHENANDOAH_TRIGGER_RE.search(line)
            if m:
                triggers.append({
                    "timestamp_s": float(m.group("timestamp")),
                    "reason": m.group("reason").strip(),
                    "explicit": "System.gc()" in m.group("reason"),
                })
                continue
            m = SHENANDOAH_SUMMARY_RE.search(line)
            if m:
                concurrent_total = int(m.group("count"))
                continue
            m = SHENANDOAH_EXPLICIT_RE.search(line)
            if m and explicit_count is None:
                explicit_count = int(m.group("count"))
                continue
            m = SHENANDOAH_IMPLICIT_RE.search(line)
            if m and implicit_count is None:
                implicit_count = int(m.group("count"))
                continue
            m = SHENANDOAH_FULL_RE.search(line)
            if m and full_count is None:
                full_count = int(m.group("count"))
                continue
    return {
        "concurrent_total": concurrent_total,
        "explicit_count": explicit_count,
        "implicit_count": implicit_count,
        "full_count": full_count,
        "triggers": triggers,
    }


ZGC_EVENT_RE = re.compile(
    r"\[(?P<timestamp>[\d.]+)s\]\[info\]\[gc\s*\]\s+"
    r"GC\((?P<gc_id>\d+)\)\s+Garbage Collection\s+\((?P<cause>.+?)\)\s+"
    r"(?P<before>\d+)(?P<before_unit>[KMG])\([\d.]+%\)->"
    r"(?P<after>\d+)(?P<after_unit>[KMG])\([\d.]+%\)\s*$"
)


def classify_zgc(cause: str) -> str:
    cause = cause.strip()
    if "System.gc()" in cause:
        return "explicit"
    return "natural_" + cause.lower().replace(" ", "_")


def parse_zgc_log(path: str):
    events = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = ZGC_EVENT_RE.search(line)
            if not m:
                continue
            d = m.groupdict()
            events.append({
                "gc_id": int(d["gc_id"]),
                "timestamp_s": float(d["timestamp"]),
                "cause": d["cause"].strip(),
                "category": classify_zgc(d["cause"]),
                "before_mb": to_mb(int(d["before"]), d["before_unit"]),
                "after_mb": to_mb(int(d["after"]), d["after_unit"]),
                "capacity_mb": None,
                "duration_ms": None,
            })
    return events


def parse_log(path: str):
    events = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = EVENT_RE.search(line)
            if not m:
                continue
            d = m.groupdict()
            events.append({
                "gc_id": int(d["gc_id"]),
                "timestamp_s": float(d["timestamp"]),
                "cause": d["cause"].strip(),
                "category": classify(d["cause"]),
                "before_mb": to_mb(int(d["before"]), d["before_unit"]),
                "after_mb": to_mb(int(d["after"]), d["after_unit"]),
                "capacity_mb": to_mb(int(d["capacity"]), d["capacity_unit"]),
                "duration_ms": float(d["duration_ms"]),
            })
    return events


def summarize(events):
    summary = {}
    for e in events:
        cat = e["category"]
        s = summary.setdefault(cat, {
            "count": 0, "total_duration_ms": 0.0,
            "max_duration_ms": 0.0, "total_reclaimed_mb": 0.0,
        })
        s["count"] += 1
        s["total_duration_ms"] += e["duration_ms"]
        s["max_duration_ms"] = max(s["max_duration_ms"], e["duration_ms"])
        s["total_reclaimed_mb"] += max(0.0, e["before_mb"] - e["after_mb"])
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", help="Path to -Xlog:gc* log file")
    ap.add_argument("--csv", help="Optional path to write per-event CSV")
    args = ap.parse_args()

    events = parse_log(args.logfile)
    if not events:
        zgc_events = parse_zgc_log(args.logfile)
        if zgc_events:
            print(f"Parsed {len(zgc_events)} ZGC-style GC events from {args.logfile}\n")
            summary = {}
            for e in zgc_events:
                cat = e["category"]
                s = summary.setdefault(cat, {"count": 0, "total_reclaimed_mb": 0.0})
                s["count"] += 1
                s["total_reclaimed_mb"] += max(0.0, e["before_mb"] - e["after_mb"])
            print(f"{'Category':<20} {'Count':>6} {'Reclaimed MB':>14}")
            for cat, s in sorted(summary.items()):
                print(f"{cat:<20} {s['count']:>6} {s['total_reclaimed_mb']:>14.1f}")
            explicit_count = summary.get("explicit", {}).get("count", 0)
            print(f"\nExplicit (System.gc()) requests realised as ZGC cycles: {explicit_count}")
            if args.csv:
                with open(args.csv, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=[
                        "gc_id", "timestamp_s", "cause", "category",
                        "before_mb", "after_mb", "capacity_mb", "duration_ms",
                    ])
                    writer.writeheader()
                    for e in zgc_events:
                        writer.writerow(e)
                print(f"\nPer-event data written to {args.csv}")
            return

        # Try Shenandoah-style summary block instead before giving up.
        summary_data = parse_shenandoah_summary(args.logfile)
        if summary_data["concurrent_total"] is None:
            print(f"No GC events matched in {args.logfile}. "
                  f"Check the log format / -Xlog options used.", file=sys.stderr)
            sys.exit(1)

        print(f"Parsed Shenandoah/ZGC-style summary from {args.logfile}\n")
        explicit = summary_data["explicit_count"] or 0
        implicit = summary_data["implicit_count"] or 0
        total = summary_data["concurrent_total"] or 0
        natural = total - explicit - implicit
        full = summary_data["full_count"] or 0
        print(f"Concurrent GCs total: {total}")
        print(f"  explicit (System.gc()):  {explicit}")
        print(f"  implicit (heuristic):    {implicit}")
        print(f"  natural (other triggers):{natural}")
        print(f"Full GCs: {full}")
        print(f"\nExplicit (System.gc()) requests realised as concurrent GC cycles: {explicit}")

        if summary_data["triggers"]:
            print(f"\nTrigger timeline ({len(summary_data['triggers'])} cycles):")
            for t in summary_data["triggers"]:
                tag = "explicit" if t["explicit"] else "natural"
                print(f"  [{t['timestamp_s']:.3f}s] {tag}: {t['reason']}")

        if args.csv:
            with open(args.csv, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["timestamp_s", "reason", "explicit"])
                writer.writeheader()
                for t in summary_data["triggers"]:
                    writer.writerow(t)
            print(f"\nPer-trigger data written to {args.csv}")
        return

    summary = summarize(events)

    print(f"Parsed {len(events)} GC events from {args.logfile}\n")
    print(f"{'Category':<15} {'Count':>6} {'Total ms':>10} {'Max ms':>10} {'Reclaimed MB':>14}")
    for cat, s in sorted(summary.items()):
        print(f"{cat:<15} {s['count']:>6} {s['total_duration_ms']:>10.2f} "
              f"{s['max_duration_ms']:>10.2f} {s['total_reclaimed_mb']:>14.1f}")

    explicit_count = summary.get("explicit", {}).get("count", 0)
    print(f"\nExplicit (System.gc()) requests realised as Full GC: {explicit_count}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "gc_id", "timestamp_s", "cause", "category",
                "before_mb", "after_mb", "capacity_mb", "duration_ms",
            ])
            writer.writeheader()
            for e in events:
                writer.writerow(e)
        print(f"\nPer-event data written to {args.csv}")


if __name__ == "__main__":
    main()