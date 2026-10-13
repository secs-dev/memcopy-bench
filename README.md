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
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 \
  bazel test --config=asan //... --test_output=errors
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
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
