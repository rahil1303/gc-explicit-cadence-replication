# The Marginal Value of Explicit Garbage Collection

Experimental infrastructure for the study:

**The Marginal Value of Explicit Garbage Collection: Cadence, Reclamation, and Cost Across Java Workloads**

This project investigates whether explicitly requesting garbage collection through `System.gc()` provides measurable value in modern Java workloads, or primarily introduces additional runtime and energy overhead.

Rather than treating `System.gc()` as if it guarantees a garbage collection, the experiment distinguishes between:

1. explicit GC requests issued by the workload,
2. explicit GC activity actually realised by the JVM,
3. naturally triggered GC activity,
4. memory reclaimed,
5. runtime cost, and
6. energy cost.

The central analysis therefore follows:

```text
Requests
   ↓
Realised GC activity
   ↓
Memory reclaimed
   ↓
Effect on natural GC behaviour
   ↓
Runtime
   ↓
Energy
```

---

## Research Questions

### RQ1

**Does higher cadence of explicit `System.gc()` requests translate into additional realised GC activity?**

This compares the number of explicit requests issued by the instrumentation with the explicit GC activity subsequently observed from the JVM.

### RQ2

**When explicit requests create extra GC activity, do they improve useful-work efficiency and memory availability enough to justify their runtime and energy cost, or do they mainly add overhead?**

This relates realised explicit GC activity to reclaimed memory, subsequent natural GC behaviour, execution time, and system-level energy consumption.

---

## Experimental Design

The study uses a work-based intervention rather than a time-based one.

Each subject executes a fixed amount of useful work. Explicit `System.gc()` requests are introduced at controlled semantic work boundaries.

Four experimental conditions are evaluated:

| Condition | Intended explicit requests |
|---|---:|
| Baseline | 0 |
| Low | approximately 2 |
| Medium | approximately 5 |
| High | approximately 10 |

The exact cadence value is calibrated separately for each subject according to its fixed measured workload.

Natural garbage collection remains enabled in every configuration. Explicit requests are therefore **additional requests layered on top of normal collector behaviour**, rather than replacements for naturally triggered GC.

---

## Garbage Collectors

The experiment evaluates five OpenJDK collectors:

- Serial GC
- Parallel GC
- G1 GC
- Shenandoah GC
- ZGC

The target runtime for production experiments is **Eclipse Temurin JDK 21 LTS**.

---

## Subjects

The study contains **12 Java workloads**:

- 9 DaCapo Chopin benchmarks
- 3 application-level workloads

### DaCapo Chopin

The selected DaCapo 23.11-MR2 subjects are:

| Subject | Dataset | Timed iterations | Low cadence | Medium cadence | High cadence |
|---|---|---:|---:|---:|---:|
| h2 | large | 30 | 15 | 6 | 3 |
| avrora | large | 17 | 8 | 3 | 2 |
| fop | default | 50 | 25 | 10 | 5 |
| jython | large | 20 | 10 | 4 | 2 |
| lusearch | large | 20 | 10 | 4 | 2 |
| pmd | large | 30 | 15 | 6 | 3 |
| sunflow | large | 20 | 10 | 4 | 2 |
| xalan | large | 20 | 10 | 4 | 2 |
| biojava | default | 25 | 12 | 5 | 3 |

The cadence value `N` determines when `System.gc()` is requested: every `N`th measured iteration.

Because some iteration counts are not evenly divisible, the realised number of requests is approximately 2, 5, and 10 rather than exactly those values for every benchmark.

DaCapo warm-up iterations do not advance the explicit-GC cadence counter, and DaCapo's built-in pre-iteration garbage collection is disabled.

---

## Application Workloads

### SableCC 3.7

One unit of work is a successfully completed grammar-generation operation.

Fixed measured workload: **100 completed grammar-generation operations**.

| Condition | Cadence | Intended requests |
|---|---:|---:|
| Baseline | 0 | 0 |
| Low | 50 | 2 |
| Medium | 20 | 5 |
| High | 10 | 10 |

### Spring PetClinic REST

One unit of work is a successful controller operation exercised through the fixed HTTP workload.

Fixed measured workload: **5,000 requests**.

| Condition | Cadence | Intended requests |
|---|---:|---:|
| Baseline | 0 | 0 |
| Low | 2500 | 2 |
| Medium | 1000 | 5 |
| High | 500 | 10 |

### Train Ticket `ts-user-service`

One unit of work is a successful database-backed user lookup.

Fixed measured workload: **10,000 successful lookups**.

| Condition | Cadence | Intended requests |
|---|---:|---:|
| Baseline | 0 | 0 |
| Low | 5000 | 2 |
| Medium | 2000 | 5 |
| High | 1000 | 10 |

The measured phase is explicitly separated from service startup and warm-up. Before measurement:

1. the service is started,
2. readiness is verified,
3. 1,000 warm-up lookups are performed,
4. the explicit-GC work counter is reset,
5. JFR recording begins,
6. the fixed 10,000-lookup workload is executed.

---

## Measurement Pipeline

The experiment combines three independent measurement sources.

### Explicit request instrumentation

Instrumentation emits a marker immediately before each injected `System.gc()` request.

This provides the number of **requests issued**, independently of whether the JVM subsequently performs a collection.

### Java Flight Recorder

JFR is used to obtain realised GC activity.

