from EventManager.Models.RunnerEvents import RunnerEvents
from EventManager.EventSubscriptionController import EventSubscriptionController
from ConfigValidator.Config.Models.RunTableModel import RunTableModel
from ConfigValidator.Config.Models.FactorModel import FactorModel
from ConfigValidator.Config.Models.RunnerContext import RunnerContext
from ConfigValidator.Config.Models.OperationType import OperationType

from typing import Dict, Any, Optional
from pathlib import Path
from os.path import dirname, realpath

import os
import subprocess
import shlex
import pandas as pd


class RunnerConfig:

    ROOT_DIR = Path(dirname(realpath(__file__)))

    name = "sablecc_gc_smoke_test"

    results_output_path = (
        Path.home()
        / "gc-explicit-cadence-study"
        / "results"
        / "services"
        / "sablecc"
        / "raw"
    )

    operation_type = OperationType.AUTO

    time_between_runs_in_ms = 1000

    def __init__(self):

        EventSubscriptionController.subscribe_to_multiple_events([
            (RunnerEvents.START_MEASUREMENT, self.start_measurement),
            (RunnerEvents.INTERACT, self.interact),
            (RunnerEvents.STOP_MEASUREMENT, self.stop_measurement),
            (RunnerEvents.POPULATE_RUN_DATA, self.populate_run_data),
        ])

        self.run_table_model = None
        self.profiler = None
        self.log_handle = None

    def create_run_table_model(self) -> RunTableModel:

        subject = FactorModel("subject", ["sablecc"])
        collector = FactorModel("collector", ["G1"])
        cadence = FactorModel("cadence", [50])

        self.run_table_model = RunTableModel(
            factors=[
                subject,
                collector,
                cadence,
            ],
            repetitions=1,

            data_columns=[
                "runtime_s",
                "system_energy_j",
                "avg_power_w",
                "avg_cpu_usage_pct",
                "avg_cpu_frequency_mhz",
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
            ],
        )

        return self.run_table_model

    def start_measurement(self, context: RunnerContext) -> None:

        collector = context.execute_run["collector"]
        cadence = context.execute_run["cadence"]

        study_root = Path.home() / "gc-explicit-cadence-study"

        runner = (
            study_root
            / "scripts"
            / "services"
            / "sablecc"
            / "run_subject.sh"
        )

        energy_csv = context.run_dir / "energibridge.csv"
        energy_log = context.run_dir / "energibridge.log"

        cmd = (
            f"/usr/local/bin/energibridge "
            f"--output {energy_csv} "
            f"--summary "
            f"{runner} {collector} {cadence}"
        )

        self.log_handle = open(energy_log, "w")

        env = dict(os.environ)

        env["JFR_FILE"] = str(
            context.run_dir / "recording.jfr"
        )

        self.profiler = subprocess.Popen(
            shlex.split(cmd),
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )

    def interact(self, context: RunnerContext) -> None:

        return_code = self.profiler.wait()

        if self.log_handle is not None:
            self.log_handle.flush()
            self.log_handle.close()
            self.log_handle = None

        if return_code != 0:
            raise RuntimeError(
                f"Measured process exited with code {return_code}"
            )

    def stop_measurement(self, context: RunnerContext) -> None:

        pass

    def _read_jfr_summary(
        self,
        context: RunnerContext,
    ) -> Dict[str, Any]:

        study_root = (
            Path.home()
            / "gc-explicit-cadence-study"
        )

        parser = (
            study_root
            / "parsers"
            / "parse_jfr_gc.py"
        )

        recording = (
            context.run_dir
            / "recording.jfr"
        )

        result = subprocess.run(
            [
                "python",
                str(parser),
                str(recording),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        metrics = {}

        for line in result.stdout.splitlines():

            if ":" not in line:
                continue

            key, value = line.split(":", 1)

            key = key.strip()
            value = value.strip()

            try:
                if "." in value:
                    metrics[key] = float(value)
                else:
                    metrics[key] = int(value)

            except ValueError:
                metrics[key] = value

        return metrics


    def populate_run_data(
        self,
        context: RunnerContext,
    ) -> Optional[Dict[str, Any]]:

        energy_file = (
            context.run_dir
            / "energibridge.csv"
        )

        df = pd.read_csv(energy_file)

        runtime_s = (
            df["Time"].iloc[-1]
            - df["Time"].iloc[0]
        ) / 1000.0

        power = pd.to_numeric(
            df["SYSTEM_POWER (Watts)"],
            errors="coerce",
        )

        delta_t_s = (
            pd.to_numeric(
                df["Time"],
                errors="coerce",
            )
            .diff()
            / 1000.0
        )

        interval_energy = (
            (power.shift(1) + power)
            / 2.0
            * delta_t_s
        )

        interval_energy = interval_energy.where(
            delta_t_s > 0
        )

        system_energy_j = interval_energy.sum()

        avg_power_w = (
            system_energy_j / runtime_s
            if runtime_s > 0
            else float("nan")
        )

        cpu_columns = [
            column
            for column in df.columns
            if column.startswith("CPU_USAGE_")
        ]

        avg_cpu_usage_pct = (
            df[cpu_columns]
            .apply(
                pd.to_numeric,
                errors="coerce",
            )
            .stack()
            .mean()
        )

        frequency_columns = [
            column
            for column in df.columns
            if column.startswith("CPU_FREQUENCY_")
        ]

        avg_cpu_frequency_mhz = (
            df[frequency_columns]
            .apply(
                pd.to_numeric,
                errors="coerce",
            )
            .stack()
            .mean()
        )

        jfr = self._read_jfr_summary(context)

        request_log = (
            context.run_dir
            / "energibridge.log"
        )

        explicit_gc_requests = 0

        if request_log.exists():
            with open(
                request_log,
                "r",
                errors="ignore",
            ) as f:
                explicit_gc_requests = sum(
                    1
                    for line in f
                    if "[EXPLICIT_GC_REQUEST]" in line
                )

        return {
            "runtime_s": round(runtime_s, 3),

            "system_energy_j": round(
                system_energy_j,
                3,
            ),

            "avg_power_w": round(
                avg_power_w,
                3,
            ),

            "avg_cpu_usage_pct": round(
                avg_cpu_usage_pct,
                3,
            ),

            "avg_cpu_frequency_mhz": round(
                avg_cpu_frequency_mhz,
                3,
            ),

            "explicit_gc_requests":
                explicit_gc_requests,

            "total_gc_events":
                jfr["total_gc_events"],

            "explicit_gc_events":
                jfr["explicit_gc_events"],

            "natural_gc_events":
                jfr["natural_gc_events"],

            "total_gc_pause_ms":
                jfr["total_gc_pause_ms"],

            "explicit_gc_pause_ms":
                jfr["explicit_gc_pause_ms"],

            "natural_gc_pause_ms":
                jfr["natural_gc_pause_ms"],

            "total_reclaimed_mb":
                jfr["total_reclaimed_mb"],

            "explicit_reclaimed_mb":
                jfr["explicit_reclaimed_mb"],

            "natural_reclaimed_mb":
                jfr["natural_reclaimed_mb"],
        }

    experiment_path: Path = None