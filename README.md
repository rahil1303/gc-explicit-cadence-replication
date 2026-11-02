# The Marginal Value of Explicit Garbage Collection

**Replication package for _The Marginal Value of Explicit Garbage Collection: Cadence, Reclamation, and Cost Across Java Workloads_.**

This repository contains the experiment configurations, Java instrumentation, modified workloads, parsers, processed measurements, analysis notebook, and generated tables and figures.

## What is the study about?

Java normally decides when to collect garbage. Applications can also request collection using `System.gc()`, but a request does not necessarily correspond to exactly one collection or produce useful memory reclamation.

We investigate what happens as an application requests collection more frequently: **does additional GC reclaim useful memory, change naturally occurring GC, or improve or worsen runtime and energy consumption?** We distinguish requests issued, GC activity attributed to those requests, observed net heap-use reductions, and execution outcomes.

## How the experiments were conducted

Each workload performs a fixed amount of work, with `System.gc()` inserted at work-based boundaries rather than time intervals. Natural GC remains enabled throughout.

| Condition | Target explicit requests per measured workload |
| --- | ---: |
| Baseline | 0 |
| Low | Approximately 2 |
| Medium | Approximately 5 |
| High | Approximately 10 |

We tested **five collectors** (Serial, Parallel, G1, Shenandoah, ZGC) across **12 Java workloads**:

- **Nine DaCapo Chopin 23.11-MR2 benchmarks:** `avrora`, `biojava`, `fop`, `h2`, `jython`, `lusearch`, `pmd`, `sunflow`, and `xalan`. A Java iteration callback issues requests at calibrated measured-iteration cadences; pre-iteration GC is disabled.
- **Spring PetClinic REST:** 5,000 measured HTTP operations, after 100 warm-up requests.
- **Train Ticket (`ts-user-service`):** 10,000 measured database-backed user lookups, after 1,000 warm-up lookups; MySQL runs locally.
- **SableCC 3.7:** 100 measured grammar-generation operations.

There were **10 independent repetitions** per subject, collector, and condition, each in a fresh JVM or service process. This gives **12 × 5 × 4 × 10 = 2,400 planned runs**. Executions followed the predetermined run-table order, with a 30-second cooldown between runs. JVM heap sizes were left to collector/JVM defaults; explicit collection was not disabled or redirected.

The measurement pipeline combines:

- **Instrumented request markers**, recording calls to `System.gc()`.
- **Java Flight Recorder (JFR)**, distinguishing GC attributed to `System.gc()` from naturally triggered GC, and measuring pauses and observed net heap-use reductions.
- **EnergiBridge and Intel RAPL**, recording elapsed runtime and CPU-package energy, with supplementary CPU and DRAM measurements. CPU-package energy is **not** whole-system energy.

### Experimental system

| Component | Specification |
| --- | --- |
| Execution host | Dedicated Green Lab server (`gl1`) |
| Operating system | Ubuntu 24.04.4 |
| Processor | Two Intel Xeon Silver 4208 CPUs |
| Memory | Approximately 376 GiB RAM |
| Java runtime | OpenJDK 21.0.12.1 |
| CPU/memory placement | First NUMA node, restricted using `numactl` |
| Execution and measurement | ExperimentRunner, workloads, JFR, and EnergiBridge on the same host |
| Energy source | Intel RAPL CPU-package counters, sampled using EnergiBridge |
| Train Ticket database | Local MySQL on the measurement host |

These details describe the system used to **collect the experimental data**. Reproducing the statistical analysis below does not require the same hardware.

## Reproduce the analysis and results

The **processed data needed to rerun the analysis is included in this repository**. To regenerate the statistical tables and figures, you do not need to build the Java workloads or repeat the 2,400-run measurement campaign.

**1. Clone the repository**

```bash
git clone https://github.com/rahil1303/gc-explicit-cadence-replication.git
cd gc-explicit-cadence-replication
```

**2. Set up Python and Jupyter**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install jupyterlab numpy pandas scipy matplotlib statsmodels
```

**3. Open and run the primary notebook**

```bash
jupyter lab data/notebooks/01_gc_analysis_clean.ipynb
```

Select **Kernel → Restart Kernel and Run All Cells**. The notebook reads CSVs under `data/processed/` and writes analysis-ready data, statistical tables, and figures under `data/outputs/`. Run Jupyter from the repository root so the project paths resolve correctly.

**4. Verify the principal dataset totals**

| Measure | Expected value |
| --- | ---: |
| Planned runs | 2,400 |
| Usable runs | 2,382 |
| Excluded runs | 18 |
| Usable treatment runs | 1,787 |
| Baseline-paired treatment runs | 1,772 |
| Explicit-GC requests in usable treatment runs | 9,930 |

The notebook's analysis uses descriptive summaries and bootstrap intervals, matched-baseline comparisons, collector-specific blocked fixed-effect regression with Holm correction, and request-level associations. Precomputed tables and figures under `data/outputs/` are included so regenerated outputs can be compared with the committed results.

The dependencies listed above are the notebook's main Python packages; their exact tested versions have not been pinned in a lockfile.

## Repository layout

```text
configs/                       Experiment configurations
instrumentation/               DaCapo explicit-GC callback
parsers/                       JFR, energy, and GC parsing scripts
scripts/                       Workload execution and orchestration
subjects/services/             Modified PetClinic, Train Ticket, and SableCC sources
data/original-run-tables/       Original runner table exports
data/processed/                 Processed run-, request-, and context-level CSVs
data/notebooks/
  01_gc_analysis_clean.ipynb   Primary analysis notebook
data/outputs/
  analysis-data/               Generated analysis datasets
  tables/                      Generated statistical and descriptive tables
  figures/                     Generated PDF and PNG figures
figures/                       Selected manuscript figures
```

## Re-executing the measurement campaign

The `configs/`, `instrumentation/`, `parsers/`, `scripts/`, and `subjects/services/` directories preserve the experiment implementation. Repeating data collection requires a compatible Java 21 environment, [DaCapo Chopin 23.11-MR2](https://dacapobench.org/), [EnergiBridge](https://github.com/tdurieux/EnergiBridge), JFR, the service build dependencies, and MySQL for Train Ticket. The external [ExperimentRunner](https://github.com/S2-group/experiment-runner) was used at commit `56620fa6e48ef99180824a24369dc2f6ec3d6910`.

Machine-specific configuration paths may require adjustment on another host. Full raw JFR recordings, per-run energy traces, and logs are not committed here because of their volume; the **processed measurements and inputs needed for the notebook are committed**.

## Interpretation and licensing

`System.gc()` is treated as a request, not a guarantee of one collection. Reported reclaimed memory is the observed *net change in heap use* around collections, not gross bytes freed. Execution effects vary with collector, subject, and cadence; the study does not identify a universally optimal request rate.

The top-level `LICENSE` covers original repository material as applicable. Bundled third-party sources retain their own licenses and notices; the top-level license does not replace them.
