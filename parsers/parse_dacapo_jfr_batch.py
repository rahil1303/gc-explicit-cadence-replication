#!/usr/bin/env python3

"""Batch JFR analysis for the recovered DaCapo dataset.

Uses run_table_recovered.csv as the authority.

Only rows marked DONE in that recovered table are parsed.
The 12 genuine benchmark failures remain represented in the
run summary as SKIPPED_NOT_DONE and do not contribute request rows.

Outputs:
    request_level.csv
    request_context.csv
    run_jfr_summary.csv

Usage:
    python3 parse_dacapo_jfr_batch.py \
        <experiment_root> \
        <recovered_run_table.csv> \
        <output_dir>
"""

from pathlib import Path
import csv
import math
import re
import sys

import parse_jfr_requests as request_parser
import parse_jfr_request_context as context_parser


RUN_ID_RE = re.compile(
    r"run_(\d+)_repetition_(\d+)$"
)


META_FIELDS = [
    "run_id",
    "run_done",
    "subject",
    "collector",
    "cadence_level",
    "dataset_size",
    "invocation_iterations",
    "cadence_n",
    "intended_gc_requests",
    "repetition",
]


REQUEST_FIELDS = (
    META_FIELDS
    + request_parser.FIELDNAMES
)


CONTEXT_FIELDS = (
    META_FIELDS
    + context_parser.FIELDNAMES
)


SUMMARY_FIELDS = [
    *META_FIELDS,
    "jfr_exists",
    "parser_status",

    "systemgc_requests",
    "realised_requests",
    "unrealised_requests",

    "explicit_gc_events",
    "unmatched_explicit_gc_events",

    "request_count_matches_intended",
    "all_requests_realised",
    "all_explicit_gc_events_matched",

    "min_gc_events_per_request",
    "max_gc_events_per_request",
    "mean_gc_events_per_request",

    "total_observed_net_heap_reduction_mb",
    "mean_observed_net_heap_reduction_mb",

    "error_message",
]


def repetition_from_run_id(run_id):
    match = RUN_ID_RE.fullmatch(run_id)

    if not match:
        return ""

    return int(match.group(2))


def optional_int(value):
    value = str(value).strip()

    if not value:
        return None

    return int(value)


def finite_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(result):
        return None

    return result


def metadata(row):
    return {
        "run_id": row["__run_id"].strip(),
        "run_done": row["__done"].strip(),
        "subject": row.get("subject", "").strip(),
        "collector": row.get("collector", "").strip(),
        "cadence_level":
            row.get("cadence_level", "").strip(),
        "dataset_size":
            row.get("dataset_size", "").strip(),
        "invocation_iterations":
            row.get(
                "invocation_iterations",
                "",
            ).strip(),
        "cadence_n":
            row.get("cadence_n", "").strip(),
        "intended_gc_requests":
            row.get(
                "intended_gc_requests",
                "",
            ).strip(),
        "repetition":
            repetition_from_run_id(
                row["__run_id"].strip()
            ),
    }


def request_statistics(rows):
    if not rows:
        return {
            "min_gc_events_per_request": 0,
            "max_gc_events_per_request": 0,
            "mean_gc_events_per_request": 0.0,
            "total_observed_net_heap_reduction_mb":
                0.0,
            "mean_observed_net_heap_reduction_mb":
                0.0,
        }

    counts = [
        int(row["gc_event_count"])
        for row in rows
    ]

    reductions = [
        finite_float(
            row[
                "observed_net_heap_reduction_mb"
            ]
        )
        for row in rows
    ]

    if all(v is not None for v in reductions):
        total_reduction = sum(reductions)
        mean_reduction = (
            total_reduction
            / len(reductions)
        )
    else:
        total_reduction = float("nan")
        mean_reduction = float("nan")

    return {
        "min_gc_events_per_request":
            min(counts),
        "max_gc_events_per_request":
            max(counts),
        "mean_gc_events_per_request":
            round(
                sum(counts) / len(counts),
                6,
            ),
        "total_observed_net_heap_reduction_mb":
            (
                round(total_reduction, 6)
                if math.isfinite(
                    total_reduction
                )
                else "nan"
            ),
        "mean_observed_net_heap_reduction_mb":
            (
                round(mean_reduction, 6)
                if math.isfinite(
                    mean_reduction
                )
                else "nan"
            ),
    }


