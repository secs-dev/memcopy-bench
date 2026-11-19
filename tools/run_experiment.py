#!/usr/bin/env python3
"""Experiment orchestrator for packet_scenario_benchmark.

Reads an experiment description (JSON), validates it against the benchmark
binary, prepares the environment, runs the benchmark, and writes a report
that combines the raw Google Benchmark JSON with environment metadata.

Environment changes are restored by default in a finally block, so they
survive benchmark failures and Ctrl+C. Pass --keep-environment to leave
them applied across runs.

Platform-specific work lives in EnvironmentAdapter subclasses. Linux,
macOS, and Windows are supported; unsupported settings are recorded under
"skipped" rather than silently ignored.

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
        "taskset_cpus": [],      # empty = no affinity
        "governor": None,        # None = leave alone
        "no_turbo": None,        # None = leave alone, bool = apply
    },
}

# --- Config loading and validation ----------------------------------------

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

# --- Benchmark name contract ----------------------------------------------

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

# --- Environment adapter base ---------------------------------------------

class EnvironmentAdapter:
    """Base adapter. Every platform starts from "skip with a reason"."""
    name = "generic"

    def __init__(self):
        # Restore state captured during apply(). Opaque to the caller.
        self._restore_plan = {}

    def apply(self, env_cfg):
        """Returns {'original': ..., 'applied': ..., 'skipped': ...}."""
        applied, skipped = {}, {}
        if env_cfg.get("governor"):
            skipped["governor"] = f"not supported on {self.name}"
        if env_cfg.get("no_turbo") is not None:
            skipped["no_turbo"] = f"not supported on {self.name}"
        if env_cfg.get("taskset_cpus"):
            skipped["taskset_cpus"] = f"not supported on {self.name}"
        return {"original": {}, "applied": applied, "skipped": skipped}

    def restore(self):
        """Returns (restored, failed) dicts. Called from main's finally."""
        return {}, {}

    def command(self, binary, args, cpus):
        """Return the command line to print and execute."""
        return [str(binary), *args]

    def run(self, cmd, cpus):
        """Execute. Raise CalledProcessError on non-zero exit."""
        subprocess.run(cmd, check=True)

    def system_info(self):
        return {}

# --- Linux ----------------------------------------------------------------

