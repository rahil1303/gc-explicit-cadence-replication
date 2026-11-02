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
import subprocess


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

    name = "train_ticket_gc_smoke"

    results_output_path = (
        Path.home()
        / "gc-explicit-cadence-study"
        / "results"
        / "services"
        / "train-ticket"
        / "smoke-high"
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
        self.stdout_handle = None
        self.stderr_handle = None

    def create_run_table_model(self) -> RunTableModel:

        subject = FactorModel("subject", ["train-ticket"])

        collector = FactorModel(
            "collector",
            [
                "Serial"],
        )

        cadence_level = FactorModel(
            "cadence_level",
            [
                "high"],
        )

        self.run_table_model = RunTableModel(
            factors=[
                subject,
                collector,
                cadence_level,
            ],
            repetitions=1,
            data_columns=[
                "work_units",
                "cadence_n",
                "intended_gc_requests",

                "runtime_s",
                "energy_sampling_duration_s",
                "package_energy_j",
                "dram_energy_j",
                "avg_package_power_w",
                "avg_dram_power_w",
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
        run_dir = Path(context.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        lookup_url = (
            "http://localhost:12342"
            "/api/v1/userservice/users/fdse_microservice"
        )

        reset_url = (
            "http://localhost:12342"
            "/api/v1/userservice/users/gc-counter/reset"
        )

        service_log = run_dir / "train-ticket.log"
        measured_log = run_dir / "train-ticket-measured.log"
        done_file = run_dir / "workload.done"
        energy_file = run_dir / "energibridge.csv"
        jfr_file = run_dir / "recording.jfr"

        env = dict(os.environ)

        payload = (
            "set -euo pipefail; "
            'ENERGY_BIN="${ENERGIBRIDGE_BIN:-$(command -v energibridge || true)}"; '
            'test -n "$ENERGY_BIN"; '
            f'SERVICE_LOG="{service_log}"; '
            f'MEASURED_LOG="{measured_log}"; '
            f'DONE_FILE="{done_file}"; '
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
            f'--output "{energy_file}" '
            '--summary '
            'scripts/services/train-ticket/run_workload.sh; '
            'test -f "$DONE_FILE"; '
            'jcmd "$SERVICE_PID" JFR.stop '
            'name=gcstudy '
            f'filename="{jfr_file}" >/dev/null; '
            'CURRENT_SIZE=$(wc -c < "$SERVICE_LOG"); '
            'if [ "$CURRENT_SIZE" -gt "$LOG_OFFSET" ]; then '
            '  tail -c +$((LOG_OFFSET + 1)) "$SERVICE_LOG" > "$MEASURED_LOG"; '
            'else '
            '  : > "$MEASURED_LOG"; '
            'fi; '
            'cleanup; '
            'trap - EXIT'
        )

        self.stdout_handle = (run_dir / "stdout.log").open("w")
        self.stderr_handle = (run_dir / "stderr.log").open("w")

        self.profiler = subprocess.Popen(
            ["/bin/bash", "-lc", payload],
            cwd=study_root,
            env=env,
            stdout=self.stdout_handle,
            stderr=self.stderr_handle,
        )

    def interact(self, context: RunnerContext) -> None:
        if self.profiler is None:
            raise RuntimeError("Train Ticket run was not started.")

        return_code = self.profiler.wait()

        if self.stdout_handle is not None:
            self.stdout_handle.close()
            self.stdout_handle = None

        if self.stderr_handle is not None:
            self.stderr_handle.close()
            self.stderr_handle = None

        if return_code != 0:
            raise RuntimeError(
                f"Train Ticket run exited with code {return_code}"
            )

    def stop_measurement(self, context: RunnerContext) -> None:
        # JFR is stopped and dumped on the server before artefact retrieval.
        pass

    def stop_run(self, context: RunnerContext) -> None:
        # Service shutdown is part of the server-side lifecycle.
        pass

    @staticmethod
    def _parse_key_value_output(
        output: str,
    ) -> Dict[str, Any]:

        metrics = {}

        for line in output.splitlines():
            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()

            try:
                if value.lower() == "nan":
                    metrics[key] = float("nan")
                elif any(marker in value for marker in [".", "e", "E"]):
                    metrics[key] = float(value)
                else:
                    metrics[key] = int(value)
            except ValueError:
                metrics[key] = value

        return metrics

    def _read_energy_summary(
        self,
        context: RunnerContext,
    ) -> Dict[str, Any]:

        parser = (
            Path.home()
            / "gc-explicit-cadence-study"
            / "parsers"
            / "parse_energibridge_gl1.py"
        )

        energy_file = (
            Path(context.run_dir)
            / "energibridge.csv"
        )

        if not energy_file.exists():
            raise FileNotFoundError(
                f"Fetched EnergiBridge CSV not found: {energy_file}"
            )

        result = subprocess.run(
            [
                "python3",
                str(parser),
                str(energy_file),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        metrics = self._parse_key_value_output(result.stdout)

        required = {
            "runtime_s",
            "package_energy_j",
            "dram_energy_j",
            "avg_package_power_w",
            "avg_dram_power_w",
            "avg_cpu_usage_pct",
            "avg_cpu_frequency_mhz",
        }

        missing = required - set(metrics)

        if missing:
            raise RuntimeError(
                "Green Lab EnergiBridge parser did not return required metrics: "
                + ", ".join(sorted(missing))
            )

        return metrics

    @staticmethod
    def _read_wrapped_runtime(
        context: RunnerContext,
    ) -> float:

        pattern = re.compile(
            r"Energy consumption in joules:\s*"
            r"[-+0-9.eE]+\s+for\s+"
            r"([-+0-9.eE]+)\s+sec of execution\."
        )

        candidates = [
            Path(context.run_dir) / "stdout.log",
            Path(context.run_dir) / "stderr.log",
        ]

        matches = []

        for log_file in candidates:
            if not log_file.exists():
                continue

            with log_file.open("r", errors="ignore") as f:
                for line in f:
                    match = pattern.search(line)
                    if match:
                        matches.append(float(match.group(1)))

        if not matches:
            raise RuntimeError(
                "Could not find EnergiBridge wrapped execution time "
                "in stdout.log or stderr.log"
            )

        rounded = {round(value, 9) for value in matches}
        if len(rounded) > 1:
            raise RuntimeError(
                "Found multiple different EnergiBridge execution times "
                f"in fetched logs: {matches}"
            )

        return matches[-1]

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

        energy = self._read_energy_summary(context)

        runtime_s = self._read_wrapped_runtime(context)

        energy_sampling_duration_s = energy["runtime_s"]

        package_energy_j = energy["package_energy_j"]
        dram_energy_j = energy["dram_energy_j"]

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

        avg_cpu_usage_pct = energy["avg_cpu_usage_pct"]
        avg_cpu_frequency_mhz = energy["avg_cpu_frequency_mhz"]

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

            "runtime_s": round(runtime_s, 6),

            "energy_sampling_duration_s": round(
                energy_sampling_duration_s,
                6,
            ),

            "package_energy_j": round(
                package_energy_j,
                6,
            ),

            "dram_energy_j": round(
                dram_energy_j,
                6,
            ),

            "avg_package_power_w": round(
                avg_package_power_w,
                6,
            ),

            "avg_dram_power_w": round(
                avg_dram_power_w,
                6,
            ),

            "avg_cpu_usage_pct": round(
                avg_cpu_usage_pct,
                6,
            ),

            "avg_cpu_frequency_mhz": round(
                avg_cpu_frequency_mhz,
                6,
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
