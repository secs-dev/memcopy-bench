# memcopy-bench

A C/C++ research framework for evaluating data movement and frame-processing
techniques in Telecom L2 scenarios. The initial focus is PDCP and GTP-U frame
processing on x86, while reusable library code avoids unnecessary
architecture-specific assumptions.

## Layout

- `lib/` — reusable frame, memory, transport, and scenario implementations.
  Libraries do not depend on benchmarking or reporting infrastructure.
- `benchmarks/` — Google Benchmark executables. `memory/` holds operation
  microbenchmarks; `scenarios/` will hold complete workflow benchmarks.
- `tests/` — integration and cross-component tests. Unit tests are colocated
  with the library code they verify.
- `tools/` — experiment automation and analysis utilities.
- `results/` — raw benchmark output and experiment metadata (kept separate
  from analysis).
- `reports/` — research findings and comparative analysis.

## Prerequisites

Install [Bazelisk](https://github.com/bazelbuild/bazelisk). It selects the
Bazel version pinned in `.bazelversion`. Commands are run from the repository
root. Optimized mode is enabled by default in `.bazelrc`.

## Build and test

```bash
bazel build //...
bazel test //...
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
bazel run //benchmarks/memory:memcpy_benchmark -- \
  --benchmark_filter='BM_Memcpy/4096' \
  --benchmark_format=json \
  --benchmark_out=results/memcpy-4096.json
```

Store the matching experiment metadata (scenario, algorithm, input size,
compiler configuration, CPU information, and source revision) alongside raw
output in `results/`. Use `reports/` for the derived analysis.

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
