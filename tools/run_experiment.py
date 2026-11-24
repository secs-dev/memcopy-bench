#!/usr/bin/env python3
"""Experiment orchestrator for packet_scenario_benchmark.

Reads an experiment description (JSON), validates it against the benchmark
binary, prepares the environment, runs the benchmark one or more times, and
writes raw measurements plus descriptive statistics and metadata.

Measurement model
-----------------
An experiment is a sequence of rounds. Each round launches the benchmark
``repetitions`` times per benchmark (Google Benchmark repetitions) and each
observation is appended to ``measurements.jsonl`` immediately, so an
interrupted experiment can be resumed with ``--resume``.

``isolation`` controls how rounds are split:

* ``batch`` - one process per round runs every selected benchmark;
* ``per-benchmark`` - one process per benchmark, with the order shuffled
  using a stored seed. Slower, but removes ordering and drift effects.

Statistics live in ``tools/bench_stats.py``; this script only measures.

Environment changes are restored by default in a finally block, so they
survive benchmark failures and Ctrl+C. Pass --keep-environment to leave
them applied across runs.
"""
import argparse
import json
import platform
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import bench_stats

# --- Config ---------------------------------------------------------------

DEFAULT_CONFIG = {
    "name": None,                # required
    "binary": "bazel-bin/benchmarks/scenarious/packet_scenario_benchmark",
    "scenarios": [],             # names as registered in C++
    "header_sizes": [16],
    "payload_sizes": [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384,
                      32768],
    "min_time": "1s",
    "env": {
        "taskset_cpus": [],      # empty = no affinity
        "governor": None,        # None = leave alone
        "no_turbo": None,        # None = leave alone, bool = apply
    },
    "runs": 1,                   # measured rounds (process launches)
    "warmup_runs": 2,            # discarded rounds before the measured ones
    "repetitions": 1,            # in-process Google Benchmark repetitions
    "cooldown_s": 0.0,           # sleep between rounds
    "isolation": "batch",        # batch | per-benchmark
    "interleave": True,          # randomize repetitions inside a process
    "seed": 0,                   # shuffling seed for per-benchmark runs
    "timeout_s": 1800,           # per process launch
    "on_trial_failure": "skip",  # skip | abort
    "stats": {},                 # see tools/bench_stats.DEFAULT_STATS
}

