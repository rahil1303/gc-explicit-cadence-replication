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
import re
import shutil
import subprocess
import pandas as pd


class RunnerConfig:

    ROOT_DIR = Path(dirname(realpath(__file__)))

    PETCLINIC_CONFIG = {
        "work_units": 5000,
        "warmup_requests": 100,
        "cadence": {
            "baseline": 0,
            "low": 2500,
            "medium": 1000,
            "high": 500,
        },
    }

    name = "petclinic_gc_experiment_remote"

    results_output_path = (
        Path.home()
        / "gc-explicit-cadence-study"
        / "results"
        / "services"
        / "petclinic"
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
        self.profiler = None
        self.current_run_id = None

    def create_run_table_model(self) -> RunTableModel:

        subject = FactorModel("subject", ["petclinic"])

        collector = FactorModel(
            "collector",
            [
                "Serial",
                "Parallel",
                "G1",
                "Shenandoah",
                "ZGC",
            ],
        )

        cadence_level = FactorModel(
            "cadence_level",
            [
                "baseline",
                "low",
                "medium",
                "high",
            ],
        )

        self.run_table_model = RunTableModel(
            factors=[
                subject,
                collector,
                cadence_level,
            ],
            repetitions=10,
            data_columns=[
                "work_units",
                "cadence_n",
                "intended_gc_requests",

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

    @staticmethod
    def _safe_component(value: Any) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")

    def _make_run_id(
        self,
        context: RunnerContext,
        collector: str,
        cadence_level: str,
    ) -> str:

        local_token = self._safe_component(Path(context.run_dir).name)

        return "__".join([
            "petclinic",
            self._safe_component(collector),
            self._safe_component(cadence_level),
            local_token,
        ])

    def start_run(self, context: RunnerContext) -> None:
        # For the remote configuration the complete service lifecycle is
        # executed in one remote command from start_measurement().
        #
        # This keeps startup and warm-up outside the EnergiBridge measurement
        # while still keeping the service/JFR lifecycle on the measurement
        # server.
        pass

    def start_measurement(self, context: RunnerContext) -> None:

        collector = context.execute_run["collector"]
        cadence_level = context.execute_run["cadence_level"]

        cadence = self.PETCLINIC_CONFIG["cadence"][cadence_level]
        warmup_requests = self.PETCLINIC_CONFIG["warmup_requests"]

        study_root = Path.home() / "gc-explicit-cadence-study"

        remote_runner = (
            study_root
            / "scripts"
            / "orchestration"
            / "remote"
            / "run_remote_subject.sh"
        )

        if not remote_runner.exists():
            raise FileNotFoundError(
                f"Remote runner not found: {remote_runner}"
            )

        self.current_run_id = self._make_run_id(
            context,
            collector,
            cadence_level,
        )

        # The whole PetClinic lifecycle happens on the Linux server:
        #
        #   start service + JFR
        #   -> wait until ready
        #   -> 100 unmeasured warm-up requests
        #   -> EnergiBridge wraps only the fixed 5000-request workload
        #   -> stop service and wait for JFR to flush
        #
        # run_remote_subject.sh provides JFR_FILE and GC_REMOTE_RUN_DIR.
        remote_payload = (
            "set -euo pipefail; "
            "if [ -f scripts/orchestration/server_env.sh ]; then "
            "source scripts/orchestration/server_env.sh; "
            "fi; "
            'ENERGY_BIN="${ENERGIBRIDGE_BIN:-$(command -v energibridge || true)}"; '
            'test -n "$ENERGY_BIN"; '
            'SERVICE_LOG="$GC_REMOTE_RUN_DIR/petclinic.log"; '
            'scripts/services/petclinic/run_subject.sh '
            f'{collector} {cadence} '
            '> "$SERVICE_LOG" 2>&1 & '
            'SERVICE_PID=$!; '
            'cleanup() { '
            '  if kill -0 "$SERVICE_PID" 2>/dev/null; then '
            '    kill "$SERVICE_PID" 2>/dev/null || true; '
            '  fi; '
            '  wait "$SERVICE_PID" 2>/dev/null || true; '
            '}; '
            'trap cleanup EXIT; '
            'READY=0; '
            'for _ in $(seq 1 60); do '
            '  if curl -fsS -o /dev/null '
            '"http://localhost:9966/petclinic/api/owners"; then '
            '    READY=1; break; '
            '  fi; '
            '  if ! kill -0 "$SERVICE_PID" 2>/dev/null; then '
            '    echo "PetClinic exited before becoming ready" >&2; '
            '    exit 20; '
            '  fi; '
            '  sleep 0.5; '
            'done; '
            'if [ "$READY" -ne 1 ]; then '
            '  echo "PetClinic did not become ready in time" >&2; '
            '  exit 21; '
            'fi; '
            f'for _ in $(seq 1 {warmup_requests}); do '
            '  curl -fsS -o /dev/null '
            '"http://localhost:9966/petclinic/api/owners"; '
            'done; '
            '"$ENERGY_BIN" '
            '--output "$GC_REMOTE_RUN_DIR/energibridge.csv" '
            '--summary '
            'scripts/services/petclinic/run_workload.sh; '
            'cleanup; '
            'trap - EXIT'
        )

        cmd = [
            str(remote_runner),
            self.current_run_id,
            "/bin/bash",
            "-lc",
            remote_payload,
        ]

        self.profiler = subprocess.Popen(
            cmd,
            env=dict(os.environ),
        )

    def _fetch_artifacts(self, context: RunnerContext) -> None:

        if self.current_run_id is None:
            raise RuntimeError("No current remote run id is available.")

        study_root = Path.home() / "gc-explicit-cadence-study"

        fetcher = (
            study_root
            / "scripts"
            / "orchestration"
            / "remote"
            / "fetch_run_artifacts.sh"
        )

        if not fetcher.exists():
            raise FileNotFoundError(
                f"Remote artifact fetcher not found: {fetcher}"
            )

        fetch_dir = Path(context.run_dir) / "_remote_artifacts"

        if fetch_dir.exists():
            shutil.rmtree(fetch_dir)

        subprocess.run(
            [
                str(fetcher),
                self.current_run_id,
                str(fetch_dir),
            ],
            check=True,
            env=dict(os.environ),
        )

        for item in fetch_dir.iterdir():

            destination = Path(context.run_dir) / item.name

            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()

            shutil.move(str(item), str(destination))

        fetch_dir.rmdir()

    def interact(self, context: RunnerContext) -> None:

        if self.profiler is None:
            raise RuntimeError("Remote PetClinic run was not started.")

        return_code = self.profiler.wait()

        if return_code != 0:
            raise RuntimeError(
                f"Remote PetClinic run exited with code {return_code}"
            )

        self._fetch_artifacts(context)

    def stop_measurement(self, context: RunnerContext) -> None:
        pass

    def stop_run(self, context: RunnerContext) -> None:
        # The remote payload stops PetClinic itself so that the JFR file is
        # fully written before artefacts are fetched.
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

        recording = context.run_dir / "recording.jfr"

        result = subprocess.run(
            [
                "python3",
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

        energy_file = context.run_dir / "energibridge.csv"

        if not energy_file.exists():
            raise FileNotFoundError(
                f"Fetched EnergiBridge CSV not found: {energy_file}"
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
            .apply(pd.to_numeric, errors="coerce")
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
            .apply(pd.to_numeric, errors="coerce")
            .stack()
            .mean()
        )

        jfr = self._read_jfr_summary(context)

        request_log = context.run_dir / "petclinic.log"

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

        cadence_level = context.execute_run["cadence_level"]
        cadence_n = self.PETCLINIC_CONFIG["cadence"][cadence_level]
        work_units = self.PETCLINIC_CONFIG["work_units"]

        intended_gc_requests = (
            0
            if cadence_n == 0
            else work_units // cadence_n
        )

        return {
            "work_units":
                work_units,

            "cadence_n":
                cadence_n,

            "intended_gc_requests":
                intended_gc_requests,

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
