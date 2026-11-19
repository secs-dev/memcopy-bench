#!/usr/bin/env python3
"""Experiment orchestrator for packet_scenario_benchmark.

Reads an experiment description (JSON), validates it against the benchmark
binary, prepares the environment, runs the benchmark, and writes a report
that combines the raw Google Benchmark JSON with environment metadata.

Environment changes (governor, turbo) are restored by default in a finally
block, so they survive benchmark failures and Ctrl+C. Pass --keep-environment
to leave them applied across runs.

The C++ layer performs measurements only. This script never touches the
hot path: it configures the OS, invokes the binary, and stitches files.
"""
import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Config ---------------------------------------------------------------

DEFAULT_CONFIG = {
    "name": None,                # required
    "binary": "bazel-bin/benchmarks/scenarious/packet_scenario_benchmark",
    "scenarios": [],             # names as registered in C++
    "header_sizes": [16],
    "payload_sizes": [64, 512, 1500, 9000],
    "min_time": "1s",
    "env": {
        "taskset_cpus": [],      # empty = no taskset
        "governor": None,        # None = leave alone
        "no_turbo": None,        # None = leave alone, bool = apply
    },
}

TURBO_PATH = Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
CPU_BASE = Path("/sys/devices/system/cpu")


def load_config(path):
    with open(path, encoding="utf-8") as f:
        user = json.load(f)
    cfg = {**DEFAULT_CONFIG, **user}
    cfg["env"] = {**DEFAULT_CONFIG["env"], **user.get("env", {})}
    return cfg


def validate_config(cfg):
    errors = []
    if not cfg.get("name"):
        errors.append("'name' is required")
    if not cfg.get("scenarios"):
        errors.append("'scenarios' must be a non-empty list")
    if not cfg.get("header_sizes"):
        errors.append("'header_sizes' must be a non-empty list")
    if not cfg.get("payload_sizes"):
        errors.append("'payload_sizes' must be a non-empty list")
    return errors

# --- Benchmark binary interaction -----------------------------------------

NAME_RE = re.compile(r"^BM_PacketScenario/([^/]+)/h(\d+)/p(\d+)$")


def parse_benchmark_name(name):
    m = NAME_RE.match(name)
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def list_registered_benchmarks(binary):
    out = subprocess.run(
        [binary, "--benchmark_list_tests"],
        check=True, capture_output=True, text=True,
    ).stdout
    return {
        parsed
        for line in out.splitlines()
        if (parsed := parse_benchmark_name(line.strip()))
    }


def check_requested_coverage(available, cfg):
    missing = []
    for scenario in cfg["scenarios"]:
        for h in cfg["header_sizes"]:
            for p in cfg["payload_sizes"]:
                if (scenario, h, p) not in available:
                    missing.append((scenario, h, p))
    return missing


def build_filter(cfg):
    scen = "|".join(re.escape(s) for s in cfg["scenarios"])
    hs = "|".join(str(h) for h in cfg["header_sizes"])
    ps = "|".join(str(p) for p in cfg["payload_sizes"])
    return rf"^BM_PacketScenario/({scen})/h({hs})/p({ps})$"

# --- Environment preparation with save/restore ----------------------------

def read_governor_state():
    """Return {path: governor} for all cores, or None if unreadable."""
    if not CPU_BASE.exists():
        return None
    state = {}
    try:
        for path in CPU_BASE.glob("cpu[0-9]*/cpufreq/scaling_governor"):
            state[str(path)] = path.read_text().strip()
    except (OSError, PermissionError):
        return None
    return state or None


def restore_governor(state):
    """Write each saved value back. Returns (ok, note)."""
    if not state:
        return False, "no saved state"
    failures = []
    for path, value in state.items():
        try:
            Path(path).write_text(value)
        except (OSError, PermissionError) as e:
            failures.append(f"{path}: {e}")
    if failures:
        return False, "; ".join(failures[:3])
    return True, f"restored {len(state)} cores"


def read_no_turbo_state():
    try:
        return TURBO_PATH.read_text().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def restore_no_turbo(value):
    if value is None:
        return False, "no saved state"
    try:
        TURBO_PATH.write_text(value)
        return True, f"restored no_turbo={value}"
    except (FileNotFoundError, PermissionError, OSError) as e:
        return False, f"failed: {e}"


def try_set_governor(governor):
    try:
        subprocess.run(
            ["cpupower", "frequency-set", "-g", governor],
            check=True, capture_output=True,
        )
        return True, f"set to {governor}"
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return False, f"failed: {e}"


def try_set_no_turbo(disable):
    try:
        TURBO_PATH.write_text("1" if disable else "0")
        return True, f"no_turbo={int(disable)}"
    except (FileNotFoundError, PermissionError, OSError) as e:
        return False, f"failed: {e}"


def summarize_governor(state):
    if not state:
        return None
    values = set(state.values())
    if len(values) == 1:
        return values.pop()
    return dict(state)


