# Shared library test support

This Bazel package is reserved for reusable, test-only support used by unit
tests across `lib/` components, such as deterministic frame builders, shared
correctness assertions, and test fixtures.

Keep a component's unit test next to its implementation (for example,
`lib/memory/memcpy_test.cpp`). Add a target here only when the same test-only
code is genuinely needed by more than one component. Do not add Google
Benchmark dependencies or production implementation code to this package.
