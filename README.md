# memcopy-bench
DevTools 2026 Research Project. Violet Team

# Setup

We use [Bazel](https://bazel.build/install) to build, run and test our project.
The Bazel version is pinned in `src/.bazelversion`, use
[Bazelisk](https://github.com/bazelbuild/bazelisk) to get the right one.

All commands below are run from `src/`:
```bash
cd src/
```

Optimized build mode (`-c opt`) is enabled by default in `src/.bazelrc`.

**Build:**
```bash
bazel build //lib:memcpy_bench # build benchmark lib only
bazel build //main:main # build main binary and library
```

**Test:**
```bash
bazel test //test:memcpy_bench_test
```

**Run benchmarks:**
```bash
bazel run //main:main

# Google Benchmark flags are passed after `--`
bazel run //main:main -- --benchmark_filter='BM_Memcpy/4096' --benchmark_format=json
```
