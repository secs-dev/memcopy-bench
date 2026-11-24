# memcopy-bench

A C/C++ research framework for evaluating data movement and frame-processing
techniques in Telecom L2 scenarios. The initial focus is PDCP and GTP-U frame
processing on x86, while reusable library code avoids unnecessary
architecture-specific assumptions.

## Layout

- `lib/` — reusable frame, memory, transport, and scenario implementations.
  Libraries do not depend on benchmarking or reporting infrastructure.
  `lib/test/` is reserved for shared test-only support; component unit tests
  remain colocated with their implementations.
- `benchmarks/` — Google Benchmark executables. `memory/` holds operation
  microbenchmarks; `scenarios/` will hold complete workflow benchmarks.
- `tests/integration/` — reserved for cross-component integration tests when
  they are needed. Unit tests are colocated with the library code they verify.
- `tools/` — experiment automation and analysis utilities.
- `results/` — raw benchmark output and experiment metadata (kept separate
  from analysis).
- `reports/` — research findings and comparative analysis.

## Prerequisites

Install [Bazelisk](https://github.com/bazelbuild/bazelisk). It selects the
Bazel version pinned in `.bazelversion`. Commands are run from the repository
root. Optimized mode is enabled by default in `.bazelrc`.

## C++ editor tooling

Generate Bazel-aware compile commands for `clangd` and `clang-tidy` after
changing C++ sources, targets, dependencies, or compile settings:

```bash
bazel run @hedron_compile_commands//:refresh_all
```

The generated root-level `compile_commands.json` is intentionally ignored by
Git. Restart the editor language server after refreshing it if it does not
reload the database automatically.

## Build, lint, and test

```bash
bazel build //...
bazel test //...
```

Run the same C++ lint check used by CI (requires `clang-tidy` on `PATH`):

```bash
sh tools/run-clang-tidy.sh
```

Run tests with the supported sanitizers:

```bash
bazel test --config=asan //... --test_output=errors
bazel test --config=ubsan //... --test_output=errors
```

For example, the initial reusable algorithm and its colocated test are:

```bash
bazel build //lib/memory:memcpy
bazel test //lib/memory:memcpy_test
```

## Run a benchmark

```bash
bazel run //benchmarks/memory:memcpy_benchmark

# Google Benchmark options follow `--`.
# Note: bazel run executes from a sandbox working directory, so use an absolute path for --benchmark_out:
bazel run //benchmarks/memory:memcpy_benchmark -- \
  --benchmark_filter='BM_Memcpy/4096' \
  --benchmark_format=json \
  --benchmark_out="$PWD/results/memcpy-4096.json"

# Alternatively, run the built binary directly:
./bazel-bin/benchmarks/memory/memcpy_benchmark \
  --benchmark_filter='BM_Memcpy/4096' \
  --benchmark_format=json \
  --benchmark_out=results/memcpy-4096.json
```

Store the matching experiment metadata (scenario, algorithm, input size,
compiler configuration, CPU information, and source revision) alongside raw
output in `results/`. Use `reports/` for the derived analysis.

## Multi-run experiments and statistics

`tools/run_experiment.py` repeats a selected set of scenarios and computes
descriptive statistics over the repetitions. The full reference for config
keys, CLI flags, isolation modes and the output layout is in
[`tools/README.md`](tools/README.md).

Install the Python dependencies once (numpy and scipy are required by the
statistics layer):

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
```

Run an experiment and open the generated charts:

```bash
.venv/bin/python tools/run_experiment.py scenarious/stability_scenario.json
# or
just experiment scenarious/stability_scenario.json
```

An experiment runs `runs` measured rounds plus an optional number of
discarded `warmup_runs`. Each launch performs `repetitions` in-process Google
Benchmark repetitions, so one benchmark yields `runs * repetitions`
independent observations. `isolation` controls process granularity:

- `batch` — one process per round runs every selected benchmark;
- `per-benchmark` — one process per benchmark in a seeded shuffled order.
  Slower, but removes ordering and drift effects.

Every observation is appended to `measurements.jsonl` immediately. If a run is
interrupted, continue it with `--resume`; `--stats-only` recomputes statistics
from existing measurements without launching anything:

```bash
.venv/bin/python tools/run_experiment.py scenarious/stability_scenario.json --resume
.venv/bin/python tools/run_experiment.py scenarious/stability_scenario.json --stats-only
.venv/bin/python tools/run_experiment.py scenarious/stability_scenario.json --dry-run
```

Results are written to `results/<name>/<UTC timestamp>/`:

- `measurements.jsonl` — one line per observation (raw data);
- `raw/round-NNN.json` — raw Google Benchmark JSON (batch isolation);
- `meta.json` — config, environment changes, system information;
- `stats.json` / `stats.csv` — medians, percentiles, CV, confidence intervals,
  per-round medians, and within/between-round dispersion for both CPU time and
  real time.

`results/<name>/latest` points at the most recent run. Warm-up rounds are kept
in `measurements.jsonl` but excluded from statistics. Outliers are never
dropped silently: with `stats.trim` set to `iqr` or `mad`, removed values are
listed under `discarded`.

Plots are generated from `stats.json`: each series is a median line with a
p25–p75 band, separately for CPU time and real time. Raw single-run JSON files
are still accepted by `plot/plot.py` and keep the original chart names.

## Development workflow

1. Add an algorithm or explicit frame-processing scenario under `lib/`.
2. Add colocated functional tests and run `bazel test`.
3. Add or extend the corresponding executable under `benchmarks/`.
4. Run controlled experiments and record raw measurements plus metadata in
   `results/`.
5. Analyze results in `reports/`.

A scenario describes *what* happens to a frame; algorithms describe *how* an
individual operation is performed. Keep those concerns separate so algorithms
can be compared across independent research cases.
