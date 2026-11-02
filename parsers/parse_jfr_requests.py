"""Parse explicit System.gc() requests and the GC work associated with them.

One output row represents one jdk.SystemGC request.

The parser associates jdk.GarbageCollection events whose cause is
"System.gc()" with the SystemGC request whose execution interval contains
the GC event start time.

This deliberately keeps the request as the unit of observation:
for example, Parallel GC may produce both ParallelScavenge and ParallelOld
for one System.gc() request, but that still produces one output row.

Usage:
    python3 parse_jfr_requests.py recording.jfr
    python3 parse_jfr_requests.py recording.jfr output.csv
"""

from pathlib import Path
from datetime import datetime, timedelta
import csv
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

TIMESTAMP_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2})(?:\.(\d+))?\s+\((\d{4}-\d{2}-\d{2})\)"
)

# Small tolerance for timestamp formatting/rounding at request boundaries.
MATCH_TOLERANCE_MS = 2.0


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


def parse_timestamp(value):
    match = TIMESTAMP_RE.fullmatch(value.strip())

    if not match:
        raise ValueError(f"Unsupported timestamp: {value}")

    clock = match.group(1)
    fraction = match.group(2) or ""
    date = match.group(3)

    # datetime supports microseconds, so pad/truncate fractional seconds.
    microseconds = (fraction + "000000")[:6]

    return datetime.strptime(
        f"{date} {clock}.{microseconds}",
        "%Y-%m-%d %H:%M:%S.%f",
    )