TIME_UNIT_TO_NS = {"ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}

# --- Config loading and validation ----------------------------------------


def load_config(path):
    with open(path, encoding="utf-8") as f:
        user = json.load(f)
    cfg = {**DEFAULT_CONFIG, **user}
    cfg["env"] = {**DEFAULT_CONFIG["env"], **user.get("env", {})}
    cfg["stats"] = {**bench_stats.DEFAULT_STATS, **user.get("stats", {})}
    return cfg


def apply_overrides(cfg, args):
    for key in ("runs", "warmup_runs", "repetitions", "cooldown_s",
                "isolation", "seed", "timeout_s"):
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = value
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
    if cfg["isolation"] not in ("batch", "per-benchmark"):
        errors.append("'isolation' must be 'batch' or 'per-benchmark'")
    if int(cfg["runs"]) < 1:
        errors.append("'runs' must be >= 1")
    if int(cfg["warmup_runs"]) < 0:
        errors.append("'warmup_runs' must be >= 0")
    if int(cfg["repetitions"]) < 1:
        errors.append("'repetitions' must be >= 1")
    if float(cfg["cooldown_s"]) < 0:
        errors.append("'cooldown_s' must be >= 0")
    if float(cfg["timeout_s"]) <= 0:
        errors.append("'timeout_s' must be > 0")
    if cfg["on_trial_failure"] not in ("skip", "abort"):
        errors.append("'on_trial_failure' must be 'skip' or 'abort'")
    if parse_min_time_seconds(cfg["min_time"]) is None and not re.fullmatch(
            r"\s*\d+\s*x\s*", str(cfg["min_time"])):
        errors.append(
            f"'min_time' {cfg['min_time']!r} is not supported by Google "
            "Benchmark; use '<float>s' (e.g. 1s, 0.5s) or '<integer>x'")
    errors.extend(bench_stats.validate_stats_config(
        bench_stats.stats_config(cfg["stats"])))
    return errors

# --- Benchmark name contract ----------------------------------------------


def parse_benchmark_name(name):
    return bench_stats.parse_benchmark_name(name)


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


def enumerate_benchmarks(cfg, available):
    names = []
    for scenario in cfg["scenarios"]:
        for h in cfg["header_sizes"]:
            for p in cfg["payload_sizes"]:
                if (scenario, h, p) in available:
                    names.append(f"BM_PacketScenario/{scenario}/h{h}/p{p}")
    return names


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

    def run(self, cmd, cpus, timeout=None):
        """Execute. Raise CalledProcessError on non-zero exit."""
        subprocess.run(cmd, check=True, timeout=timeout)

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

    def run(self, cmd, cpus, timeout=None):
        if not cpus:
            subprocess.run(cmd, check=True, timeout=timeout)
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

        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
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

# --- Execution plan -------------------------------------------------------


@dataclass
class RoundInfo:
    index: int
    warmup: bool


def build_rounds(cfg):
    return [
        RoundInfo(index=i, warmup=i < int(cfg["warmup_runs"]))
        for i in range(int(cfg["warmup_runs"]) + int(cfg["runs"]))
    ]


def parse_min_time_seconds(text):
    """Seconds for a Google Benchmark ``--benchmark_min_time`` value.

    Google Benchmark accepts only ``<float>s`` (or a bare float, also in
    seconds) and ``<integer>x``. Returns None for iteration-based values or
    anything it cannot interpret.
    """
    text = str(text).strip()
    if text.endswith("x"):
        return None
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*s?", text)
    return float(m.group(1)) if m else None


def estimate_seconds(cfg, rounds, benchmark_count):
    min_time = parse_min_time_seconds(cfg["min_time"])
    if min_time is None:
        return None
    # Every benchmark runs once per round in both isolation modes; only the
    # number of process launches differs.
    return (len(rounds) * benchmark_count * int(cfg["repetitions"]) *
            min_time)


# --- Benchmark invocation -------------------------------------------------

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


def benchmark_args(cfg, out_json, filter_re):
    args = [
        f"--benchmark_filter={filter_re}",
        f"--benchmark_out={Path(out_json).resolve()}",
        "--benchmark_out_format=json",
        f"--benchmark_min_time={cfg['min_time']}",
        f"--benchmark_repetitions={int(cfg['repetitions'])}",
        "--benchmark_time_unit=ns",
    ]
    if int(cfg["repetitions"]) > 1 and cfg.get("interleave", True):
        args.append("--benchmark_enable_random_interleaving=true")
    return args

# --- Measurement log ------------------------------------------------------


def observation_key(rec):
    return (rec.get("round"), rec.get("name"), rec.get("repetition"))


class MeasurementLog:
    """Append-only JSONL log with in-memory deduplication for --resume."""

    def __init__(self, path):
        self.path = Path(path)
        self._keys = set()
        self.count = 0
        if self.path.exists():
            observations, _ = bench_stats.read_measurements(self.path)
            for rec in observations:
                self._keys.add(observation_key(rec))
                self.count += 1

    def has(self, key):
        return key in self._keys

    def append(self, rec):
        key = observation_key(rec)
        if key in self._keys:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
            f.flush()
        self._keys.add(key)
        self.count += 1
        return True


def normalize_context(context):
    keep = ("num_cpus", "mhz_per_cpu", "cpu_scaling_enabled", "load_avg",
            "caches", "library_version")
    return {key: context[key] for key in keep if key in context}


def ingest_gbench_json(path, round_info, log):
    """Convert a Google Benchmark JSON file into observations."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    context = normalize_context(data.get("context") or {})
    added = 0
    for bm in data.get("benchmarks") or []:
        # Aggregates (mean/median/stddev/cv) are computed over repetitions and
        # must not be counted as independent observations.
        if bm.get("run_type") not in (None, "iteration"):
            continue
        name = bm.get("name") or bm.get("run_name")
        if not name:
            continue
        repetition = int(bm.get("repetition_index") or 0)
        if log.has((round_info.index, name, repetition)):
            continue

        parsed = parse_benchmark_name(name)
        rec = {
            "round": round_info.index,
            "warmup": round_info.warmup,
            "name": name,
            "repetition": repetition,
            "scenario": parsed[0] if parsed else None,
            "header": parsed[1] if parsed else None,
            "payload": parsed[2] if parsed else None,
            "bytes": (parsed[1] + parsed[2]) if parsed else None,
            "context": context,
        }

        reason = bm.get("error_message")
        if bm.get("error_occurred") or bm.get("skipped") or reason:
            rec["status"] = "error"
            rec["reason"] = reason or "benchmark reported an error"
        else:
            factor = TIME_UNIT_TO_NS.get(bm.get("time_unit", "ns"), 1.0)
            rec.update({
                "status": "ok",
                "real_time_ns": _scale(bm.get("real_time"), factor),
                "cpu_time_ns": _scale(bm.get("cpu_time"), factor),
                "iterations": bm.get("iterations"),
                "bytes_per_second": bm.get("bytes_per_second"),
            })
        if log.append(rec):
            added += 1
    return added


def _scale(value, factor):
    if value is None:
        return None
    try:
        return float(value) * factor
    except (TypeError, ValueError):
        return None


def record_failure(log, round_info, name, reason):
    log.append({
        "round": round_info.index,
        "warmup": round_info.warmup,
        "name": name,
        "repetition": None,
        "status": "failed",
        "reason": reason,
    })


def slugify(name):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", name).strip("_") or "benchmark"

# --- Round execution ------------------------------------------------------


def launch(adapter, binary, cfg, out_json, filter_re):
    """Run the benchmark once. Returns (ok, reason)."""
    cpus = cfg["env"].get("taskset_cpus") or []
    cmd = adapter.command(binary, benchmark_args(cfg, out_json, filter_re),
                          cpus)
    print("[run]", " ".join(str(part) for part in cmd), flush=True)
    try:
        adapter.run(cmd, cpus, timeout=float(cfg["timeout_s"]))
        return True, None
    except subprocess.TimeoutExpired:
        return False, f"timeout after {cfg['timeout_s']}s"
    except subprocess.CalledProcessError as e:
        return False, f"exit code {e.returncode}"


def handle_failure(cfg, log, round_info, name, reason, failures):
    print(f"[warn] {name or 'launch'} failed: {reason}", file=sys.stderr)
    record_failure(log, round_info, name, reason)
    failures.append({"round": round_info.index, "name": name,
                     "reason": reason})
    if cfg["on_trial_failure"] == "abort":
        raise RuntimeError(f"{name or 'launch'} failed: {reason}")


def run_round_batch(adapter, binary, cfg, filter_re, round_info, raw_dir, log):
    failures = []
    out_json = Path(raw_dir) / f"round-{round_info.index:03d}.json"
    if out_json.exists():
        print(f"[round {round_info.index}] reusing {out_json}")
        ingest_gbench_json(out_json, round_info, log)
        return failures

    ok, reason = launch(adapter, binary, cfg, out_json, filter_re)
    if ok:
        ingest_gbench_json(out_json, round_info, log)
    else:
        handle_failure(cfg, log, round_info, None, reason, failures)
    return failures


def run_round_per_benchmark(adapter, binary, cfg, names, round_info, raw_dir,
                            log):
    failures = []
    rng = random.Random(int(cfg["seed"]) * 1000 + round_info.index)
    order = list(names)
    rng.shuffle(order)
    repetitions = int(cfg["repetitions"])
    round_dir = Path(raw_dir) / f"round-{round_info.index:03d}"
    round_dir.mkdir(parents=True, exist_ok=True)

    for position, name in enumerate(order, 1):
        if all(log.has((round_info.index, name, rep))
               for rep in range(repetitions)):
            continue
        out_json = round_dir / f"{slugify(name)}.json"
        print(f"[round {round_info.index}] {position}/{len(order)} {name}",
              flush=True)
        if out_json.exists():
            ingest_gbench_json(out_json, round_info, log)
            continue
        ok, reason = launch(adapter, binary, cfg, out_json,
                            rf"^{re.escape(name)}$")
        if ok:
            ingest_gbench_json(out_json, round_info, log)
        else:
            handle_failure(cfg, log, round_info, name, reason, failures)
    return failures


def execute_plan(adapter, binary, cfg, names, rounds, filter_re, run_dir, log):
    raw_dir = Path(run_dir) / "raw"
    failures = []
    for position, round_info in enumerate(rounds, 1):
        label = "warmup" if round_info.warmup else "round"
        print(f"[{label} {position}/{len(rounds)}] "
              f"{'discarded' if round_info.warmup else 'measured'}")
        if cfg["isolation"] == "per-benchmark":
            failures.extend(run_round_per_benchmark(
                adapter, binary, cfg, names, round_info, raw_dir, log))
        else:
            failures.extend(run_round_batch(
                adapter, binary, cfg, filter_re, round_info, raw_dir, log))
        if cfg["cooldown_s"] and position < len(rounds):
            time.sleep(float(cfg["cooldown_s"]))
    return failures

# --- Directories and metadata --------------------------------------------


def is_legacy_layout(cfg):
    return (int(cfg["runs"]) == 1 and int(cfg["warmup_runs"]) == 0
            and int(cfg["repetitions"]) == 1
            and cfg["isolation"] == "batch")


def resolve_run_dir(results_dir, cfg, args):
    base = Path(results_dir) / cfg["name"]
    latest = base / "latest"

    explicit = bool(args.run_dir)
    if explicit:
        path = Path(args.run_dir)
    elif args.resume or args.stats_only:
        if not latest.is_file():
            raise ValueError(
                f"--resume/--stats-only requested but {latest} does not exist")
        ref = latest.read_text(encoding="utf-8").strip()
        path = Path(ref) if Path(ref).is_absolute() else base / ref
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = base / stamp
        suffix = 1
        while path.exists():
            path = base / f"{stamp}-{suffix}"
            suffix += 1

    path.mkdir(parents=True, exist_ok=True)
    (path / "raw").mkdir(exist_ok=True)

    if not explicit:
        base.mkdir(parents=True, exist_ok=True)
        latest.write_text(path.name + "\n", encoding="utf-8")
    return path


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


def write_meta(cfg, env_meta, system, run_dir, rounds, names, log,
               failures, out_meta):
    meta = {
        "experiment": cfg,
        "environment": env_meta,
        "system": system,
        "run": {
            "directory": str(run_dir),
            "rounds": len(rounds),
            "warmup_rounds": int(cfg["warmup_runs"]),
            "measured_rounds": int(cfg["runs"]),
            "repetitions": int(cfg["repetitions"]),
            "isolation": cfg["isolation"],
            "seed": int(cfg["seed"]),
            "benchmarks": len(names),
            "measurements": log.count,
            "failures": failures,
        },
    }
    out_meta = Path(out_meta)
    out_meta.parent.mkdir(parents=True, exist_ok=True)
    out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta

# --- Dry run --------------------------------------------------------------


def print_plan(cfg, names, rounds):
    per_round = len(names) if cfg["isolation"] == "per-benchmark" else 1
    estimate = estimate_seconds(cfg, rounds, len(names))
    print("Experiment plan")
    print(f"  name:        {cfg['name']}")
    print(f"  isolation:   {cfg['isolation']}")
    print(f"  benchmarks:  {len(names)}")
    print(f"  rounds:      {len(rounds)} "
          f"({cfg['warmup_runs']} warmup + {cfg['runs']} measured)")
    print(f"  repetitions: {cfg['repetitions']} per launch")
    print(f"  launches:    {len(rounds) * per_round}")
    print(f"  min_time:    {cfg['min_time']} per repetition")
    if estimate is None:
        print("  estimate:    unknown (iteration-based min_time)")
    elif estimate < 60:
        print(f"  estimate:    ~{estimate:.1f} s of benchmark time")
    else:
        print(f"  estimate:    ~{estimate / 60:.1f} min of benchmark time")
    print(f"  env:         governor={cfg['env'].get('governor')} "
          f"no_turbo={cfg['env'].get('no_turbo')} "
          f"cpus={cfg['env'].get('taskset_cpus')}")
    if cfg["isolation"] == "per-benchmark" and len(rounds):
        rng = random.Random(int(cfg["seed"]) * 1000)
        preview = list(names)
        rng.shuffle(preview)
        head = ", ".join(preview[:3])
        print(f"  round 0 order (seeded): {head}"
              f"{', ...' if len(names) > 3 else ''}")
    if cfg["isolation"] == "per-benchmark" and cfg.get("interleave"):
        print("  note: interleave is ignored with per-benchmark isolation")
    print("Dry run: no environment changes, no benchmark launches.")

# --- Main -----------------------------------------------------------------


def build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("config", help="path to experiment JSON")
    p.add_argument("--results-dir", default="results",
                   help="where to write run directories")
    p.add_argument("--reports-dir", default=None,
                   help="optional directory for a copy of the report; the "
                        "canonical report always lives in the run directory")
    p.add_argument("--run-dir", default=None,
                   help="explicit run directory (overrides --resume lookup)")
    p.add_argument("--resume", action="store_true",
                   help="continue the latest run of this experiment")
    p.add_argument("--stats-only", action="store_true",
                   help="recompute statistics from existing measurements")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and exit without side effects")
    p.add_argument("--skip-plot", action="store_true",
                   help="do not invoke plot.py after the benchmark")
    p.add_argument("--skip-report", action="store_true",
                   help="do not assemble the combined HTML report")
    p.add_argument("--keep-environment", action="store_true",
                   help="do not restore governor/turbo/power plan after the "
                        "run (default: restore)")

    p.add_argument("--runs", type=int, help="override measured rounds")
    p.add_argument("--warmup", dest="warmup_runs", type=int,
                   help="override discarded warmup rounds")
    p.add_argument("--repetitions", type=int,
                   help="override in-process repetitions")
    p.add_argument("--cooldown", dest="cooldown_s", type=float,
                   help="override seconds between rounds")
    p.add_argument("--isolation", choices=("batch", "per-benchmark"),
                   help="override isolation mode")
    p.add_argument("--seed", type=int, help="override shuffle seed")
    p.add_argument("--timeout", dest="timeout_s", type=float,
                   help="override per-launch timeout in seconds")
    return p


def main(argv):
    args = build_arg_parser().parse_args(argv)

    cfg = apply_overrides(load_config(args.config), args)
    errors = validate_config(cfg)
    if errors:
        print("Invalid config:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    binary = None
    available = set()
    names = []
    rounds = build_rounds(cfg)
    filter_re = build_filter(cfg)

    # --stats-only must work offline: no binary lookup, no coverage checks.
    if not args.stats_only:
        binary = resolve_binary(cfg["binary"])
        if binary is None:
            print(f"Binary not found: {cfg['binary']}", file=sys.stderr)
            print("Build first: "
                  "bazel build //benchmarks/scenarious:packet_scenario_benchmark",
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
            print("Hint: extend the whitelist in "
                  "packet_scenario_benchmark.cpp and rebuild. On hosts "
                  "without AVX2, ComposedPrefetch is intentionally not "
                  "registered.", file=sys.stderr)
            return 2

        names = enumerate_benchmarks(cfg, available)

    if args.dry_run:
        print_plan(cfg, names, rounds)
        return 0

    try:
        run_dir = resolve_run_dir(args.results_dir, cfg, args)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    log = MeasurementLog(run_dir / "measurements.jsonl")
    meta_path = run_dir / "meta.json"
    stats_path = run_dir / "stats.json"
    csv_path = run_dir / "stats.csv"

    exit_code = 0
    env_meta = {}
    system = {}
    failures = []

    if not args.stats_only:
        if (cfg["isolation"] == "per-benchmark"
                and int(cfg["repetitions"]) > 1 and cfg.get("interleave")):
            print("[note] per-benchmark isolation runs a single benchmark per "
                  "process; intra-process interleaving has no effect.",
                  file=sys.stderr)
        adapter = make_adapter()
        env_meta = adapter.apply(cfg["env"])
        try:
            failures = execute_plan(
                adapter, binary, cfg, names, rounds, filter_re, run_dir, log)
            env_meta["run_status"] = ("ok" if not failures else f"partial: {len(failures)} failures")
        except KeyboardInterrupt:
            print("\nInterrupted; measurements so far are preserved.",
                  file=sys.stderr)
            env_meta["run_status"] = "interrupted"
            exit_code = 130
        except Exception as e:  # noqa: BLE001 - report and keep artifacts
            print(f"Benchmark failed: {e}", file=sys.stderr)
            env_meta["run_status"] = f"failed: {e}"
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
            system = collect_system_info(adapter)
            write_meta(cfg, env_meta, system, run_dir, rounds, names, log,
                       failures, meta_path)
            print(f"[ok] meta: {meta_path}")

    observations, read_warnings = bench_stats.read_measurements(
        run_dir / "measurements.jsonl")
    stats = bench_stats.build_stats(
        observations, cfg,
        extra_meta={"run_dir": str(run_dir), "failures": failures})
    stats["warnings"].extend(read_warnings)
    bench_stats.write_stats_json(stats, stats_path)
    bench_stats.write_stats_csv(stats, csv_path)

    print(f"[ok] raw:   {run_dir / 'measurements.jsonl'}")
    print(f"[ok] stats: {stats_path}")
    print(f"[ok] csv:   {csv_path}")

    successful = stats["totals"]["observations"]
    if successful:
        print(bench_stats.format_summary(stats, metric="cpu"))
    if stats["warnings"]:
        print("Warnings:", file=sys.stderr)
        for warning in stats["warnings"]:
            print(f"  - {warning}", file=sys.stderr)

    if not successful:
        print("No successful measurements collected.", file=sys.stderr)
        return 1

    if is_legacy_layout(cfg):
        legacy_raw = Path(args.results_dir) / f"{cfg['name']}.json"
        raw_round = run_dir / "raw" / "round-000.json"
        if raw_round.exists():
            shutil.copyfile(raw_round, legacy_raw)
        if meta_path.exists():
            shutil.copyfile(
                meta_path,
                Path(args.results_dir) / f"{cfg['name']}.meta.json")
        print(f"[ok] legacy copies: {legacy_raw}")

    if not args.skip_plot:
        plot_script = Path(__file__).parent.parent / "plot/plot.py"
        run_plots_dir = run_dir / "plots"
        subprocess.run(
            [sys.executable, str(plot_script),
             str(stats_path), str(run_plots_dir)],
            check=True,
        )
        if not args.skip_report:
            report_script = Path(__file__).parent / "build_report.py"
            report_path = run_dir / "report.html"
            report_cmd = [
                sys.executable, str(report_script),
                str(stats_path), str(run_plots_dir),
                "--output", str(report_path),
            ]
            if meta_path.exists():
                report_cmd += ["--meta", str(meta_path)]
            subprocess.run(report_cmd, check=True)
            print(f"[ok] report: {report_path}")

            if args.reports_dir:
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_",
                                   str(cfg["name"])).strip("_")
                stable = (Path(args.reports_dir) /
                          f"{safe_name or 'report'}_report.html")
                stable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(report_path, stable)
                print(f"[ok] report copy: {stable}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
