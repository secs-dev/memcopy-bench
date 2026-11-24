# Experiment tooling

`tools/run_experiment.py` runs the packet scenario benchmark one or more times,
records every measurement, and computes descriptive statistics. The statistics
live in `tools/bench_stats.py` so they can be unit-tested without a benchmark
binary or a specific platform.

- `run_experiment.py` — orchestrator (config, environment, launches, artifacts)
- `bench_stats.py` — statistics and `stats.json` / `stats.csv` writers
- `tests/test_bench_stats.py` — unit tests for the statistics layer
- `requirements.txt` — Python dependencies (numpy, scipy, plotly)

## Install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
```

## Quick start

Single run, exactly like the old behaviour (writes `results/<name>.json` and
`results/<name>.meta.json` in addition to the new run directory):

```bash
.venv/bin/python tools/run_experiment.py scenarious/example_scenario.json
```

Multi-run with statistics and plots:

```bash
.venv/bin/python tools/run_experiment.py scenarious/stability_scenario.json
```

The same through `just`:

```bash
just experiment scenarious/stability_scenario.json
just stats scenarious/stability_scenario.json   # recompute, do not measure
just stats-test                                 # unit tests for bench_stats
```

## Run variants

### Plan only, no side effects

```bash
.venv/bin/python tools/run_experiment.py scenarious/stability_scenario.json --dry-run
```

Prints the number of rounds, launches, repetitions, an estimated benchmark
time, the environment plan, and the seeded order of the first round. No
environment changes and no benchmark launches are performed.

### Batch isolation (default)

One process per round runs all selected benchmarks. Google Benchmark
repetitions inside that process can be randomly interleaved, which reduces
ordering effects between benchmarks.

```bash
.venv/bin/python tools/run_experiment.py config.json \
  --isolation batch --runs 5 --warmup 1 --repetitions 3
```

### Per-benchmark isolation

One process per benchmark with the order shuffled by `seed`. Slower, but it
removes drift and ordering effects between benchmarks and gives each benchmark
a fresh process.

```bash
.venv/bin/python tools/run_experiment.py config.json \
  --isolation per-benchmark --runs 7 --repetitions 1 --seed 1234
```

Cost is `rounds x benchmarks x repetitions x min_time`, plus process startup.
With per-benchmark isolation `interleave` has no effect (a process contains a
single benchmark).

### Resume an interrupted experiment

Every observation is appended to `measurements.jsonl` immediately, and raw
round files are kept, so `--resume` continues the latest run of the experiment
and skips measurements that already exist:

```bash
.venv/bin/python tools/run_experiment.py config.json --resume
```

### Recompute statistics only

Works without the benchmark binary and without touching the environment:

```bash
.venv/bin/python tools/run_experiment.py config.json --stats-only
.venv/bin/python tools/run_experiment.py config.json --stats-only --skip-plot
```

### Continue with the environment left applied

By default governor/turbo/power plan are restored in a `finally` block, so an
interruption still restores them. `--keep-environment` leaves them applied:

```bash
.venv/bin/python tools/run_experiment.py config.json --keep-environment
```

### Use an explicit run directory

```bash
.venv/bin/python tools/run_experiment.py config.json \
  --run-dir /var/tmp/bench/my-run --reports-dir /var/tmp/bench/reports
```

`--run-dir` disables the `results/<name>/latest` pointer update.

### Choose output locations

```bash
.venv/bin/python tools/run_experiment.py config.json \
  --results-dir /data/results --reports-dir /data/reports
