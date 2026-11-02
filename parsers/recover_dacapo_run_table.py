#!/usr/bin/env python3

"""Recover DaCapo run-table rows lost to RAPL counter wraparound.

The original run_table.csv is NEVER modified.

Recoverable TODO rows:
    - workload itself completed
    - recording.jfr exists
    - energibridge.csv exists
    - TODO arose from PACKAGE/DRAM cumulative energy rollover

Genuine workload failures remain TODO:
    - Jython ClassCastException
    - DaCapo validation failure

For recovered rows:
    - fixed experiment metadata is copied from a successful sibling
      with the same logical run number
    - energy metrics are recomputed with wrap-aware RAPL handling
    - GC metrics are recomputed from JFR
    - jdk.SystemGC events are counted directly
    - runtime_s is approximated by the EnergiBridge measurement span
      and explicitly recorded as such in recovery_report.csv

Usage:
    python3 recover_dacapo_run_table.py \
        <experiment_root> \
        <output_dir>
"""

from pathlib import Path
import csv
import math
import re
import sys

import parse_energibridge_gl1_wrapaware as eb
import parse_jfr_gc as gc_summary
import parse_jfr_requests as jfr_requests


RUN_RE = re.compile(
    r"run_(\d+)_repetition_(\d+)$"
)

FIXED_METADATA_FIELDS = [
    "dataset_size",
    "invocation_iterations",
    "cadence_n",
    "intended_gc_requests",
]

ENERGY_FIELDS = [
    "runtime_s",
    "energy_sampling_duration_s",
    "package_energy_j",
    "dram_energy_j",
    "avg_package_power_w",
    "avg_dram_power_w",
    "avg_cpu_usage_pct",
    "avg_cpu_frequency_mhz",
]

GC_FIELDS = [
    "explicit_gc_requests",
    "total_gc_events",
    "explicit_gc_events",
    "natural_gc_events",
    "total_gc_pause_ms",
    "explicit_gc_pause_ms",
    "natural_gc_pause_ms",
    "total_reclaimed_mb",
    "explicit_reclaimed_mb",
    "natural_reclaimed_mb",
]

REPORT_FIELDS = [
    "run_id",
    "subject",
    "collector",
    "cadence_level",
    "original_done",
    "new_done",
    "recovery_status",
    "sibling_source",
    "runtime_source",
    "package_wrapped",
    "dram_wrapped",
    "systemgc_requests",
    "intended_gc_requests",
    "request_count_matches_intended",
    "gc_events_missing_heap_pair",
    "gc_events_missing_pause",
    "gc_events_missing_cause",
    "error_message",
]


def to_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(result):
        return None

    return result


def logical_run_number(run_id):
    match = RUN_RE.fullmatch(run_id)

    if not match:
        raise ValueError(
            f"Unexpected run id: {run_id}"
        )

    return int(match.group(1))


def classify_workload_failure(run_dir):
    stderr_file = run_dir / "stderr.log"

    if not stderr_file.is_file():
        return None

    text = stderr_file.read_text(
        errors="replace"
    )

    if "ClassCastException" in text:
        return "WORKLOAD_JYTHON_CLASSCAST"

    if "Validation FAILED" in text:
        return "WORKLOAD_VALIDATION_FAILED"

    return None


def find_successful_sibling(rows, target):
    logical = logical_run_number(
        target["__run_id"]
    )

    candidates = []

    for row in rows:
        if row["__done"].strip() != "DONE":
            continue

        try:
            same_logical = (
                logical_run_number(
                    row["__run_id"]
                )
                == logical
            )
        except ValueError:
            continue

        if not same_logical:
            continue

        if (
            row["subject"] != target["subject"]
            or row["collector"] != target["collector"]
            or row["cadence_level"]
            != target["cadence_level"]
        ):
            continue

        candidates.append(row)

    if not candidates:
        raise RuntimeError(
            "No successful sibling row found"
        )

    return candidates[0]