def prepare_environment(cfg):
    """Apply requested changes. Returns (env_meta, restore_plan).

    If a setting cannot be saved, it is NOT applied: no silent global
    mutation that we cannot undo.
    """
    env_cfg = cfg["env"]
    applied, skipped = {}, {}
    restore_plan = {}
    original = {}

    if env_cfg.get("governor"):
        state = read_governor_state()
        if state is None:
            skipped["governor"] = "cannot read original state"
        else:
            ok, note = try_set_governor(env_cfg["governor"])
            if ok:
                applied["governor"] = note
                original["governor"] = summarize_governor(state)
                restore_plan["governor"] = state
            else:
                skipped["governor"] = note

    if env_cfg.get("no_turbo") is not None:
        state = read_no_turbo_state()
        if state is None:
            skipped["no_turbo"] = "cannot read original state"
        else:
            ok, note = try_set_no_turbo(env_cfg["no_turbo"])
            if ok:
                applied["no_turbo"] = note
                original["no_turbo"] = state
                restore_plan["no_turbo"] = state
            else:
                skipped["no_turbo"] = note

    if env_cfg.get("taskset_cpus"):
        applied["taskset_cpus"] = env_cfg["taskset_cpus"]

    return (
        {"original": original, "applied": applied, "skipped": skipped},
        restore_plan,
    )


def restore_environment(restore_plan):
    restored, failed = {}, {}
    if "governor" in restore_plan:
        ok, note = restore_governor(restore_plan["governor"])
        (restored if ok else failed)["governor"] = note
    if "no_turbo" in restore_plan:
        ok, note = restore_no_turbo(restore_plan["no_turbo"])
        (restored if ok else failed)["no_turbo"] = note
    return restored, failed

# --- Execution ------------------------------------------------------------

def resolve_binary(spec):
    p = Path(spec)
    if p.exists():
        return p
    found = shutil.which(spec)
    return Path(found) if found else None


def run_benchmark(binary, cfg, filter_re, out_json):
    out_json.parent.mkdir(parents=True, exist_ok=True)
    cmd = []
    cpus = cfg["env"].get("taskset_cpus") or []
    if cpus:
        cmd += ["taskset", "-c", ",".join(str(c) for c in cpus)]
    cmd += [
        str(binary),
        f"--benchmark_filter={filter_re}",
        "--benchmark_format=json",
        f"--benchmark_out={out_json.resolve()}",
        f"--benchmark_min_time={cfg['min_time']}",
    ]
    print("[run]", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)

# --- Metadata -------------------------------------------------------------

def collect_system_info():
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        info["cpu_model"] = next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        )
    except (StopIteration, FileNotFoundError):
        pass
    for key, cmd in (
            ("git_sha", ["git", "rev-parse", "HEAD"]),
            ("bazel_version", ["bazel", "--version"]),
    ):
        try:
            info[key] = subprocess.run(
                cmd, check=True, capture_output=True, text=True,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    return info


def write_meta(cfg, env_meta, out_meta):
    meta = {
        "experiment": cfg,
        "environment": env_meta,
        "system": collect_system_info(),
    }
    out_meta.parent.mkdir(parents=True, exist_ok=True)
    out_meta.write_text(json.dumps(meta, indent=2))

# --- Main -----------------------------------------------------------------

def main(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("config", help="path to experiment JSON")
    p.add_argument("--results-dir", default="results",
                   help="where to write raw and meta JSON")
    p.add_argument("--reports-dir", default="reports",
                   help="where plot.py writes HTML")
    p.add_argument("--skip-plot", action="store_true",
                   help="do not invoke plot.py after benchmark")
    p.add_argument("--keep-environment", action="store_true",
                   help="do not restore governor/turbo after the run "
                        "(default: restore)")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    errors = validate_config(cfg)
    if errors:
        print("Invalid config:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    binary = resolve_binary(cfg["binary"])
    if binary is None:
        print(f"Binary not found: {cfg['binary']}", file=sys.stderr)
        print("Build first: "
              "bazel build //benchmarks/scenarios:packet_scenario_benchmark",
              file=sys.stderr)
        return 2

    available = list_registered_benchmarks(binary)
    missing = check_requested_coverage(available, cfg)
    if missing:
        print(f"{len(missing)} requested (scenario, H, P) not registered:",
              file=sys.stderr)
        for s, h, p in missing[:10]:
            print(f"  - {s} h{h} p{p}", file=sys.stderr)
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more", file=sys.stderr)
        print("Hint: extend the whitelist in packet_scenario_benchmark.cpp "
              "and rebuild. On hosts without AVX2, ComposedPrefetch is "
              "intentionally not registered.", file=sys.stderr)
        return 2

    env_meta, restore_plan = prepare_environment(cfg)

    out_json = Path(args.results_dir) / f"{cfg['name']}.json"
    out_meta = Path(args.results_dir) / f"{cfg['name']}.meta.json"

    exit_code = 0
    try:
        run_benchmark(binary, cfg, build_filter(cfg), out_json)
        env_meta["run_status"] = "ok"
    except subprocess.CalledProcessError as e:
        print(f"Benchmark failed: {e}", file=sys.stderr)
        env_meta["run_status"] = f"failed: exit code {e.returncode}"
        exit_code = 1
    finally:
        if args.keep_environment:
            env_meta["restored"] = {}
            env_meta["failed_restore"] = {}
            print("[env] kept as configured (--keep-environment)",
                  file=sys.stderr)
        else:
            restored, failed = restore_environment(restore_plan)
            env_meta["restored"] = restored
            env_meta["failed_restore"] = failed
            for key, note in restored.items():
                print(f"[env] {key}: {note}", file=sys.stderr)
            for key, note in failed.items():
                print(f"[env] WARNING {key} restore failed: {note}",
                      file=sys.stderr)
        write_meta(cfg, env_meta, out_meta)
        print(f"[ok] meta: {out_meta}")

    if exit_code != 0:
        return exit_code

    print(f"[ok] raw:  {out_json}")

    if not args.skip_plot:
        plot_script = Path(__file__).parent.parent / "plot/plot.py"
        subprocess.run(
            [sys.executable, str(plot_script),
             str(out_json), args.reports_dir],
            check=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))