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
import time
import urllib.request
import pandas as pd


class RunnerConfig:

    ROOT_DIR = Path(dirname(realpath(__file__)))

    name = "train_ticket_gc_smoke_test"

    results_output_path = (
        Path.home()
        / "gc-explicit-cadence-study"
        / "results"
        / "services"
        / "train-ticket"
        / "raw"
    )

    operation_type = OperationType.AUTO
    time_between_runs_in_ms = 1000

    def __init__(self):

        EventSubscriptionController.subscribe_to_multiple_events([
            (RunnerEvents.START_RUN, self.start_run),
            (RunnerEvents.START_MEASUREMENT, self.start_measurement),
            (RunnerEvents.INTERACT, self.interact),
            (RunnerEvents.STOP_MEASUREMENT, self.stop_measurement),
            (RunnerEvents.STOP_RUN, self.stop_run),
            (RunnerEvents.POPULATE_RUN_DATA, self.populate_run_data),
        ])

        self.run_table_model = None
        self.service = None
        self.profiler = None
        self.service_log = None
        self.energy_log = None
        self.request_log_offset = 0

    def create_run_table_model(self) -> RunTableModel:

        subject = FactorModel("subject", ["train-ticket"])
        collector = FactorModel("collector", ["G1"])
        cadence = FactorModel("cadence", [10000])

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

    def _wait_for_service(self, timeout_s=30):

        url = (
            "http://localhost:12342"
            "/api/v1/userservice/users/fdse_microservice"
        )

        deadline = time.time() + timeout_s

        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass

            time.sleep(0.5)

        raise RuntimeError(
            "Train Ticket user-service did not become ready in time"
        )

    def _request(self, url, method="GET"):

        request = urllib.request.Request(
            url,
            method=method,
        )

        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"HTTP {response.status}: {url}"
                )

            return response.read()

    def start_run(self, context: RunnerContext) -> None:

        collector = context.execute_run["collector"]
        cadence = context.execute_run["cadence"]

        study_root = (
            Path.home()
            / "gc-explicit-cadence-study"
        )

        runner = (
            study_root
            / "scripts"
            / "services"
            / "train-ticket"
            / "run_subject.sh"
        )

        self.service_log = open(
            context.run_dir / "train-ticket.log",
            "w",
        )

        env = dict(os.environ)
        env["JFR_FILE"] = str(
            context.run_dir / "recording.jfr"
        )

        self.service = subprocess.Popen(
            [
                str(runner),
                collector,
                str(cadence),
            ],
            stdout=self.service_log,
            stderr=subprocess.STDOUT,
            env=env,
        )

        self._wait_for_service()

        lookup_url = (
            "http://localhost:12342"
            "/api/v1/userservice/users/fdse_microservice"
        )

        # Warm-up: 1000 successful DB-backed lookups.
        for _ in range(1000):
            self._request(lookup_url)

        # Exclude warm-up operations from cadence bookkeeping.
        reset_url = (
            "http://localhost:12342"
            "/api/v1/userservice/users/gc-counter/reset"
        )

        self._request(
            reset_url,
            method="POST",
        )
        # Everything before this point belongs to startup/warm-up.
        # Only count explicit-GC request markers written after this offset.
        if self.service_log is not None:
            self.service_log.flush()

        request_log = context.run_dir / "train-ticket.log"

        if request_log.exists():
            self.request_log_offset = request_log.stat().st_size
        else:
            self.request_log_offset = 0

    def start_measurement(self, context: RunnerContext) -> None:

        recording = context.run_dir / "recording.jfr"

        subprocess.run(
            [
                "jcmd",
                str(self.service.pid),
                "JFR.start",
                "name=gcstudy",
                "settings=profile",
            ],
            check=True,
        )

        energy_csv = (
            context.run_dir
            / "energibridge.csv"
        )

        self.energy_log = open(
            context.run_dir / "energibridge.log",
            "w",
        )

        workload = (
            Path.home()
            / "gc-explicit-cadence-study"
            / "scripts"
            / "services"
            / "train-ticket"
            / "run_workload.sh"
        )

        env = dict(os.environ)
        env["WORKLOAD_DONE_FILE"] = str(
            context.run_dir / "workload.done"
        )

        self.profiler = subprocess.Popen(
            [
                "/usr/local/bin/energibridge",
                "--output",
                str(energy_csv),
                "--summary",
                str(workload),
            ],
            stdout=self.energy_log,
            stderr=subprocess.STDOUT,
            env=env,
        )
    
    def interact(self, context: RunnerContext) -> None:

        return_code = self.profiler.wait()

        if self.energy_log is not None:
            self.energy_log.flush()
            self.energy_log.close()
            self.energy_log = None

        energy_csv = context.run_dir / "energibridge.csv"
        workload_done = context.run_dir / "workload.done"

        if not workload_done.exists():
            raise RuntimeError(
                "Measured Train Ticket workload did not complete"
            )

        if return_code != 0 and not energy_csv.exists():
            raise RuntimeError(
                f"EnergiBridge exited with code {return_code}"
            )

    def stop_measurement(self, context: RunnerContext) -> None:

        recording = context.run_dir / "recording.jfr"

        subprocess.run(
            [
                "jcmd",
                str(self.service.pid),
                "JFR.stop",
                "name=gcstudy",
                f"filename={recording}",
            ],
            check=True,
        )
    
    def stop_run(self, context: RunnerContext) -> None:

        if self.service is not None:
            self.service.terminate()

            try:
                self.service.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.service.kill()
                self.service.wait()

        if self.service_log is not None:
            self.service_log.flush()
            self.service_log.close()
            self.service_log = None

    def _read_jfr_summary(
        self,
        context: RunnerContext,
    ) -> Dict[str, Any]:

        parser = (
            Path.home()
            / "gc-explicit-cadence-study"
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

        df = pd.read_csv(
            context.run_dir / "energibridge.csv"
        )

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
            / "train-ticket.log"
        )

        explicit_gc_requests = 0

        if request_log.exists():
            with open(
                request_log,
                "r",
                errors="ignore",
            ) as f:

                f.seek(self.request_log_offset)

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