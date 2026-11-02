"""Add between-request non-explicit GC context to explicit System.gc() requests.

One output row represents one jdk.SystemGC request.

For every explicit request, this parser reports:

1. The explicit GC work associated with that request.
2. Non-System.gc() GC activity since the previous explicit request ended.
3. Non-System.gc() GC activity after the request ended and before the next
   explicit request begins.

For the first request, the "previous" window contains non-explicit GC events
seen earlier in the JFR recording.

For the last request, the "next" window contains non-explicit GC events seen
later in the JFR recording.

"Natural" is retained here for compatibility with the existing study code,
but technically means any jdk.GarbageCollection event whose cause is present
and is not "System.gc()".

Usage:
    python3 parse_jfr_request_context.py recording.jfr
    python3 parse_jfr_request_context.py recording.jfr output.csv
"""

from pathlib import Path
import csv
import math
import sys

import parse_jfr_requests as base


FIELDNAMES = [
    # Explicit request
    "request_index",
    "request_start_time",
    "request_duration_ms",
    "invoked_concurrent",
    "realised",
    "first_gc_latency_ms",
    "explicit_gc_event_count",
    "explicit_gc_ids",
    "explicit_gc_names",
    "explicit_gc_total_duration_ms",
    "explicit_gc_total_pause_ms",
    "explicit_heap_before_mb",
    "explicit_heap_after_mb",
    "explicit_observed_net_heap_reduction_mb",

    # Timing between explicit requests
    "ms_since_previous_request_end",
    "ms_until_next_request_start",

    # Non-explicit GC before this request
    "natural_before_event_count",
    "natural_before_gc_ids",
    "natural_before_gc_names",
    "natural_before_gc_causes",
    "natural_before_duration_ms",
    "natural_before_pause_ms",
    "natural_before_observed_reduction_mb",
    "natural_before_events_with_heap_pair",
    "natural_before_events_missing_heap_pair",

    # Non-explicit GC after this request, before next request
    "natural_after_event_count",
    "natural_after_gc_ids",
    "natural_after_gc_names",
    "natural_after_gc_causes",
    "natural_after_duration_ms",
    "natural_after_pause_ms",
    "natural_after_observed_reduction_mb",
    "natural_after_events_with_heap_pair",
    "natural_after_events_missing_heap_pair",
]


def is_natural(event):
    cause = event.get("cause")
    return (
        cause is not None
        and cause != "System.gc()"
    )


def finite_number(value):
    if value is None:
        return False

    if isinstance(value, float):
        return math.isfinite(value)

    return True


def round_or_nan(value, digits=6):
    if not finite_number(value):
        return "nan"

    return round(float(value), digits)


def event_end(event):
    return base.event_end(event)


def natural_summary(events, heap):
    gc_ids = []
    names = []
    causes = []

    durations = []
    pauses = []
    reductions = []

    heap_pair_count = 0

    for event in events:
        gc_id = event.get("gcId")

        if gc_id is not None:
            gc_ids.append(str(gc_id))

        names.append(event.get("name", ""))
        causes.append(event.get("cause", ""))

        duration = event.get("durationMs")
        if duration is not None:
            durations.append(duration)

        pause = event.get("sumOfPausesMs")
        if pause is not None:
            pauses.append(pause)

        pair = heap.get(gc_id, {})
        before = (
            pair.get("Before GC", {})
            .get("heapUsedMB")
        )
        after = (
            pair.get("After GC", {})
            .get("heapUsedMB")
        )

        if before is not None and after is not None:
            reductions.append(before - after)
            heap_pair_count += 1

    event_count = len(events)

    if len(durations) == event_count:
        total_duration = round(sum(durations), 6)
    else:
        total_duration = "nan"

    if len(pauses) == event_count:
        total_pause = round(sum(pauses), 6)
    else:
        total_pause = "nan"

    if len(reductions) == event_count:
        total_reduction = round(sum(reductions), 6)
    else:
        total_reduction = "nan"

    return {
        "event_count": event_count,
        "gc_ids": "|".join(gc_ids),
        "gc_names": "|".join(names),
        "gc_causes": "|".join(causes),
        "duration_ms": total_duration,
        "pause_ms": total_pause,
        "observed_reduction_mb": total_reduction,
        "events_with_heap_pair": heap_pair_count,
        "events_missing_heap_pair":
            event_count - heap_pair_count,
    }


