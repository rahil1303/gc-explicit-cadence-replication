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

    DACAPO_CONFIGS = {
        "h2": {
            "size": "large",
            "iterations": 30,
            "cadence": {
                "baseline": 0,
                "low": 15,
                "medium": 6,
                "high": 3,
            },
        },

        "avrora": {
            "size": "large",
            "iterations": 17,
            "cadence": {
                "baseline": 0,
                "low": 8,
                "medium": 3,
                "high": 2,
            },
        },

        "fop": {
            "size": "default",
            "iterations": 50,
            "cadence": {
                "baseline": 0,
                "low": 25,
                "medium": 10,
                "high": 5,
            },
        },

        "jython": {
            "size": "large",
            "iterations": 20,
            "cadence": {
                "baseline": 0,
                "low": 10,
                "medium": 4,
                "high": 2,
            },
        },

        "lusearch": {
            "size": "large",
            "iterations": 20,
            "cadence": {
                "baseline": 0,
                "low": 10,
                "medium": 4,
                "high": 2,
            },
        },

        "pmd": {
            "size": "large",
            "iterations": 30,
            "cadence": {
                "baseline": 0,
                "low": 15,
                "medium": 6,
                "high": 3,
            },
        },

        "sunflow": {
            "size": "large",
            "iterations": 20,
            "cadence": {
                "baseline": 0,
                "low": 10,
                "medium": 4,
                "high": 2,
            },
        },

        "xalan": {
            "size": "large",
            "iterations": 20,
            "cadence": {
                "baseline": 0,
                "low": 10,
                "medium": 4,
                "high": 2,
            },
        },

        "biojava": {
            "size": "default",
            "iterations": 25,
            "cadence": {
                "baseline": 0,
                "low": 12,
                "medium": 5,
                "high": 3,
            },
        },
    }

    name = "dacapo_gc_experiment_greenlab_smoke"

    results_output_path = (
        Path.home()
        / "gc-explicit-cadence-study"
        / "results"
        / "dacapo"
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
        self.stdout_handle = None
        self.stderr_handle = None

    def create_run_table_model(self) -> RunTableModel:

        subject = FactorModel(
            "subject",
            ["fop"],
        )

        collector = FactorModel(
            "collector",
            ["G1"],
        )

        cadence_level = FactorModel(
            "cadence_level",
            ["baseline"],
        )

        self.run_table_model = RunTableModel(
            factors=[
                subject,
                collector,
                cadence_level,
            ],

            repetitions=1,

            data_columns=[
                "dataset_size",
                "invocation_iterations",
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
        subject: str,
        collector: str,
        cadence_level: str,
    ) -> str:

        local_token = self._safe_component(Path(context.run_dir).name)

        return "__".join([
            "dacapo",
            self._safe_component(subject),
            self._safe_component(collector),
            self._safe_component(cadence_level),
            local_token,
        ])

    def start_measurement(self, context: RunnerContext) -> None:
        subject = context.execute_run["subject"]
        collector = context.execute_run["collector"]
        cadence_level = context.execute_run["cadence_level"]

        benchmark_config = self.DACAPO_CONFIGS[subject]

        dataset_size = benchmark_config["size"]
        invocation_iterations = benchmark_config["iterations"]
        cadence = benchmark_config["cadence"][cadence_level]

        study_root = Path.home() / "gc-explicit-cadence-study"
        run_dir = Path(context.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env["JFR_FILE"] = str(run_dir / "recording.jfr")
        env["GC_LOG_FILE"] = str(run_dir / "gc.log")

        energy_file = run_dir / "energibridge.csv"

        gc_log_name = f"gc-{subject}-{collector}-n{cadence}.log"
        source_gc_log = (
            study_root
            / "subjects"
            / "dacapo"
            / "dacapo-23.11-MR2-chopin"
            / gc_log_name
        )

        payload = (
            "set -euo pipefail; "
            f'rm -f "{source_gc_log}"; '
            'ENERGY_BIN="${ENERGIBRIDGE_BIN:-$(command -v energibridge || true)}"; '
            'test -n "$ENERGY_BIN"; '
            '"$ENERGY_BIN" '
            f'--output "{energy_file}" '
            '--summary '
            'scripts/dacapo/run_subject.sh '
            f'{subject} {collector} {cadence} '
            f'-n {invocation_iterations} -s {dataset_size}; '
            f'cp "{source_gc_log}" "{run_dir / "gc.log"}"'
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
            raise RuntimeError("DaCapo run was not started.")

        return_code = self.profiler.wait()

        if self.stdout_handle is not None:
            self.stdout_handle.close()
            self.stdout_handle = None

        if self.stderr_handle is not None:
            self.stderr_handle.close()
            self.stderr_handle = None

        if return_code != 0:
            raise RuntimeError(
                f"DaCapo run exited with code {return_code}"
            )

    def stop_measurement(self, context: RunnerContext) -> None:
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

        metrics = self._parse_key_value_output(
            result.stdout
        )

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
                "Green Lab EnergiBridge parser did not "
                "return required metrics: "
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

            with log_file.open(
                "r",
                errors="ignore",
            ) as f:

                for line in f:
                    match = pattern.search(line)

                    if match:
                        matches.append(
                            float(match.group(1))
                        )

        if not matches:
            raise RuntimeError(
                "Could not find EnergiBridge wrapped "
                "execution time in stdout.log or stderr.log"
            )

        rounded = {
            round(value, 9)
            for value in matches
        }

        if len(rounded) > 1:
            raise RuntimeError(
                "Found multiple different EnergiBridge "
                "execution times in fetched logs: "
                f"{matches}"
            )

        return matches[-1]

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
            Path(context.run_dir)
            / "recording.jfr"
        )

        if not recording.exists():
            raise FileNotFoundError(
                f"JFR recording not found: {recording}"
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

        return self._parse_key_value_output(
            result.stdout
        )

    @staticmethod
    def _count_explicit_requests(
        context: RunnerContext,
    ) -> int:

        request_logs = [
            Path(context.run_dir) / "stdout.log",
            Path(context.run_dir) / "stderr.log",
        ]

        count = 0

        for request_log in request_logs:

            if not request_log.exists():
                continue

            with open(
                request_log,
                "r",
                errors="ignore",
            ) as f:
                count += sum(
                    1
                    for line in f
                    if "[CADENCE]" in line
                    and "issuing System.gc()" in line
                )

        return count

    def populate_run_data(
        self,
        context: RunnerContext,
    ) -> Optional[Dict[str, Any]]:

        energy = self._read_energy_summary(
            context
        )

        runtime_s = self._read_wrapped_runtime(
            context
        )

        energy_sampling_duration_s = energy[
            "runtime_s"
        ]

        package_energy_j = energy[
            "package_energy_j"
        ]

        dram_energy_j = energy[
            "dram_energy_j"
        ]

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

        avg_cpu_usage_pct = energy[
            "avg_cpu_usage_pct"
        ]

        avg_cpu_frequency_mhz = energy[
            "avg_cpu_frequency_mhz"
        ]

        jfr = self._read_jfr_summary(context)

        explicit_gc_requests = self._count_explicit_requests(context)

        subject = context.execute_run["subject"]
        cadence_level = context.execute_run["cadence_level"]

        benchmark_config = self.DACAPO_CONFIGS[subject]

        dataset_size = benchmark_config["size"]
        invocation_iterations = benchmark_config["iterations"]
        cadence_n = benchmark_config["cadence"][cadence_level]

        intended_gc_requests = (
            0
            if cadence_n == 0
            else invocation_iterations // cadence_n
        )

        return {
            "dataset_size":
                dataset_size,

            "invocation_iterations":
                invocation_iterations,

            "cadence_n":
                cadence_n,

            "intended_gc_requests":
                intended_gc_requests,

            "runtime_s": round(
                runtime_s,
                6,
            ),

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
