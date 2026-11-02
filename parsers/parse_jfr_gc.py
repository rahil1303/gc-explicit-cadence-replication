"""Summarise JFR GC events, pauses and observed net heap-use reductions.

Usage:
    python3 parse_jfr_gc.py recording.jfr

Missing measurements are emitted as 'nan', not as measured zero.

Existing *_reclaimed_mb names are retained for RunnerConfig compatibility.
These fields represent signed net heap-use reductions associated with GC
events, not exact garbage reclaimed.
"""

from pathlib import Path
import math
import re
import subprocess
import sys


SIZE_FACTORS_MB = {
    "B": 1 / (1024 * 1024),
    "KB": 1 / 1024,
    "KIB": 1 / 1024,
    "MB": 1.0,
    "MIB": 1.0,
    "GB": 1024.0,
    "GIB": 1024.0,
    "TB": 1024.0 ** 2,
    "TIB": 1024.0 ** 2,
}

DURATION_FACTORS_MS = {
    "NS": 1 / 1_000_000,
    "US": 1 / 1_000,
    "MS": 1.0,
    "S": 1000.0,
}

NUMBER_AND_UNIT = re.compile(
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]+)"
)


def parse_quantity(value, factors, kind):
    normalised = (
        value.strip()
        .replace("µ", "u")
        .replace("μ", "u")
    )

    match = NUMBER_AND_UNIT.fullmatch(normalised)

    if not match:
        raise ValueError(f"Unsupported {kind}: {value}")

    number = float(match.group(1))
    unit = match.group(2).upper()

    if kind == "size" and unit in {"BYTE", "BYTES"}:
        unit = "B"

    if unit not in factors:
        raise ValueError(f"Unsupported {kind} unit: {value}")

    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Invalid {kind}: {value}")

    return number * factors[unit]


def parse_size_mb(value):
    return parse_quantity(value, SIZE_FACTORS_MB, "size")


def parse_duration_ms(value):
    return parse_quantity(value, DURATION_FACTORS_MS, "duration")


def dump_jfr(jfr_file):
    return subprocess.run(
        [
            "jfr",
            "print",
            "--events",
            "jdk.GarbageCollection,jdk.GCHeapSummary",
            str(jfr_file),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def parse_events(text):
    heap = {}
    gc_events = []

    current_type = None
    current = {}
    brace_depth = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if current_type is None:
            if line.startswith("jdk.GCHeapSummary {"):
                current_type = "heap"
            elif line.startswith("jdk.GarbageCollection {"):
                current_type = "gc"
            else:
                continue

            current = {}
            brace_depth = 1
            continue

        # Read only top-level event fields, not nested object fields.
        if brace_depth == 1 and "=" in line:
            key, value = (
                part.strip()
                for part in line.split("=", 1)
            )

            if key == "gcId":
                current["gcId"] = int(value)

            elif key in {"when", "name", "cause", "startTime"}:
                current[key] = value.strip('"')

            elif key == "heapUsed":
                current["heapUsedMB"] = parse_size_mb(value)

            elif key == "duration":
                current["durationMs"] = parse_duration_ms(value)

            elif key == "sumOfPauses":
                current["sumOfPausesMs"] = parse_duration_ms(value)

        brace_depth += line.count("{") - line.count("}")

        if brace_depth == 0:
            if current_type == "heap":
                gc_id = current.get("gcId")
                when = current.get("when")

                if gc_id is not None and when:
                    heap.setdefault(gc_id, {})[when] = current

            elif current_type == "gc":
                gc_events.append(current)

            current_type = None
            current = {}

    if current_type is not None:
        raise ValueError("Incomplete event in jfr print output")

    return heap, gc_events


def summarise(heap, gc_events):
    explicit = [
        event
        for event in gc_events
        if event.get("cause") == "System.gc()"
    ]

    natural = [
        event
        for event in gc_events
        if (
            event.get("cause") is not None
            and event.get("cause") != "System.gc()"
        )
    ]

    unknown = [
        event
        for event in gc_events
        if not event.get("cause")
    ]

    result = {}

    for prefix, events in (
        ("total", gc_events),
        ("explicit", explicit),
        ("natural", natural),
    ):
        result[f"{prefix}_gc_events"] = len(events)

        pauses = [
            event["sumOfPausesMs"]
            for event in events
            if event.get("sumOfPausesMs") is not None
        ]
        missing_pauses = len(events) - len(pauses)

        # Never replace pause time with whole-cycle duration.
        # If any pause measurement is missing, the aggregate is unavailable.
        result[f"{prefix}_gc_pause_ms"] = (
            float("nan")
            if missing_pauses
            else round(sum(pauses), 6)
        )

        durations = [
            event["durationMs"]
            for event in events
            if event.get("durationMs") is not None
        ]

        result[f"{prefix}_gc_duration_ms"] = (
            float("nan")
            if len(durations) != len(events)
            else round(sum(durations), 6)
        )

        reductions = []

        for event in events:
            pair = heap.get(event.get("gcId"), {})

            before = pair.get("Before GC", {}).get("heapUsedMB")
            after = pair.get("After GC", {}).get("heapUsedMB")

            if before is not None and after is not None:
                reductions.append(before - after)

        missing_pairs = len(events) - len(reductions)

        result[f"{prefix}_reclaimed_mb"] = (
            float("nan")
            if missing_pairs
            else round(sum(reductions), 6)
        )

        # Preserve the coverage names used by the hardened parser.
        coverage_prefix = "" if prefix == "total" else prefix + "_"

        result[f"{coverage_prefix}gc_events_with_heap_pair"] = (
            len(reductions)
        )
        result[f"{coverage_prefix}gc_events_missing_heap_pair"] = (
            missing_pairs
        )
        result[f"{coverage_prefix}gc_events_with_pause"] = len(pauses)
        result[f"{coverage_prefix}gc_events_missing_pause"] = (
            missing_pauses
        )

    result["unknown_gc_events"] = len(unknown)
    result["gc_events_missing_cause"] = len(unknown)

    return result


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python3 parse_jfr_gc.py <recording.jfr>",
            file=sys.stderr,
        )
        return 1

    recording = Path(sys.argv[1])

    if not recording.is_file():
        raise FileNotFoundError(recording)

    heap, events = parse_events(dump_jfr(recording))

    if not events:
        print(
            "Warning: no jdk.GarbageCollection events found; verify recording "
            "coverage before treating this as a zero-GC run.",
            file=sys.stderr,
        )

    summary = summarise(heap, events)

    for field in (
        "gc_events_missing_heap_pair",
        "gc_events_missing_pause",
        "gc_events_missing_cause",
    ):
        if summary[field]:
            print(
                f"Warning: {field}={summary[field]}",
                file=sys.stderr,
            )

    for key, value in summary.items():
        print(f"{key}: {value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