def parse_energy(csv_file):
    rows = eb.load_rows(csv_file)

    required = {
        "Time",
        "PACKAGE_ENERGY (J)",
        "DRAM_ENERGY (J)",
    }

    missing = required - set(
        rows[0].keys()
    )

    if missing:
        raise RuntimeError(
            "Missing EnergiBridge columns: "
            + ", ".join(sorted(missing))
        )

    start_index = eb.find_measurement_start(
        rows
    )

    measurement_rows = rows[start_index:]

    first = measurement_rows[0]
    last = measurement_rows[-1]

    start_time = to_float(
        first["Time"]
    )
    end_time = to_float(
        last["Time"]
    )

    package_start = to_float(
        first["PACKAGE_ENERGY (J)"]
    )
    package_end = to_float(
        last["PACKAGE_ENERGY (J)"]
    )

    dram_start = to_float(
        first["DRAM_ENERGY (J)"]
    )
    dram_end = to_float(
        last["DRAM_ENERGY (J)"]
    )

    if None in (
        start_time,
        end_time,
        package_start,
        package_end,
        dram_start,
        dram_end,
    ):
        raise RuntimeError(
            "Could not parse required "
            "EnergiBridge values"
        )

    duration_s = (
        end_time - start_time
    ) / 1000.0

    if duration_s <= 0:
        raise RuntimeError(
            "Non-positive EnergiBridge duration"
        )

    package_energy_j, package_wrapped = (
        eb.wrapped_delta(
            package_start,
            package_end,
            eb.RAPL_MODULUS_J,
        )
    )

    dram_energy_j, dram_wrapped = (
        eb.wrapped_delta(
            dram_start,
            dram_end,
            eb.RAPL_MODULUS_J,
        )
    )

    # Recovery is intended only for actual wrap cases.
    if not package_wrapped and not dram_wrapped:
        raise RuntimeError(
            "TODO row does not contain a "
            "PACKAGE or DRAM wrap"
        )

    avg_package_power_w = (
        package_energy_j / duration_s
    )

    avg_dram_power_w = (
        dram_energy_j / duration_s
    )

    avg_cpu_usage_pct = eb.average_columns(
        measurement_rows,
        "CPU_USAGE_",
    )

    avg_cpu_frequency_mhz = (
        eb.average_columns(
            measurement_rows,
            "CPU_FREQUENCY_",
        )
    )

    return {
        # Explicit methodological decision:
        # recovered runtime uses EnergiBridge span.
        "runtime_s": duration_s,
        "energy_sampling_duration_s":
            duration_s,
        "package_energy_j":
            package_energy_j,
        "dram_energy_j":
            dram_energy_j,
        "avg_package_power_w":
            avg_package_power_w,
        "avg_dram_power_w":
            avg_dram_power_w,
        "avg_cpu_usage_pct":
            avg_cpu_usage_pct,
        "avg_cpu_frequency_mhz":
            avg_cpu_frequency_mhz,
        "_package_wrapped":
            package_wrapped,
        "_dram_wrapped":
            dram_wrapped,
    }


def parse_jfr(jfr_file):
    text = jfr_requests.dump_jfr(
        jfr_file
    )

    heap, gc_events, requests = (
        jfr_requests.parse_events(text)
    )

    summary = gc_summary.summarise(
        heap,
        gc_events,
    )

    values = {
        "explicit_gc_requests":
            len(requests),

        "total_gc_events":
            summary["total_gc_events"],

        "explicit_gc_events":
            summary["explicit_gc_events"],

        "natural_gc_events":
            summary["natural_gc_events"],

        "total_gc_pause_ms":
            summary["total_gc_pause_ms"],

        "explicit_gc_pause_ms":
            summary["explicit_gc_pause_ms"],

        "natural_gc_pause_ms":
            summary["natural_gc_pause_ms"],

        "total_reclaimed_mb":
            summary["total_reclaimed_mb"],

        "explicit_reclaimed_mb":
            summary["explicit_reclaimed_mb"],

        "natural_reclaimed_mb":
            summary["natural_reclaimed_mb"],

        "_gc_events_missing_heap_pair":
            summary[
                "gc_events_missing_heap_pair"
            ],

        "_gc_events_missing_pause":
            summary[
                "gc_events_missing_pause"
            ],

        "_gc_events_missing_cause":
            summary[
                "gc_events_missing_cause"
            ],
    }

    return values


