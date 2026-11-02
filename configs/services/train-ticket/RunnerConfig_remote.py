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

    TRAIN_TICKET_CONFIG = {
        "work_units": 10000,
        "warmup_requests": 1000,
        "cadence": {
            "baseline": 0,
            "low": 5000,
            "medium": 2000,
            "high": 1000,
        },
    }

    name = "train_ticket_gc_experiment_remote"

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
        self.profiler = None
        self.current_run_id = None

    def create_run_table_model(self) -> RunTableModel:

        subject = FactorModel("subject", ["train-ticket"])

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
            "train-ticket",
            self._safe_component(collector),
            self._safe_component(cadence_level),
            local_token,
        ])

    def start_run(self, context: RunnerContext) -> None:
        # The remote configuration performs the complete service lifecycle
        # inside one server-side command in start_measurement().
        pass

    def start_measurement(self, context: RunnerContext) -> None:

        collector = context.execute_run["collector"]
        cadence_level = context.execute_run["cadence_level"]

        cadence = self.TRAIN_TICKET_CONFIG["cadence"][cadence_level]
        warmup_requests = self.TRAIN_TICKET_CONFIG["warmup_requests"]

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

        lookup_url = (
            "http://localhost:12342"
            "/api/v1/userservice/users/fdse_microservice"
        )

        reset_url = (
            "http://localhost:12342"
            "/api/v1/userservice/users/gc-counter/reset"
        )

        # Server-side lifecycle:
        #
        #   start user-service
        #   -> wait until ready
        #   -> 1000 unmeasured DB-backed warm-up lookups
        #   -> reset explicit-GC cadence counter
        #   -> record service-log offset
        #   -> start JFR
        #   -> EnergiBridge wraps exactly 10000 measured lookups
        #   -> stop/dump JFR
        #   -> extract measured-only service log
        #   -> stop service
        #
        # run_subject.sh uses exec java, so SERVICE_PID is the JVM PID and can
        # be targeted directly by jcmd.
        remote_payload = (
            "set -euo pipefail; "
            "if [ -f scripts/orchestration/server_env.sh ]; then "
            "source scripts/orchestration/server_env.sh; "
            "fi; "
            'ENERGY_BIN="${ENERGIBRIDGE_BIN:-$(command -v energibridge || true)}"; '
            'test -n "$ENERGY_BIN"; '
            'SERVICE_LOG="$GC_REMOTE_RUN_DIR/train-ticket.log"; '
            'MEASURED_LOG="$GC_REMOTE_RUN_DIR/train-ticket-measured.log"; '
            'DONE_FILE="$GC_REMOTE_RUN_DIR/workload.done"; '
            'scripts/services/train-ticket/run_subject.sh '
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
            f'  if curl -fsS -o /dev/null "{lookup_url}"; then '
            '    READY=1; break; '
            '  fi; '
            '  if ! kill -0 "$SERVICE_PID" 2>/dev/null; then '
            '    echo "Train Ticket user-service exited before becoming ready" >&2; '
            '    exit 30; '
            '  fi; '
            '  sleep 0.5; '
            'done; '
            'if [ "$READY" -ne 1 ]; then '
            '  echo "Train Ticket user-service did not become ready in time" >&2; '
            '  exit 31; '
            'fi; '
            f'for _ in $(seq 1 {warmup_requests}); do '
            f'  curl -fsS -o /dev/null "{lookup_url}"; '
            'done; '
            f'curl -fsS -o /dev/null -X POST "{reset_url}"; '
            'LOG_OFFSET=$(wc -c < "$SERVICE_LOG"); '
            'jcmd "$SERVICE_PID" JFR.start '
            'name=gcstudy settings=profile >/dev/null; '
            'export WORKLOAD_DONE_FILE="$DONE_FILE"; '
            '"$ENERGY_BIN" '
            '--output "$GC_REMOTE_RUN_DIR/energibridge.csv" '
            '--summary '
            'scripts/services/train-ticket/run_workload.sh; '
            'test -f "$DONE_FILE"; '
            'jcmd "$SERVICE_PID" JFR.stop '
            'name=gcstudy '
            'filename="$JFR_FILE" >/dev/null; '
            'CURRENT_SIZE=$(wc -c < "$SERVICE_LOG"); '
            'if [ "$CURRENT_SIZE" -gt "$LOG_OFFSET" ]; then '
            '  tail -c +$((LOG_OFFSET + 1)) "$SERVICE_LOG" > "$MEASURED_LOG"; '
            'else '
            '  : > "$MEASURED_LOG"; '
            'fi; '
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
            raise RuntimeError("Remote Train Ticket run was not started.")

        return_code = self.profiler.wait()

        if return_code != 0:
            raise RuntimeError(
                f"Remote Train Ticket run exited with code {return_code}"
            )

        self._fetch_artifacts(context)

    def stop_measurement(self, context: RunnerContext) -> None:
        # JFR is stopped and dumped on the server before artefact retrieval.
        pass

    def stop_run(self, context: RunnerContext) -> None:
        # Service shutdown is part of the server-side lifecycle.
        pass

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

        energy_file = (
            context.run_dir
            / "energibridge.csv"
        )

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
            / "train-ticket-measured.log"
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

        cadence_level = context.execute_run["cadence_level"]
        cadence_n = self.TRAIN_TICKET_CONFIG["cadence"][cadence_level]
        work_units = self.TRAIN_TICKET_CONFIG["work_units"]

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
