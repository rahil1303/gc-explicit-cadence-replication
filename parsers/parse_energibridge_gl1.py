#!/usr/bin/env python3

import csv
import sys
from pathlib import Path
from statistics import mean


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_rows(csv_file: Path):
    with csv_file.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        raise RuntimeError("EnergiBridge CSV contains no data rows")

    return rows


def find_measurement_start(rows):
    """
    EnergiBridge on gl1 emits initial setup samples where the timestamp
    may be duplicated.

    Use the first row whose Time value has actually advanced relative
    to the preceding row as the beginning of the stable measurement
    interval.
    """

    previous_time = to_float(rows[0].get("Time"))

    for i in range(1, len(rows)):
        current_time = to_float(rows[i].get("Time"))

        if (
            current_time is not None
            and previous_time is not None
            and current_time > previous_time
        ):
            return i

        if current_time is not None:
            previous_time = current_time

    raise RuntimeError(
        "Could not determine EnergiBridge measurement start"
    )


def average_columns(rows, prefix):
    values = []

    for row in rows:
        for key, value in row.items():
            if key.startswith(prefix):
                number = to_float(value)

                if number is not None:
                    values.append(number)

    if not values:
        return None

    return mean(values)


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python3 parse_energibridge_gl1.py "
            "<energibridge.csv>"
        )
        sys.exit(1)

    csv_file = Path(sys.argv[1])

    if not csv_file.exists():
        raise FileNotFoundError(csv_file)

    rows = load_rows(csv_file)

    required = {
        "Time",
        "PACKAGE_ENERGY (J)",
        "DRAM_ENERGY (J)",
    }

    missing = required - set(rows[0].keys())

    if missing:
        raise RuntimeError(
            "Missing expected Green Lab EnergiBridge columns: "
            + ", ".join(sorted(missing))
        )

    start_index = find_measurement_start(rows)

    measurement_rows = rows[start_index:]

    first = measurement_rows[0]
    last = measurement_rows[-1]

    start_time = to_float(first["Time"])
    end_time = to_float(last["Time"])

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
            "Could not parse required EnergiBridge values"
        )

    runtime_s = (end_time - start_time) / 1000.0

    package_energy_j = package_end - package_start
    dram_energy_j = dram_end - dram_start

    if package_energy_j < 0:
        raise RuntimeError(
            "PACKAGE_ENERGY counter decreased during measurement"
        )

    if dram_energy_j < 0:
        raise RuntimeError(
            "DRAM_ENERGY counter decreased during measurement"
        )

    avg_package_power_w = (
        package_energy_j / runtime_s
        if runtime_s > 0
        else float("nan")
    )

    avg_dram_power_w = (
        dram_energy_j / runtime_s
        if runtime_s > 0
        else float("nan")
    )

    avg_cpu_usage_pct = average_columns(
        measurement_rows,
        "CPU_USAGE_",
    )

    avg_cpu_frequency_mhz = average_columns(
        measurement_rows,
        "CPU_FREQUENCY_",
    )

    print(f"runtime_s: {runtime_s:.6f}")
    print(f"package_energy_j: {package_energy_j:.6f}")
    print(f"dram_energy_j: {dram_energy_j:.6f}")
    print(
        f"avg_package_power_w: "
        f"{avg_package_power_w:.6f}"
    )
    print(
        f"avg_dram_power_w: "
        f"{avg_dram_power_w:.6f}"
    )

    if avg_cpu_usage_pct is not None:
        print(
            f"avg_cpu_usage_pct: "
            f"{avg_cpu_usage_pct:.6f}"
        )

    if avg_cpu_frequency_mhz is not None:
        print(
            f"avg_cpu_frequency_mhz: "
            f"{avg_cpu_frequency_mhz:.6f}"
        )


if __name__ == "__main__":
    main()