def format_value(value):
    if value is None:
        return ""

    if isinstance(value, bool):
        return str(int(value))

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if math.isnan(value):
            return "nan"

        return f"{value:.6f}".rstrip(
            "0"
        ).rstrip(".")

    return str(value)


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python3 "
            "recover_dacapo_run_table.py "
            "<experiment_root> "
            "<output_dir>",
            file=sys.stderr,
        )
        return 1

    root = Path(sys.argv[1]).resolve()
    output_dir = Path(
        sys.argv[2]
    ).resolve()

    original_table = (
        root / "run_table.csv"
    )

    if not original_table.is_file():
        raise FileNotFoundError(
            original_table
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    recovered_table = (
        output_dir
        / "run_table_recovered.csv"
    )

    report_file = (
        output_dir
        / "recovery_report.csv"
    )

    with original_table.open(
        newline="",
        errors="replace",
    ) as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if not fieldnames:
        raise RuntimeError(
            "run_table.csv has no header"
        )

    recovered_count = 0
    genuine_failures = 0
    recovery_errors = 0

    report_rows = []

    for row in rows:
        if row["__done"].strip() == "DONE":
            continue

        run_id = row["__run_id"]
        run_dir = root / run_id

        report = {
            "run_id": run_id,
            "subject": row["subject"],
            "collector": row["collector"],
            "cadence_level":
                row["cadence_level"],
            "original_done":
                row["__done"],
            "new_done": row["__done"],
            "recovery_status": "",
            "sibling_source": "",
            "runtime_source": "",
            "package_wrapped": "",
            "dram_wrapped": "",
            "systemgc_requests": "",
            "intended_gc_requests": "",
            "request_count_matches_intended":
                "",
            "gc_events_missing_heap_pair":
                "",
            "gc_events_missing_pause": "",
            "gc_events_missing_cause": "",
            "error_message": "",
        }

        failure = (
            classify_workload_failure(
                run_dir
            )
        )

        if failure is not None:
            report["recovery_status"] = (
                failure
            )

            genuine_failures += 1
            report_rows.append(report)
            continue

        try:
            sibling = find_successful_sibling(
                rows,
                row,
            )

            report["sibling_source"] = (
                sibling["__run_id"]
            )

            for field in (
                FIXED_METADATA_FIELDS
            ):
                row[field] = sibling[field]

            energy = parse_energy(
                run_dir / "energibridge.csv"
            )

            jfr = parse_jfr(
                run_dir / "recording.jfr"
            )

            for field in ENERGY_FIELDS:
                row[field] = format_value(
                    energy[field]
                )

            for field in GC_FIELDS:
                row[field] = format_value(
                    jfr[field]
                )

            intended = int(
                row[
                    "intended_gc_requests"
                ]
            )

            observed = int(
                jfr[
                    "explicit_gc_requests"
                ]
            )

            if observed != intended:
                raise RuntimeError(
                    "SystemGC request count "
                    f"{observed} does not match "
                    f"intended count {intended}"
                )

            row["__done"] = "DONE"

            report.update({
                "new_done": "DONE",
                "recovery_status":
                    "RECOVERED_RAPL_WRAP",
                "runtime_source":
                    "energibridge_measurement_span",
                "package_wrapped":
                    int(
                        energy[
                            "_package_wrapped"
                        ]
                    ),
                "dram_wrapped":
                    int(
                        energy[
                            "_dram_wrapped"
                        ]
                    ),
                "systemgc_requests":
                    observed,
                "intended_gc_requests":
                    intended,
                "request_count_matches_intended":
                    1,
                "gc_events_missing_heap_pair":
                    jfr[
                        "_gc_events_missing_heap_pair"
                    ],
                "gc_events_missing_pause":
                    jfr[
                        "_gc_events_missing_pause"
                    ],
                "gc_events_missing_cause":
                    jfr[
                        "_gc_events_missing_cause"
                    ],
            })

            recovered_count += 1

        except Exception as exc:
            recovery_errors += 1

            report[
                "recovery_status"
            ] = "RECOVERY_ERROR"

            report[
                "error_message"
            ] = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        report_rows.append(report)

    with recovered_table.open(
        "w",
        newline="",
    ) as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    with report_file.open(
        "w",
        newline="",
    ) as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=REPORT_FIELDS,
        )

        writer.writeheader()
        writer.writerows(
            report_rows
        )

    done_after = sum(
        row["__done"].strip()
        == "DONE"
        for row in rows
    )

    todo_after = (
        len(rows) - done_after
    )

    print()
    print(
        "===== DACAPO RECOVERY SUMMARY ====="
    )
    print(
        f"Original rows: {len(rows)}"
    )
    print(
        f"Recovered wrap rows: "
        f"{recovered_count}"
    )
    print(
        f"Genuine workload failures "
        f"left TODO: {genuine_failures}"
    )
    print(
        f"Recovery errors: "
        f"{recovery_errors}"
    )
    print(
        f"DONE after recovery: "
        f"{done_after}"
    )
    print(
        f"TODO after recovery: "
        f"{todo_after}"
    )
    print(
        f"Recovered table: "
        f"{recovered_table}"
    )
    print(
        f"Recovery report: "
        f"{report_file}"
    )

    return (
        0
        if recovery_errors == 0
        else 2
    )


if __name__ == "__main__":
    sys.exit(main())
