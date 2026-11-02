"""Batch request-level JFR analysis for an ExperimentRunner result directory.

Reads:
    <experiment_root>/run_table.csv
    <experiment_root>/<run_id>/recording.jfr

Writes:
    request_level.csv
        One row per jdk.SystemGC request.

    run_jfr_summary.csv
        One row per experimental run, including baseline runs with
        zero explicit requests and QC information.

The production experiment directory is read-only.

Usage:
    python3 parse_jfr_requests_batch.py \
        <experiment_root> \
        <output_dir>
"""

from pathlib import Path

import csv
import math
import re
import sys

import parse_jfr_requests as request_parser


RUN_ID_RE = re.compile(
    r"run_(\d+)_repetition_(\d+)$"
)


REQUEST_FIELDS = [
    "run_id",
    "run_done",
    "subject",
    "collector",
    "cadence_level",
    "repetition",
    "intended_gc_requests",
] + request_parser.FIELDNAMES


RUN_SUMMARY_FIELDS = [
    "run_id",
    "run_done",
    "subject",
    "collector",
    "cadence_level",
    "repetition",
    "intended_gc_requests",
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


def parse_optional_int(value):
    value = str(value).strip()

    if not value:
        return None

    return int(value)


def repetition_from_run_id(run_id):
    match = RUN_ID_RE.fullmatch(run_id)

    if not match:
        return ""

    return int(match.group(2))


def finite_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(result):
        return None

    return result


def summarise_request_rows(rows):
    if not rows:
        return {
            "min_gc_events_per_request": 0,
            "max_gc_events_per_request": 0,
            "mean_gc_events_per_request": 0.0,
            "total_observed_net_heap_reduction_mb": 0.0,
            "mean_observed_net_heap_reduction_mb": 0.0,
        }

    event_counts = [
        int(row["gc_event_count"])
        for row in rows
    ]

    reductions = []

    for row in rows:
        value = finite_float(
            row["observed_net_heap_reduction_mb"]
        )

        if value is not None:
            reductions.append(value)

    if len(reductions) == len(rows):
        total_reduction = round(sum(reductions), 6)
        mean_reduction = round(
            total_reduction / len(reductions),
            6,
        )
    else:
        total_reduction = "nan"
        mean_reduction = "nan"

    return {
        "min_gc_events_per_request": min(event_counts),
        "max_gc_events_per_request": max(event_counts),
        "mean_gc_events_per_request": round(
            sum(event_counts) / len(event_counts),
            6,
        ),
        "total_observed_net_heap_reduction_mb":
            total_reduction,
        "mean_observed_net_heap_reduction_mb":
            mean_reduction,
    }


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python3 parse_jfr_requests_batch.py "
            "<experiment_root> <output_dir>",
            file=sys.stderr,
        )
        return 1

    experiment_root = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()

    run_table = experiment_root / "run_table.csv"

    if not run_table.is_file():
        raise FileNotFoundError(run_table)

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    request_output = (
        output_dir / "request_level.csv"
    )

    run_output = (
        output_dir / "run_jfr_summary.csv"
    )

    combined_request_rows = []
    run_summary_rows = []

    with run_table.open(
        newline="",
        errors="replace",
    ) as source:

        reader = csv.DictReader(source)
        experiment_rows = list(reader)

    total_runs = len(experiment_rows)

    print(
        f"Experiment root: {experiment_root}",
        flush=True,
    )

    print(
        f"Runs in run_table.csv: {total_runs}",
        flush=True,
    )

    for run_number, run_row in enumerate(
        experiment_rows,
        start=1,
    ):
        run_id = run_row["__run_id"].strip()
        run_done = run_row["__done"].strip()

        subject = run_row.get(
            "subject",
            "",
        ).strip()

        collector = run_row.get(
            "collector",
            "",
        ).strip()

        cadence_level = run_row.get(
            "cadence_level",
            "",
        ).strip()

        intended_requests = parse_optional_int(
            run_row.get(
                "intended_gc_requests",
                "",
            )
        )

        repetition = repetition_from_run_id(
            run_id
        )

        jfr_file = (
            experiment_root
            / run_id
            / "recording.jfr"
        )

        summary = {
            "run_id": run_id,
            "run_done": run_done,
            "subject": subject,
            "collector": collector,
            "cadence_level": cadence_level,
            "repetition": repetition,
            "intended_gc_requests":
                intended_requests
                if intended_requests is not None
                else "",
            "jfr_exists": int(jfr_file.is_file()),
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
            "total_observed_net_heap_reduction_mb": "",
            "mean_observed_net_heap_reduction_mb": "",
            "error_message": "",
        }

        if not jfr_file.is_file():
            summary["parser_status"] = "NO_JFR"

            run_summary_rows.append(summary)

            print(
                f"[{run_number}/{total_runs}] "
                f"{run_id}: NO_JFR",
                flush=True,
            )

            continue

        try:
            heap, gc_events, requests = (
                request_parser.parse_events(
                    request_parser.dump_jfr(
                        jfr_file
                    )
                )
            )

            request_rows, unmatched_events = (
                request_parser.build_rows(
                    heap,
                    gc_events,
                    requests,
                )
            )

            explicit_gc_events = [
                event
                for event in gc_events
                if event.get("cause") == "System.gc()"
            ]

            realised_requests = sum(
                int(row["realised"])
                for row in request_rows
            )

            systemgc_requests = len(requests)

            unrealised_requests = (
                systemgc_requests
                - realised_requests
            )

            request_count_matches = (
                ""
                if intended_requests is None
                else int(
                    systemgc_requests
                    == intended_requests
                )
            )

            all_requests_realised = int(
                realised_requests
                == systemgc_requests
            )

            all_explicit_events_matched = int(
                len(unmatched_events) == 0
            )

            request_stats = (
                summarise_request_rows(
                    request_rows
                )
            )

            summary.update({
                "parser_status": "OK",
                "systemgc_requests":
                    systemgc_requests,
                "realised_requests":
                    realised_requests,
                "unrealised_requests":
                    unrealised_requests,
                "explicit_gc_events":
                    len(explicit_gc_events),
                "unmatched_explicit_gc_events":
                    len(unmatched_events),
                "request_count_matches_intended":
                    request_count_matches,
                "all_requests_realised":
                    all_requests_realised,
                "all_explicit_gc_events_matched":
                    all_explicit_events_matched,
                **request_stats,
            })

            for request_row in request_rows:
                combined = {
                    "run_id": run_id,
                    "run_done": run_done,
                    "subject": subject,
                    "collector": collector,
                    "cadence_level": cadence_level,
                    "repetition": repetition,
                    "intended_gc_requests":
                        intended_requests
                        if intended_requests is not None
                        else "",
                }

                combined.update(request_row)

                combined_request_rows.append(
                    combined
                )

            print(
                f"[{run_number}/{total_runs}] "
                f"{run_id}: "
                f"{collector}/{cadence_level} "
                f"requests={systemgc_requests} "
                f"realised={realised_requests} "
                f"explicit_events="
                f"{len(explicit_gc_events)} "
                f"unmatched="
                f"{len(unmatched_events)}",
                flush=True,
            )

        except Exception as exc:
            summary["parser_status"] = "ERROR"
            summary["error_message"] = (
                f"{type(exc).__name__}: {exc}"
            )

            print(
                f"[{run_number}/{total_runs}] "
                f"{run_id}: ERROR: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

        run_summary_rows.append(summary)

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
            combined_request_rows
        )

    with run_output.open(
        "w",
        newline="",
    ) as destination:

        writer = csv.DictWriter(
            destination,
            fieldnames=RUN_SUMMARY_FIELDS,
        )

        writer.writeheader()
        writer.writerows(
            run_summary_rows
        )

    ok_runs = sum(
        row["parser_status"] == "OK"
        for row in run_summary_rows
    )

    error_runs = sum(
        row["parser_status"] == "ERROR"
        for row in run_summary_rows
    )

    missing_jfr_runs = sum(
        row["parser_status"] == "NO_JFR"
        for row in run_summary_rows
    )

    print()
    print("===== BATCH SUMMARY =====")
    print(f"Runs processed: {total_runs}")
    print(f"Parser OK: {ok_runs}")
    print(f"Parser errors: {error_runs}")
    print(f"Missing JFR: {missing_jfr_runs}")
    print(
        "Request-level rows: "
        f"{len(combined_request_rows)}"
    )
    print(f"Request output: {request_output}")
    print(f"Run summary: {run_output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