class LinuxEnvironment(EnvironmentAdapter):
    name = "linux"
    TURBO_PATH = Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
    CPU_BASE = Path("/sys/devices/system/cpu")

    def _read_governor_state(self):
        if not self.CPU_BASE.exists():
            return None
        state = {}
        try:
            for path in self.CPU_BASE.glob("cpu[0-9]*/cpufreq/scaling_governor"):
                state[str(path)] = path.read_text().strip()
        except (OSError, PermissionError):
            return None
        return state or None

    def _restore_governor(self, state):
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

    def _read_no_turbo_state(self):
        try:
            return self.TURBO_PATH.read_text().strip()
        except (FileNotFoundError, PermissionError, OSError):
            return None

    def _restore_no_turbo(self, value):
        if value is None:
            return False, "no saved state"
        try:
            self.TURBO_PATH.write_text(value)
            return True, f"restored no_turbo={value}"
        except (FileNotFoundError, PermissionError, OSError) as e:
            return False, f"failed: {e}"

    def _set_governor(self, governor):
        try:
            subprocess.run(
                ["cpupower", "frequency-set", "-g", governor],
                check=True, capture_output=True,
            )
            return True, f"set to {governor}"
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            return False, f"failed: {e}"

    def _set_no_turbo(self, disable):
        try:
            self.TURBO_PATH.write_text("1" if disable else "0")
            return True, f"no_turbo={int(disable)}"
        except (FileNotFoundError, PermissionError, OSError) as e:
            return False, f"failed: {e}"

    @staticmethod
    def _summarize_governor(state):
        if not state:
            return None
        values = set(state.values())
        return values.pop() if len(values) == 1 else dict(state)

    def apply(self, env_cfg):
        applied, skipped, original = {}, {}, {}

        if env_cfg.get("governor"):
            state = self._read_governor_state()
            if state is None:
                skipped["governor"] = "cannot read original state"
            else:
                ok, note = self._set_governor(env_cfg["governor"])
                if ok:
                    applied["governor"] = note
                    original["governor"] = self._summarize_governor(state)
                    self._restore_plan["governor"] = state
                else:
                    skipped["governor"] = note

        if env_cfg.get("no_turbo") is not None:
            state = self._read_no_turbo_state()
            if state is None:
                skipped["no_turbo"] = "cannot read original state"
            else:
                ok, note = self._set_no_turbo(env_cfg["no_turbo"])
                if ok:
                    applied["no_turbo"] = note
                    original["no_turbo"] = state
                    self._restore_plan["no_turbo"] = state
                else:
                    skipped["no_turbo"] = note

        if env_cfg.get("taskset_cpus"):
            applied["taskset_cpus"] = env_cfg["taskset_cpus"]

        return {"original": original, "applied": applied, "skipped": skipped}

    def restore(self):
        restored, failed = {}, {}
        if "governor" in self._restore_plan:
            ok, note = self._restore_governor(self._restore_plan["governor"])
            (restored if ok else failed)["governor"] = note
        if "no_turbo" in self._restore_plan:
            ok, note = self._restore_no_turbo(self._restore_plan["no_turbo"])
            (restored if ok else failed)["no_turbo"] = note
        return restored, failed

    def command(self, binary, args, cpus):
        cmd = [str(binary), *args]
        if cpus:
            cmd = ["taskset", "-c", ",".join(str(c) for c in cpus), *cmd]
        return cmd

    def system_info(self):
        info = {}
        try:
            info["cpu_model"] = next(
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            )
        except (StopIteration, FileNotFoundError):
            pass
        return info

# --- macOS ----------------------------------------------------------------

class MacOSEnvironment(EnvironmentAdapter):
    name = "macos"

    def apply(self, env_cfg):
        applied, skipped = {}, {}
        if env_cfg.get("governor"):
            skipped["governor"] = "macos does not expose a cpu governor"
        if env_cfg.get("no_turbo") is not None:
            skipped["no_turbo"] = "macos does not expose turbo control"
        if env_cfg.get("taskset_cpus"):
            skipped["taskset_cpus"] = "macos does not support cpu affinity"
        return {"original": {}, "applied": applied, "skipped": skipped}

    def system_info(self):
        info = {}
        try:
            value = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            if value:
                info["cpu_model"] = value
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        return info

# --- Windows --------------------------------------------------------------