```

## Experiment configuration

The config is a JSON object. Missing keys fall back to the defaults below;
unknown keys are ignored.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required | Experiment name; used for directories and legacy file names. |
| `binary` | string | `bazel-bin/benchmarks/scenarious/packet_scenario_benchmark` | Benchmark executable. `.exe` and `PATH` are tried as fallbacks. |
| `scenarios` | list of strings | `[]` (required non-empty) | Scenario names registered in C++. |
| `header_sizes` | list of int | `[16]` (required non-empty) | Header sizes `H` to run. |
| `payload_sizes` | list of int | `[64, 512, 1500, 9000]` (required non-empty) | Payload sizes `P` to run. |
| `min_time` | string | `"1s"` | Google Benchmark minimum time **per repetition**. Only `<float>s`, a bare number (seconds), or `<integer>x` are accepted (`20ms` is invalid and rejected). |
| `runs` | int >= 1 | `1` | Number of measured rounds (process launches). |
| `warmup_runs` | int >= 0 | `0` | Rounds executed before the measured ones and excluded from statistics. |
| `repetitions` | int >= 1 | `1` | In-process Google Benchmark repetitions. |
| `cooldown_s` | number >= 0 | `0.0` | Sleep between rounds (not applied after the last round). |
| `isolation` | `"batch"` or `"per-benchmark"` | `"batch"` | Process granularity, see above. |
| `interleave` | bool | `true` | Pass `--benchmark_enable_random_interleaving` when `repetitions > 1` (batch only). |
| `seed` | int | `0` | Shuffling seed for per-benchmark order. Stored in metadata and stats. |
| `timeout_s` | number > 0 | `1800` | Timeout for one process launch. |
| `on_trial_failure` | `"skip"` or `"abort"` | `"skip"` | Continue with the remaining rounds or abort the experiment. |
| `env` | object | see below | Platform tuning, see next table. |
| `stats` | object | see below | Statistics options, see next table. |

Number of observations per benchmark: `runs x repetitions`.

### `env` keys

| Key | Type | Default | Linux | macOS | Windows |
| --- | --- | --- | --- | --- | --- |
| `taskset_cpus` | list of int | `[]` | `taskset -c` (no affinity when empty) | skipped | `SetProcessAffinityMask` after spawn |
| `governor` | string or null | `null` | `cpupower frequency-set -g <value>` | skipped | power plan name (`high_performance`, `balanced`, `power_saver`) or a raw GUID via `powercfg` |
| `no_turbo` | bool or null | `null` | `/sys/.../intel_pstate/no_turbo` | skipped | skipped |

Original values are captured before applying and restored afterwards. Anything
that cannot be applied is recorded under `skipped` in `meta.json`, never
silently ignored. On unsupported platforms the run proceeds and reports the
reason.

### `stats` keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `percentiles` | list of numbers 0–100 | `[5, 25, 50, 75, 95]` | Percentiles computed for both time and throughput. |
| `trim` | `"none"`, `"iqr"`, `"mad"` | `"none"` | Optional outlier removal before statistics. Removed samples are listed under `discarded`. |
| `ci_level` | number in (0, 1) | `0.95` | Confidence level for `ci95_mean` (t-interval; needs `n >= 3`). |
| `min_meaningful_n` | int | `10` | Below this many observations a warning says percentiles should be read as min/max. |
| `load_avg_threshold` | number | `0.25` | Warn when `load_avg[0] > threshold x num_cpus` in a round. |

## Command-line reference

```
run_experiment.py CONFIG [options]
```

| Flag | Meaning |
| --- | --- |
| `--results-dir DIR` | Base directory for run directories (default `results`). |
| `--reports-dir DIR` | Where `plot.py` writes HTML (default `reports`). |
| `--run-dir DIR` | Explicit run directory; disables the `latest` pointer. |
| `--resume` | Continue the latest run of this experiment. |
| `--stats-only` | Recompute `stats.json`/`stats.csv` from existing measurements; skips binary lookup, coverage checks, environment changes and launches. |
| `--dry-run` | Print the plan and exit. |
| `--skip-plot` | Do not invoke `plot/plot.py`. |
| `--keep-environment` | Do not restore governor/turbo/power plan. |
| `--runs N` | Override config `runs`. |
| `--warmup N` | Override config `warmup_runs`. |
| `--repetitions N` | Override config `repetitions`. |
| `--cooldown SECONDS` | Override config `cooldown_s`. |
| `--isolation MODE` | Override config `isolation` (`batch`, `per-benchmark`). |
| `--seed N` | Override config `seed`. |
| `--timeout SECONDS` | Override config `timeout_s`. |

CLI overrides are applied before validation.

## Output layout

```
results/<name>/<UTC timestamp>/
  measurements.jsonl    # one JSON object per observation
  raw/                  # raw Google Benchmark JSON
    round-000.json      #   batch isolation
    round-000/<name>.json   # per-benchmark isolation
  meta.json             # config, environment changes, system info, run summary
  stats.json            # descriptive statistics per benchmark and metric
  stats.csv             # time statistics as a table (throughput lives in stats.json)
