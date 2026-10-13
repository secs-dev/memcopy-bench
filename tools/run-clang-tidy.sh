#!/usr/bin/env sh
set -eu

if ! command -v clang-tidy >/dev/null 2>&1; then
  echo "clang-tidy is required but was not found on PATH." >&2
  exit 1
fi

bazel run @hedron_compile_commands//:refresh_all

find lib benchmarks -type f \( -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \) \
  -exec clang-tidy --warnings-as-errors='*' -p . {} +