class WindowsEnvironment(EnvironmentAdapter):
    name = "windows"

    # Windows power plan presets. Users may also pass a raw GUID as "governor".
    POWER_PLANS = {
        "high_performance": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
        "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
        "power_saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
    }

    def _read_active_plan(self):
        try:
            out = subprocess.run(
                ["powercfg", "/getactivescheme"],
                check=True, capture_output=True, text=True,
            ).stdout
            m = re.search(
                r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
                out,
            )
            return m.group(1) if m else None
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

    def _set_active_plan(self, guid):
        try:
            subprocess.run(
                ["powercfg", "/setactive", guid],
                check=True, capture_output=True,
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def apply(self, env_cfg):
        applied, skipped, original = {}, {}, {}

        governor = env_cfg.get("governor")
        if governor:
            guid = self.POWER_PLANS.get(governor, governor)
            prev = self._read_active_plan()
            if prev is None:
                skipped["governor"] = "cannot read active power plan"
            elif self._set_active_plan(guid):
                applied["governor"] = f"power plan -> {governor}"
                original["governor"] = prev
                self._restore_plan["power_plan"] = prev
            else:
                skipped["governor"] = "powercfg /setactive failed"

        if env_cfg.get("no_turbo") is not None:
            skipped["no_turbo"] = (
                "not supported on windows (use powercfg manually)"
            )

        if env_cfg.get("taskset_cpus"):
            applied["taskset_cpus"] = env_cfg["taskset_cpus"]

        return {"original": original, "applied": applied, "skipped": skipped}

    def restore(self):
        restored, failed = {}, {}
        prev = self._restore_plan.get("power_plan")
        if prev:
            if self._set_active_plan(prev):
                restored["governor"] = f"restored power plan {prev}"
            else:
                failed["governor"] = "powercfg restore failed"
        return restored, failed

    def command(self, binary, args, cpus):
        # Affinity is applied post-spawn in run(), so the command line is plain.
        return [str(binary), *args]

    def run(self, cmd, cpus):
        if not cpus:
            subprocess.run(cmd, check=True)
            return

        import ctypes  # windll is only available on Windows
        proc = subprocess.Popen(cmd)

        mask = 0
        for cpu in cpus:
            mask |= 1 << cpu

        kernel32 = ctypes.windll.kernel32
        PROCESS_SET_INFORMATION = 0x0200
        PROCESS_QUERY_INFORMATION = 0x0400
        handle = kernel32.OpenProcess(
            PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION,
            False, proc.pid,
            )
        if handle:
            try:
                kernel32.SetProcessAffinityMask(handle, mask)
            finally:
                kernel32.CloseHandle(handle)

        rc = proc.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)

    def system_info(self):
        info = {}
        try:
            value = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor).Name"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            if value:
                info["cpu_model"] = value
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        return info

# --- Adapter factory ------------------------------------------------------

def make_adapter():
    system = platform.system()
    if system == "Linux":
        return LinuxEnvironment()
    if system == "Darwin":
        return MacOSEnvironment()
    if system == "Windows":
        return WindowsEnvironment()
    return EnvironmentAdapter()

# --- Execution ------------------------------------------------------------

def resolve_binary(spec):
    candidates = [Path(spec)]
    if platform.system() == "Windows" and not spec.lower().endswith(".exe"):
        candidates.append(Path(spec + ".exe"))
    for p in candidates:
        if p.exists():
            return p
    found = shutil.which(spec)
    if found is None and platform.system() == "Windows":
        found = shutil.which(spec + ".exe")
    return Path(found) if found else None


def run_benchmark(adapter, binary, cfg, filter_re, out_json):
    out_json.parent.mkdir(parents=True, exist_ok=True)
    cpus = cfg["env"].get("taskset_cpus") or []
    args = [
        f"--benchmark_filter={filter_re}",
        "--benchmark_format=json",
        f"--benchmark_out={out_json.resolve()}",
        f"--benchmark_min_time={cfg['min_time']}",
    ]
    cmd = adapter.command(binary, args, cpus)
    print("[run]", " ".join(cmd), flush=True)
    adapter.run(cmd, cpus)

# --- Metadata -------------------------------------------------------------

def collect_system_info(adapter):
    info = {
        "adapter": adapter.name,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    info.update(adapter.system_info())

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


def write_meta(adapter, cfg, env_meta, out_meta):
    meta = {
        "experiment": cfg,
        "environment": env_meta,
        "system": collect_system_info(adapter),
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
                   help="do not restore governor/turbo/power plan after the "
                        "run (default: restore)")
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

    adapter = make_adapter()
    env_meta = adapter.apply(cfg["env"])

    out_json = Path(args.results_dir) / f"{cfg['name']}.json"
    out_meta = Path(args.results_dir) / f"{cfg['name']}.meta.json"

    exit_code = 0
    try:
        run_benchmark(adapter, binary, cfg, build_filter(cfg), out_json)
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
            restored, failed = adapter.restore()
            env_meta["restored"] = restored
            env_meta["failed_restore"] = failed
            for key, note in restored.items():
                print(f"[env] {key}: {note}", file=sys.stderr)
            for key, note in failed.items():
                print(f"[env] WARNING {key} restore failed: {note}",
                      file=sys.stderr)
        write_meta(adapter, cfg, env_meta, out_meta)
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