def main():
    if len(sys.argv) != 4:
        print(
            "Usage: python3 "
            "parse_dacapo_jfr_batch.py "
            "<experiment_root> "
            "<recovered_run_table.csv> "
            "<output_dir>",
            file=sys.stderr,
        )
        return 1

    experiment_root = Path(
        sys.argv[1]
    ).resolve()

    recovered_table = Path(
        sys.argv[2]
    ).resolve()

    output_dir = Path(
        sys.argv[3]
    ).resolve()

    if not recovered_table.is_file():
        raise FileNotFoundError(
            recovered_table
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    request_output = (
        output_dir
        / "request_level.csv"
    )

    context_output = (
        output_dir
        / "request_context.csv"
    )

    summary_output = (
        output_dir
        / "run_jfr_summary.csv"
    )

    with recovered_table.open(
        newline="",
        errors="replace",
    ) as source:
        rows = list(
            csv.DictReader(source)
        )

    request_rows_all = []
    context_rows_all = []
    summary_rows = []

    total = len(rows)
    done_rows = sum(
        r["__done"].strip() == "DONE"
        for r in rows
    )

    print(
        f"Rows in recovered table: {total}",
        flush=True,
    )

    print(
        f"DONE rows to parse: {done_rows}",
        flush=True,
    )

    for number, row in enumerate(
        rows,
        start=1,
    ):
        meta = metadata(row)
        run_id = meta["run_id"]

        summary = {
            **meta,
            "jfr_exists": "",
            "parser_status": "",
            "systemgc_requests": "",
            "realised_requests": "",
            "unrealised_requests": "",
            "explicit_gc_events": "",
            "unmatched_explicit_gc_events": "",
            "request_count_matches_intended": "",
            "all_requests_realised": "",
            "all_explicit_gc_events_matched": "",
            "min_gc_events_per_request": "",
            "max_gc_events_per_request": "",
            "mean_gc_events_per_request": "",
            "total_observed_net_heap_reduction_mb":
                "",
            "mean_observed_net_heap_reduction_mb":
                "",
            "error_message": "",
        }

        if meta["run_done"] != "DONE":
            summary["parser_status"] = (
                "SKIPPED_NOT_DONE"
            )

            summary_rows.append(summary)

            print(
                f"[{number}/{total}] "
                f"{run_id}: "
                f"SKIPPED_NOT_DONE",
                flush=True,
            )

            continue

        jfr_file = (
            experiment_root
            / run_id
            / "recording.jfr"
        )

        summary["jfr_exists"] = int(
            jfr_file.is_file()
        )

        if not jfr_file.is_file():
            summary["parser_status"] = (
                "NO_JFR"
            )

            summary_rows.append(summary)

            print(
                f"[{number}/{total}] "
                f"{run_id}: NO_JFR",
                flush=True,
            )

            continue

        try:
            # One jfr print invocation only.
            text = request_parser.dump_jfr(
                jfr_file
            )

            heap, gc_events, requests = (
                request_parser.parse_events(
                    text
                )
            )

            request_rows, unmatched = (
                request_parser.build_rows(
                    heap,
                    gc_events,
                    requests,
                )
            )

            context_rows, context_unmatched = (
                context_parser.build_context_rows(
                    heap,
                    gc_events,
                    requests,
                )
            )

            if len(unmatched) != len(
                context_unmatched
            ):
                raise RuntimeError(
                    "Request/context unmatched "
                    "event counts disagree"
                )

            if len(request_rows) != len(
                context_rows
            ):
                raise RuntimeError(
                    "Request/context row counts "
                    "disagree"
                )

            explicit_events = [
                event
                for event in gc_events
                if event.get("cause")
                == "System.gc()"
            ]

            realised = sum(
                int(r["realised"])
                for r in request_rows
            )

            request_count = len(requests)
            unrealised = (
                request_count - realised
            )

            intended = optional_int(
                meta[
                    "intended_gc_requests"
                ]
            )

            if intended is None:
                request_match = ""
            else:
                request_match = int(
                    request_count
                    == intended
                )

            stats = request_statistics(
                request_rows
            )

            summary.update({
                "parser_status": "OK",
                "systemgc_requests":
                    request_count,
                "realised_requests":
                    realised,
                "unrealised_requests":
                    unrealised,
                "explicit_gc_events":
                    len(explicit_events),
                "unmatched_explicit_gc_events":
                    len(unmatched),
                "request_count_matches_intended":
                    request_match,
                "all_requests_realised":
                    int(
                        realised
                        == request_count
                    ),
                "all_explicit_gc_events_matched":
                    int(
                        len(unmatched) == 0
                    ),
                **stats,
            })

            for request_row in request_rows:
                combined = dict(meta)
                combined.update(request_row)

                request_rows_all.append(
                    combined
                )

            for context_row in context_rows:
                combined = dict(meta)
                combined.update(context_row)

                context_rows_all.append(
                    combined
                )

            print(
                f"[{number}/{total}] "
                f"{run_id}: "
                f"{meta['subject']}/"
                f"{meta['collector']}/"
                f"{meta['cadence_level']} "
                f"requests={request_count} "
                f"realised={realised} "
                f"explicit_events="
                f"{len(explicit_events)} "
                f"unmatched="
                f"{len(unmatched)}",
                flush=True,
            )

        except Exception as exc:
            summary["parser_status"] = (
                "ERROR"
            )

            summary["error_message"] = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            print(
                f"[{number}/{total}] "
                f"{run_id}: ERROR "
                f"{type(exc).__name__}: "
                f"{exc}",
                file=sys.stderr,
                flush=True,
            )

        summary_rows.append(summary)

    with request_output.open(
        "w",
        newline="",
    ) as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=REQUEST_FIELDS,
        )

        writer.writeheader()
        writer.writerows(
            request_rows_all
        )

    with context_output.open(
        "w",
        newline="",
    ) as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=CONTEXT_FIELDS,
        )

        writer.writeheader()
        writer.writerows(
            context_rows_all
        )

    with summary_output.open(
        "w",
        newline="",
    ) as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=SUMMARY_FIELDS,
        )

        writer.writeheader()
        writer.writerows(
            summary_rows
        )

    status_counts = {}

    for row in summary_rows:
        status = row["parser_status"]

        status_counts[status] = (
            status_counts.get(status, 0)
            + 1
        )

    print()
    print(
        "===== DACAPO JFR BATCH SUMMARY ====="
    )

    print(
        f"Rows in recovered table: "
        f"{total}"
    )

    for status in sorted(status_counts):
        print(
            f"{status}: "
            f"{status_counts[status]}"
        )

    print(
        f"Request-level rows: "
        f"{len(request_rows_all)}"
    )

    print(
        f"Request-context rows: "
        f"{len(context_rows_all)}"
    )

    print(
        f"Request output: "
        f"{request_output}"
    )

    print(
        f"Context output: "
        f"{context_output}"
    )

    print(
        f"Run summary: "
        f"{summary_output}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
