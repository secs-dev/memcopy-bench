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

# Run benchmarks (optional filter argument: `just bench BM_Memcpy` or `just bench "" results/my_bench.json`)
bench filter="" out="":
    bazel run //benchmarks/memory:memcpy_benchmark -- \
        {{ if filter != "" { "--benchmark_filter=" + filter } else { "" } }} \
        {{ if out != "" { "--benchmark_format=json --benchmark_out=$PWD/" + out } else { "" } }}

# Generate interactive Plotly charts from benchmark results
plot input="results/all_methods_benchmark.json" output="reports":
    @if [ -f "plot/.venv/bin/python3" ]; then \
        plot/.venv/bin/python3 plot/plot.py {{ input }} {{ output }}; \
    else \
        python3 plot/plot.py {{ input }} {{ output }}; \
    fi

# Run benchmarks and immediately build plots
bench-plot filter="" out="results/all_methods_benchmark.json" reports="reports":
    just bench "{{ filter }}" "{{ out }}"
    just plot "{{ out }}" "{{ reports }}"

# Run a multi-run experiment described by a JSON config
experiment config="scenarious/example_scenario.json" results="results" reports="reports":
    @if [ -x ".venv/bin/python" ]; then \
        .venv/bin/python tools/run_experiment.py {{ config }} \
            --results-dir {{ results }} --reports-dir {{ reports }}; \
    else \
        python3 tools/run_experiment.py {{ config }} \
            --results-dir {{ results }} --reports-dir {{ reports }}; \
    fi

# Recompute statistics for the latest run of an experiment
stats config="scenarious/example_scenario.json" results="results":
    @if [ -x ".venv/bin/python" ]; then \
        .venv/bin/python tools/run_experiment.py {{ config }} \
            --results-dir {{ results }} --stats-only --skip-plot; \
    else \
        python3 tools/run_experiment.py {{ config }} \
            --results-dir {{ results }} --stats-only --skip-plot; \
    fi

# Unit tests for the statistics layer
stats-test:
    @if [ -x ".venv/bin/python" ]; then \
        .venv/bin/python -m unittest discover -s tools/tests -v; \
    else \
        python3 -m unittest discover -s tools/tests -v; \
    fi

# Refresh compile_commands.json for clangd and IDE completion
compile-commands:
    bazel run @hedron_compile_commands//:refresh_all

# Remove local build outputs and symlinks
clean:
    bazel clean

# Wipe the entire local Bazel cache and reset external downloads
clean-all:
    bazel clean --expunge