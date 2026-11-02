"""Batch request-context analysis for ExperimentRunner JFR results.

Reads all recording.jfr files referenced by run_table.csv and writes one
combined row per jdk.SystemGC request.

Usage:
    python3 parse_jfr_request_context_batch.py \
        <experiment_root> \
        <output_csv>
"""

from pathlib import Path
import csv
import re
import sys

import parse_jfr_requests as base
import parse_jfr_request_context as context


RUN_ID_RE = re.compile(
    r"run_(\d+)_repetition_(\d+)$"
)


META_FIELDS = [
    "run_id",
    "run_done",
    "subject",
    "collector",
    "cadence_level",
    "repetition",
    "intended_gc_requests",
]


FIELDNAMES = META_FIELDS + context.FIELDNAMES


def repetition_from_run_id(run_id):
    match = RUN_ID_RE.fullmatch(run_id)

    if not match:
        return ""

    return int(match.group(2))


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python3 "
            "parse_jfr_request_context_batch.py "
            "<experiment_root> <output.csv>",
            file=sys.stderr,
        )
        return 1

    root = Path(sys.argv[1]).resolve()
    output_file = Path(sys.argv[2]).resolve()

    run_table = root / "run_table.csv"

    if not run_table.is_file():
        raise FileNotFoundError(run_table)

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with run_table.open(
        newline="",
        errors="replace",
    ) as f:
        runs = list(csv.DictReader(f))

    combined_rows = []

    errors = 0
    unmatched_total = 0

    for number, run in enumerate(
        runs,
        start=1,
    ):
        run_id = run["__run_id"].strip()

        jfr_file = (
            root
            / run_id
            / "recording.jfr"
        )

        if not jfr_file.is_file():
            print(
                f"[{number}/{len(runs)}] "
                f"{run_id}: NO_JFR",
                flush=True,
            )
            errors += 1
            continue

        try:
            heap, gc_events, requests = (
                base.parse_events(
                    base.dump_jfr(jfr_file)
                )
            )

            rows, unmatched = (
                context.build_context_rows(
                    heap,
                    gc_events,
                    requests,
                )
            )

            unmatched_total += len(unmatched)

            metadata = {
                "run_id": run_id,
                "run_done":
                    run.get("__done", ""),
                "subject":
                    run.get("subject", ""),
                "collector":
                    run.get("collector", ""),
                "cadence_level":
                    run.get("cadence_level", ""),
                "repetition":
                    repetition_from_run_id(
                        run_id
                    ),
                "intended_gc_requests":
                    run.get(
                        "intended_gc_requests",
                        "",
                    ),
            }

            for row in rows:
                combined = dict(metadata)
                combined.update(row)
                combined_rows.append(combined)

            print(
                f"[{number}/{len(runs)}] "
                f"{run_id}: "
                f"{run.get('collector', '')}/"
                f"{run.get('cadence_level', '')} "
                f"requests={len(requests)} "
                f"context_rows={len(rows)} "
                f"unmatched={len(unmatched)}",
                flush=True,
            )

        except Exception as exc:
            errors += 1

            print(
                f"[{number}/{len(runs)}] "
                f"{run_id}: ERROR "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    with output_file.open(
        "w",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=FIELDNAMES,
        )

        writer.writeheader()
        writer.writerows(combined_rows)

    print()
    print("===== CONTEXT BATCH SUMMARY =====")
    print(f"Runs examined: {len(runs)}")
    print(f"Errors/missing JFR: {errors}")
    print(
        f"Request-context rows: "
        f"{len(combined_rows)}"
    )
    print(
        f"Unmatched explicit GC events: "
        f"{unmatched_total}"
    )
    print(f"Output: {output_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