def dump_jfr(jfr_file):
    return subprocess.run(
        [
            "jfr",
            "print",
            "--events",
            "jdk.SystemGC,jdk.GarbageCollection,jdk.GCHeapSummary",
            str(jfr_file),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def parse_events(text):
    heap = {}
    gc_events = []
    requests = []

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
            elif line.startswith("jdk.SystemGC {"):
                current_type = "request"
            else:
                continue

            current = {}
            brace_depth = 1
            continue

        # Read only top-level event fields.
        if brace_depth == 1 and "=" in line:
            key, value = (
                part.strip()
                for part in line.split("=", 1)
            )

            if key == "gcId":
                current["gcId"] = int(value)

            elif key in {"when", "name", "cause"}:
                current[key] = value.strip('"')

            elif key == "startTime":
                raw_value = value.strip('"')
                current["startTimeRaw"] = raw_value
                current["startTime"] = parse_timestamp(raw_value)

            elif key == "duration":
                current["durationMs"] = parse_duration_ms(value)

            elif key == "sumOfPauses":
                current["sumOfPausesMs"] = parse_duration_ms(value)

            elif key == "heapUsed":
                current["heapUsedMB"] = parse_size_mb(value)

            elif key == "invokedConcurrent":
                current["invokedConcurrent"] = (
                    value.lower() == "true"
                )

        brace_depth += line.count("{") - line.count("}")

        if brace_depth == 0:
            if current_type == "heap":
                gc_id = current.get("gcId")
                when = current.get("when")

                if gc_id is not None and when:
                    heap.setdefault(gc_id, {})[when] = current

            elif current_type == "gc":
                gc_events.append(current)

            elif current_type == "request":
                requests.append(current)

            current_type = None
            current = {}

    if current_type is not None:
        raise ValueError("Incomplete event in jfr print output")

    gc_events.sort(
        key=lambda event: event.get("startTime", datetime.max)
    )

    requests.sort(
        key=lambda event: event.get("startTime", datetime.max)
    )

    return heap, gc_events, requests


def event_end(event):
    start = event.get("startTime")
    duration = event.get("durationMs")

    if start is None or duration is None:
        return None

    return start + timedelta(milliseconds=duration)


def match_explicit_events(requests, gc_events):
    explicit_events = [
        event
        for event in gc_events
        if event.get("cause") == "System.gc()"
    ]

    assignments = {index: [] for index in range(len(requests))}
    unmatched_events = []

    tolerance = timedelta(milliseconds=MATCH_TOLERANCE_MS)

    for event in explicit_events:
        event_start = event.get("startTime")

        if event_start is None:
            unmatched_events.append(event)
            continue

        candidates = []

        for index, request in enumerate(requests):
            request_start = request.get("startTime")
            request_end = event_end(request)

            if request_start is None or request_end is None:
                continue

            if (
                request_start - tolerance
                <= event_start
                <= request_end + tolerance
            ):
                distance_ms = abs(
                    (event_start - request_start).total_seconds()
                    * 1000.0
                )

                candidates.append((distance_ms, index))

        if not candidates:
            unmatched_events.append(event)
            continue

        # If intervals ever overlap, choose the request whose start is
        # nearest to this GC event.
        _, best_index = min(candidates)
        assignments[best_index].append(event)

    for events in assignments.values():
        events.sort(
            key=lambda event: event.get("startTime", datetime.max)
        )

    return assignments, unmatched_events


def fmt_number(value, digits=6):
    if value is None:
        return "nan"

    if isinstance(value, float) and math.isnan(value):
        return "nan"

    return round(value, digits)


def build_rows(heap, gc_events, requests):
    assignments, unmatched_events = match_explicit_events(
        requests,
        gc_events,
    )

    rows = []

    for request_index, request in enumerate(requests, start=1):
        matched = assignments[request_index - 1]

        gc_ids = [
            str(event["gcId"])
            for event in matched
            if event.get("gcId") is not None
        ]

        gc_names = [
            event.get("name", "")
            for event in matched
        ]

        durations = [
            event["durationMs"]
            for event in matched
            if event.get("durationMs") is not None
        ]

        pauses = [
            event["sumOfPausesMs"]
            for event in matched
            if event.get("sumOfPausesMs") is not None
        ]

        event_reductions = []
        heap_pairs = []

        for event in matched:
            gc_id = event.get("gcId")
            pair = heap.get(gc_id, {})

            before = pair.get("Before GC", {}).get("heapUsedMB")
            after = pair.get("After GC", {}).get("heapUsedMB")

            if before is not None and after is not None:
                event_reductions.append(before - after)
                heap_pairs.append((before, after))

        missing_heap_pairs = len(matched) - len(heap_pairs)

        if matched and heap_pairs and missing_heap_pairs == 0:
            first_gc_id = matched[0].get("gcId")
            last_gc_id = matched[-1].get("gcId")

            first_pair = heap.get(first_gc_id, {})
            last_pair = heap.get(last_gc_id, {})

            request_heap_before = (
                first_pair
                .get("Before GC", {})
                .get("heapUsedMB")
            )

            request_heap_after = (
                last_pair
                .get("After GC", {})
                .get("heapUsedMB")
            )

            if (
                request_heap_before is not None
                and request_heap_after is not None
            ):
                request_net_reduction = (
                    request_heap_before - request_heap_after
                )
            else:
                request_net_reduction = float("nan")

        else:
            request_heap_before = float("nan")
            request_heap_after = float("nan")
            request_net_reduction = float("nan")

        if matched:
            first_gc_start = matched[0].get("startTime")

            if first_gc_start is not None:
                latency_ms = (
                    first_gc_start - request["startTime"]
                ).total_seconds() * 1000.0
            else:
                latency_ms = float("nan")
        else:
            latency_ms = float("nan")

        rows.append({
            "request_index": request_index,
            "request_start_time": request.get(
                "startTimeRaw",
                "",
            ),
            "request_duration_ms": fmt_number(
                request.get("durationMs")
            ),
            "invoked_concurrent": request.get(
                "invokedConcurrent",
                "",
            ),
            "realised": 1 if matched else 0,
            "first_gc_latency_ms": fmt_number(latency_ms),
            "gc_event_count": len(matched),
            "gc_ids": "|".join(gc_ids),
            "gc_names": "|".join(gc_names),
            "gc_total_duration_ms": (
                fmt_number(sum(durations))
                if len(durations) == len(matched)
                else "nan"
            ),
            "gc_total_pause_ms": (
                fmt_number(sum(pauses))
                if len(pauses) == len(matched)
                else "nan"
            ),
            "heap_before_mb": fmt_number(
                request_heap_before
            ),
            "heap_after_mb": fmt_number(
                request_heap_after
            ),
            "observed_net_heap_reduction_mb": fmt_number(
                request_net_reduction
            ),
            "sum_event_heap_reduction_mb": (
                fmt_number(sum(event_reductions))
                if len(event_reductions) == len(matched)
                else "nan"
            ),
            "gc_events_with_heap_pair": len(heap_pairs),
            "gc_events_missing_heap_pair": missing_heap_pairs,
        })

    return rows, unmatched_events


FIELDNAMES = [
    "request_index",
    "request_start_time",
    "request_duration_ms",
    "invoked_concurrent",
    "realised",
    "first_gc_latency_ms",
    "gc_event_count",
    "gc_ids",
    "gc_names",
    "gc_total_duration_ms",
    "gc_total_pause_ms",
    "heap_before_mb",
    "heap_after_mb",
    "observed_net_heap_reduction_mb",
    "sum_event_heap_reduction_mb",
    "gc_events_with_heap_pair",
    "gc_events_missing_heap_pair",
]


def write_csv(rows, output):
    writer = csv.DictWriter(
        output,
        fieldnames=FIELDNAMES,
    )

    writer.writeheader()
    writer.writerows(rows)


def main():
    if len(sys.argv) not in {2, 3}:
        print(
            "Usage: python3 parse_jfr_requests.py "
            "<recording.jfr> [output.csv]",
            file=sys.stderr,
        )
        return 1

    recording = Path(sys.argv[1])

    if not recording.is_file():
        raise FileNotFoundError(recording)

    heap, gc_events, requests = parse_events(
        dump_jfr(recording)
    )

    rows, unmatched_events = build_rows(
        heap,
        gc_events,
        requests,
    )

    explicit_event_count = sum(
        1
        for event in gc_events
        if event.get("cause") == "System.gc()"
    )

    realised_count = sum(
        row["realised"]
        for row in rows
    )

    print(
        f"SystemGC requests: {len(requests)}",
        file=sys.stderr,
    )

    print(
        f"Realised requests: {realised_count}",
        file=sys.stderr,
    )

    print(
        f"System.gc()-caused GC events: {explicit_event_count}",
        file=sys.stderr,
    )

    print(
        f"Unmatched System.gc()-caused GC events: "
        f"{len(unmatched_events)}",
        file=sys.stderr,
    )

    if unmatched_events:
        for event in unmatched_events:
            print(
                "Warning: unmatched explicit GC event "
                f"gcId={event.get('gcId')} "
                f"name={event.get('name')} "
                f"startTime={event.get('startTimeRaw')}",
                file=sys.stderr,
            )

    if len(sys.argv) == 3:
        output_file = Path(sys.argv[2])

        with output_file.open(
            "w",
            newline="",
        ) as output:
            write_csv(rows, output)

        print(
            f"Wrote {len(rows)} request rows to "
            f"{output_file}",
            file=sys.stderr,
        )

    else:
        write_csv(rows, sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