The parser separates:

- total GC events,
- explicit GC events,
- natural GC events,
- total GC pause time,
- explicit GC pause time,
- natural GC pause time,
- total reclaimed memory,
- explicitly reclaimed memory,
- naturally reclaimed memory.

Explicit events are attributed using the GC cause recorded by the JVM.

### EnergiBridge

EnergiBridge provides system-level execution measurements including:

- total energy consumption,
- wall-clock runtime,
- average power,
- average CPU utilisation,
- average CPU frequency.

---

## Requests Are Not Assumed to Equal GC Events

A key property of the design is that:

```text
explicit requests issued != necessarily explicit GC events realised
```

`System.gc()` is a request to the JVM rather than a binding command.

A mismatch between request count and realised explicit GC activity is therefore treated as an experimental observation rather than automatically as a failed run.

---

## Full Experimental Matrix

The planned production experiment contains:

```text
12 subjects
× 5 collectors
× 4 explicit-GC conditions
× 10 independent repetitions
= 2,400 runs
```

The DaCapo subset accounts for:

```text
9 × 5 × 4 × 10 = 1,800 runs
```

---

## Repository Structure

```text
.
├── configs/
│   ├── dacapo/
│   └── services/
│       ├── petclinic/
│       ├── sablecc/
│       └── train-ticket/
├── instrumentation/
│   └── CadenceCallback.java
├── metadata/
├── parsers/
│   ├── parse_gc_log.py
│   └── parse_jfr_gc.py
├── results/
│   ├── dacapo/
│   └── services/
├── scripts/
│   ├── dacapo/
│   └── services/
│       ├── petclinic/
│       ├── sablecc/
│       └── train-ticket/
└── subjects/
    ├── dacapo/
    └── services/
        ├── sablecc-3.7/
        ├── spring-petclinic-rest/
        └── train-ticket/
```

Generated build artefacts, raw measurement traces, JFR recordings, logs, and the unpacked DaCapo distribution are intentionally excluded from version control.

---

## DaCapo Dependency

The DaCapo runtime is not committed to this repository because the unpacked distribution is several gigabytes in size.

The experiment currently uses **DaCapo 23.11-MR2 Chopin**.

The distribution should be placed under:

```text
subjects/dacapo/
```

so that the resulting layout contains:

```text
subjects/dacapo/dacapo-23.11-MR2-chopin.jar
subjects/dacapo/dacapo-23.11-MR2-chopin/
```

---

## Application Source Trees

The repository retains the application source trees used during local experiment development so that the instrumentation points and workload integration remain visible and reproducible.

Generated Maven/Java build outputs are excluded through `.gitignore`.

The experimentally relevant instrumentation is located at semantic work boundaries rather than being triggered according to wall-clock time.

---

## Experiment Runner

Experiments are orchestrated through Experiment Runner configuration files under `configs/`.

The corresponding workload and service lifecycle scripts are under `scripts/`.

A typical configuration controls:

- subject,
- collector,
- cadence,
- workload size,
- repetition,
- JFR recording,
- EnergiBridge execution,
- result parsing.

---

## Output Metrics

The processed experimental dataset contains fields corresponding to:

```text
subject
collector
cadence
explicit_gc_requests

total_gc_events
explicit_gc_events
natural_gc_events

total_gc_pause_ms
explicit_gc_pause_ms
natural_gc_pause_ms

total_reclaimed_mb
explicit_reclaimed_mb
natural_reclaimed_mb

runtime_s
system_energy_j
avg_power_w
avg_cpu_usage_pct
avg_cpu_frequency_mhz
```

DaCapo records additionally retain benchmark-specific workload metadata such as:

```text
dataset_size
timed_iterations
cadence_n
intended_gc_requests
```

---

## Run Validity

A production run is considered valid only when:

- the measured workload completes,
- the configured explicit requests are issued correctly,
- the JFR recording is complete and parseable,
- the energy trace covers the measured workload,
- parsed GC measurements are internally consistent.

A mismatch between explicit requests and realised explicit GC events does **not** by itself invalidate a run.

---

## Repetitions

Each subject × collector × cadence configuration is executed through **10 independent invocations**.

For DaCapo, these independent repetitions are distinct from the timed benchmark iterations occurring within a single invocation.

---

## Current Status

The experimental instrumentation and local execution pipelines have been implemented and validated across the DaCapo and application execution paths.

Local validation has confirmed:

- controlled explicit-GC request injection,
- request counting,
- separation of explicit and natural GC activity,
- JFR collection and parsing,
- reclaimed-memory extraction,
- EnergiBridge integration,
- fixed workload boundaries for the three application subjects.

These local runs are treated as **mechanism and pipeline validation**, not as empirical findings.

Production measurements will be collected on the dedicated experimental server once the server environment and measurement tooling have been provisioned and validated.

---

## Results

Production results are not yet included.

Raw measurement artefacts are intentionally excluded from the repository during experiment development. Processed datasets and analysis artefacts will be added once production data collection has been completed.

---

## Study

This repository accompanies ongoing research on the marginal value of developer-requested garbage collection in Java.

The study focuses not simply on whether calling `System.gc()` changes runtime or energy consumption, but on the mechanism connecting:

```text
request cadence
→ realised collector behaviour
→ reclamation
→ changes in natural GC activity
→ performance
→ energy
```
