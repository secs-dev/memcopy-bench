#!/usr/bin/env python3
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

from scipy import stats as sps

REPO_ROOT = "/home/modernovo/labs/memcp-bench"
TARGET_HDR = os.path.join(REPO_ROOT, "lib", "scenarious", "candidate_scenario.hpp")
BENCH_BIN = os.path.join(
    REPO_ROOT, "bazel-bin", "benchmarks", "scenarious", "packet_scenario_benchmark"
)
FILTER = r"^BM_PacketScenario/(Memcpy|Candidate)/h(8|16)/p(64|128|256|512|1024|4096|32768|65536)$"


def fail(reason: str, checks=None):
    out = {
        "status": "complete",
        "metrics": {},
        "checks": checks or {"tests": "fail"},
        "diagnostics": {"error": reason},
    }
    print(json.dumps(out))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: evaluate_hybrid.py <candidate_dir>", file=sys.stderr)
        return 2

    candidate_dir = sys.argv[1]
    cand_hdr = os.path.join(candidate_dir, "candidate.hpp")
    if not os.path.isfile(cand_hdr):
        return fail(f"Missing candidate.hpp in {candidate_dir}")

    shutil.copyfile(cand_hdr, TARGET_HDR)

    test_proc = subprocess.run(
        ["bazel", "test", "//lib/scenarious:scenarios_test", "--test_output=errors"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )
    if test_proc.returncode != 0:
        print(test_proc.stdout, file=sys.stderr)
        return fail("scenarios_test failed", checks={"tests": "fail"})

    build_proc = subprocess.run(
        ["bazel", "build", "//benchmarks/scenarious:packet_scenario_benchmark"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )
    if build_proc.returncode != 0:
        print(build_proc.stdout, file=sys.stderr)
        return fail("benchmark build failed", checks={"tests": "pass"})

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        bench_json = tf.name

    try:
        cmd = [
            "taskset",
            "-c",
            "2",
            BENCH_BIN,
            f"--benchmark_filter={FILTER}",
            "--benchmark_repetitions=15",
            "--benchmark_min_time=0.02s",
            "--benchmark_enable_random_interleaving=true",
            "--benchmark_format=json",
            f"--benchmark_out={bench_json}",
        ]
        bench_proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
        if bench_proc.returncode != 0:
            print(bench_proc.stderr, file=sys.stderr)
            return fail("benchmark execution failed", checks={"tests": "pass"})

        with open(bench_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    finally:
        if os.path.exists(bench_json):
            os.remove(bench_json)

    samples = {}
    for entry in data.get("benchmarks", []):
        if entry.get("run_type") != "iteration":
            continue
        name = entry.get("name", "")
        parts = name.split("/")
        if len(parts) < 4:
            continue
        _, scen, hdr, pay = parts[0], parts[1], parts[2], parts[3]
        key = (hdr, int(pay[1:]))
        samples.setdefault(key, {}).setdefault(scen, []).append(float(entry["cpu_time"]))

    ratios = []
    sig_wins = 0
    per_case = {}
    for (hdr, pay) in sorted(samples.keys()):
        d = samples[(hdr, pay)]
        m_vals = d.get("Memcpy", [])
        c_vals = d.get("Candidate", [])
        if not m_vals or not c_vals:
            continue
        m_med = statistics.median(m_vals)
        c_med = statistics.median(c_vals)
        ratio = c_med / m_med if m_med > 0 else 1.0
        _, p_mwu = sps.mannwhitneyu(c_vals, m_vals, alternative="two-sided")
        ratios.append(ratio)
        if ratio <= 0.96 and p_mwu < 0.05:
            sig_wins += 1
        per_case[f"{hdr}/p{pay}"] = {
            "memcpy_ns": round(m_med, 2),
            "cand_ns": round(c_med, 2),
            "ratio": round(ratio, 4),
            "p_mwu": round(float(p_mwu), 5),
        }

    if not ratios:
        return fail("No benchmark samples collected", checks={"tests": "pass"})

    geomean = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
    out = {
        "status": "complete",
        "metrics": {
            "geomean_ratio": round(geomean, 4),
            "sig_wins": sig_wins,
        },
        "checks": {"tests": "pass"},
        "diagnostics": {
            "best_ratio": round(min(ratios), 4),
            "worst_ratio": round(max(ratios), 4),
            "cases": per_case,
        },
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
