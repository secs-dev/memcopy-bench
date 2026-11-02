# Set default recipe to show available commands
default:
    @just --list

# Build all targets in the workspace
build:
    bazel build //...

# Run all unit tests with error logs on failure
test:
    bazel test //... --test_output=errors

# Run AddressSanitizer and UndefinedBehaviorSanitizer checks
sanitizers:
    bazel test --config=asan //... --test_output=errors
    bazel test --config=ubsan //... --test_output=errors

# Run C++ linter (clang-tidy)
lint:
    sh tools/run-clang-tidy.sh

# Run complete local verification (format, lint, tests, and sanitizers)
check: lint test sanitizers

# Run benchmarks (optional filter argument: `just bench BM_Memcpy`)
bench filter="":
    bazel run //benchmarks/memory:memcpy_benchmark -- {{ if filter != "" { "--benchmark_filter=" + filter } else { "" } }}

# Refresh compile_commands.json for clangd and IDE completion
compile-commands:
    bazel run @hedron_compile_commands//:refresh_all

# Remove local build outputs and symlinks
clean:
    bazel clean

# Wipe the entire local Bazel cache and reset external downloads
clean-all:
    bazel clean --expunge