def build_context_rows(heap, gc_events, requests):
    explicit_rows, unmatched = base.build_rows(
        heap,
        gc_events,
        requests,
    )

    natural_events = [
        event
        for event in gc_events
        if is_natural(event)
        and event.get("startTime") is not None
    ]

    rows = []

    for i, request in enumerate(requests):
        request_start = request.get("startTime")
        request_end = event_end(request)

        if request_start is None or request_end is None:
            raise ValueError(
                f"Request {i + 1} missing start/duration"
            )

        previous_end = None
        if i > 0:
            previous_end = event_end(
                requests[i - 1]
            )

        next_start = None
        if i + 1 < len(requests):
            next_start = requests[i + 1].get(
                "startTime"
            )

        # Natural GC before current request.
        #
        # For request 1:
        #     all natural events before request start.
        #
        # For later requests:
        #     events starting after previous SystemGC request ended
        #     and before current request begins.
        natural_before = []

        for event in natural_events:
            start = event["startTime"]

            if i == 0:
                if start < request_start:
                    natural_before.append(event)
            else:
                if (
                    previous_end is not None
                    and previous_end < start < request_start
                ):
                    natural_before.append(event)

        # Natural GC after current explicit request.
        #
        # For non-last requests:
        #     after current request end, before next request start.
        #
        # For final request:
        #     all later natural events remaining in recording.
        natural_after = []

        for event in natural_events:
            start = event["startTime"]

            if i + 1 < len(requests):
                if (
                    next_start is not None
                    and request_end < start < next_start
                ):
                    natural_after.append(event)
            else:
                if start > request_end:
                    natural_after.append(event)

        before = natural_summary(
            natural_before,
            heap,
        )

        after = natural_summary(
            natural_after,
            heap,
        )

        if previous_end is None:
            since_previous_ms = "nan"
        else:
            since_previous_ms = round(
                (
                    request_start - previous_end
                ).total_seconds()
                * 1000.0,
                6,
            )

        if next_start is None:
            until_next_ms = "nan"
        else:
            until_next_ms = round(
                (
                    next_start - request_end
                ).total_seconds()
                * 1000.0,
                6,
            )

        explicit = explicit_rows[i]

        row = {
            "request_index":
                explicit["request_index"],
            "request_start_time":
                explicit["request_start_time"],
            "request_duration_ms":
                explicit["request_duration_ms"],
            "invoked_concurrent":
                explicit["invoked_concurrent"],
            "realised":
                explicit["realised"],
            "first_gc_latency_ms":
                explicit["first_gc_latency_ms"],
            "explicit_gc_event_count":
                explicit["gc_event_count"],
            "explicit_gc_ids":
                explicit["gc_ids"],
            "explicit_gc_names":
                explicit["gc_names"],
            "explicit_gc_total_duration_ms":
                explicit["gc_total_duration_ms"],
            "explicit_gc_total_pause_ms":
                explicit["gc_total_pause_ms"],
            "explicit_heap_before_mb":
                explicit["heap_before_mb"],
            "explicit_heap_after_mb":
                explicit["heap_after_mb"],
            "explicit_observed_net_heap_reduction_mb":
                explicit[
                    "observed_net_heap_reduction_mb"
                ],

            "ms_since_previous_request_end":
                since_previous_ms,
            "ms_until_next_request_start":
                until_next_ms,

            "natural_before_event_count":
                before["event_count"],
            "natural_before_gc_ids":
                before["gc_ids"],
            "natural_before_gc_names":
                before["gc_names"],
            "natural_before_gc_causes":
                before["gc_causes"],
            "natural_before_duration_ms":
                before["duration_ms"],
            "natural_before_pause_ms":
                before["pause_ms"],
            "natural_before_observed_reduction_mb":
                before["observed_reduction_mb"],
            "natural_before_events_with_heap_pair":
                before["events_with_heap_pair"],
            "natural_before_events_missing_heap_pair":
                before["events_missing_heap_pair"],

            "natural_after_event_count":
                after["event_count"],
            "natural_after_gc_ids":
                after["gc_ids"],
            "natural_after_gc_names":
                after["gc_names"],
            "natural_after_gc_causes":
                after["gc_causes"],
            "natural_after_duration_ms":
                after["duration_ms"],
            "natural_after_pause_ms":
                after["pause_ms"],
            "natural_after_observed_reduction_mb":
                after["observed_reduction_mb"],
            "natural_after_events_with_heap_pair":
                after["events_with_heap_pair"],
            "natural_after_events_missing_heap_pair":
                after["events_missing_heap_pair"],
        }

        rows.append(row)

    return rows, unmatched


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
            "Usage: python3 "
            "parse_jfr_request_context.py "
            "<recording.jfr> [output.csv]",
            file=sys.stderr,
        )
        return 1

    recording = Path(sys.argv[1])

    if not recording.is_file():
        raise FileNotFoundError(recording)

    heap, gc_events, requests = (
        base.parse_events(
            base.dump_jfr(recording)
        )
    )

    rows, unmatched = build_context_rows(
        heap,
        gc_events,
        requests,
    )

    print(
        f"SystemGC requests: {len(requests)}",
        file=sys.stderr,
    )

    print(
        f"Context rows: {len(rows)}",
        file=sys.stderr,
    )

    print(
        f"Unmatched explicit GC events: "
        f"{len(unmatched)}",
        file=sys.stderr,
    )

    print(
        "Natural GC events in recording: "
        f"{sum(is_natural(e) for e in gc_events)}",
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
            f"Wrote {len(rows)} rows to "
            f"{output_file}",
            file=sys.stderr,
        )

    else:
        write_csv(rows, sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
