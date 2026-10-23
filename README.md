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

## Development environment

Nix support is optional. The Bazel project and its configuration do not depend
on Nix, so use whichever setup suits your platform and workflow. Commands are
run from the repository root. Optimized mode is enabled by default in
`.bazelrc`.

### With Nix

Install [Nix](https://nixos.org/download/) with flakes enabled, then enter the
pinned development shell:

```bash
nix develop
bazel build //...
bazel test //...
```

The shell supports `x86_64-linux`, `x86_64-darwin` (Intel macOS), and
`aarch64-darwin` (Apple Silicon). It provides Bazelisk, Clang/LLVM,
`clangd`, `clang-format`, `clang-tidy`, Buildifier, and common build tools.
The `bazel` command in this shell is a wrapper around Bazelisk that passes
`--repo_env=CC=clang --repo_env=CXX=clang++`. This makes Bazel's local C++
toolchain configuration select the Nix-provided Clang; exported `CC` and
`CXX` alone are not sufficient to guarantee that Bazel does so.

To enter the shell automatically when changing into the repository, optionally
use [direnv](https://direnv.net/). After enabling direnv in your shell, create
a local `.envrc` containing `use flake` and run `direnv allow`. `.envrc` is a
local convenience file and should not be committed unless the project chooses
to standardize it.

### Without Nix

Install [Bazelisk](https://github.com/bazelbuild/bazelisk), which selects the
Bazel version pinned in `.bazelversion`. Also install a C++17-capable compiler
(such as Clang or GCC); install Clang/LLVM tools including `clangd`,
`clang-format`, and `clang-tidy` for editor tooling and linting; and install
[Buildifier](https://github.com/bazelbuild/buildtools) when formatting Bazel
files. Then use the same commands:

```bash
bazel build //...
bazel test //...
```

Nix improves the developer experience without replacing this path: pinned tool
versions provide reproducible development environments, easier onboarding, and
controlled toolchain updates. It does not make results identical everywhere:
platform-specific toolchains, CPU architectures, and benchmark measurements
may still differ between machines.

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