results/<name>/latest   # text file with the name of the most recent run
```

`measurements.jsonl` line:

```json
{"round": 1, "warmup": false, "name": "BM_PacketScenario/Memcpy/h8/p64",
 "repetition": 0, "scenario": "Memcpy", "header": 8, "payload": 64,
 "bytes": 72, "status": "ok", "real_time_ns": 2.6, "cpu_time_ns": 2.6,
 "iterations": 27159489, "bytes_per_second": 27889987697.5,
 "context": {"num_cpus": 8, "mhz_per_cpu": 4200, "load_avg": [0.4, 0.5, 0.6],
             "cpu_scaling_enabled": false, "caches": [...]}}
```

Failed launches are recorded with `"status": "failed"` and a `reason`; they
appear in `stats.json` under `gaps` and do not block later rounds unless
`on_trial_failure` is `abort`.

### Legacy compatibility

When `runs == 1`, `warmup_runs == 0`, `repetitions == 1` and
`isolation == "batch"`, the script also copies the raw JSON to
`results/<name>.json` and metadata to `results/<name>.meta.json`, so older
workflows keep working. The run directory is still created.

## Statistics semantics

- Two metrics are computed independently: `cpu` (`cpu_time`) and `real`
  (`real_time`).
- Warm-up rounds are stored but excluded from statistics.
- Percentiles use linear interpolation (numpy `method="linear"`).
- Confidence intervals are t-intervals of the mean and are omitted for `n < 3`.
- Throughput is produced by inverting the time summary, so percentiles map
  exactly (`p05` of throughput equals `bytes / p95` of time). Mean/std/cv/mad
  of throughput are advisory approximations.
- `sigma_within` is the pooled variance inside rounds; `sigma_between` is the
  dispersion of per-round medians. They separate measurement noise from
  machine drift.
- `warnings` may include: fewer observations than `runs x repetitions`, high
  load average, changed `cpu_scaling_enabled`, `bytes_per_second` inconsistent
  with `bytes/time`, and too few samples for meaningful percentiles.
- `discarded` lists every sample removed by `trim`, with its metric and reason.

Plot names produced from `stats.json` include the metric:
`packet_scenario_h8_throughput_cpu.html`, `..._throughput_real.html`, and the
matching `_time_*.html` files. Raw single-run JSON still produces the original
unsuffixed names.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Statistics were produced (partial failures are reported but tolerated). |
| `1` | No successful measurements were collected. |
| `2` | Invalid config, missing binary, or `--resume`/`--stats-only` without an existing run. |
| `130` | Interrupted with Ctrl+C; existing measurements are preserved and statistics are still written. |

## Practical notes

- Benchmarks must run sequentially; the tool never runs two benchmark processes
  in parallel.
- `min_time` applies to each repetition, so total benchmark time is roughly
  `launches x repetitions x min_time`.
- In per-benchmark mode the first round's order is reproducible from `seed`.
- Google Benchmark aggregate entries (`mean`, `median`, `stddev`, `cv`) are
  excluded when importing; only individual repetitions become observations.
- `--stats-only` is safe to use while a run is still incomplete.

## Tests

```bash
.venv/bin/python -m unittest discover -s tools/tests -v
```
