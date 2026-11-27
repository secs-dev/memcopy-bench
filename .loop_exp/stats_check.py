#!/usr/bin/env python3
import json
import sys
from collections import defaultdict
import numpy as np
from scipy import stats

rng = np.random.default_rng(42)


def analyze(label, samples_dict):
    print(f"\n=== {label} ===")
    rows = []
    for (h, p), d in sorted(samples_dict.items(), key=lambda x: (x[0][0], x[0][1])):
        m = np.array(d["Memcpy"], dtype=float)
        c = np.array(d["Candidate"], dtype=float)
        if len(m) < 3 or len(c) < 3:
            continue
        mm, cm = np.median(m), np.median(c)
        ratio = cm / mm
        bm = np.median(rng.choice(m, size=(5000, len(m)), replace=True), axis=1)
        bc = np.median(rng.choice(c, size=(5000, len(c)), replace=True), axis=1)
        br = bc / bm
        ci_lo, ci_hi = np.percentile(br, [2.5, 97.5])
        _, p_mwu = stats.mannwhitneyu(c, m, alternative="two-sided")
        _, p_welch = stats.ttest_ind(c, m, equal_var=False)
        rows.append((h, p, len(m), mm, cm, ratio, ci_lo, ci_hi, p_mwu, p_welch))

    m_tests = len(rows)
    order = np.argsort([r[8] for r in rows])
    adj = np.empty(m_tests)
    running_max = 0.0
    for rank, idx in enumerate(order):
        val = rows[idx][8] * (m_tests - rank)
        running_max = max(running_max, val)
        adj[idx] = min(1.0, running_max)

    print(
        f"{'hdr':3s} {'payload':>8s} {'n':>2s} | {'Memcpy':>10s} | {'Cand':>10s} | {'ratio [95% boot CI]':>21s} | {'p_MWU':>7s} {'p_Holm':>7s} | sig?"
    )
    print("-" * 92)
    for i, (h, p, n, mm, cm, r, lo, hi, pm, pw) in enumerate(rows):
        if r < 0.97 or pm < 0.05 or adj[i] < 0.05:
            if adj[i] < 0.05:
                sig = "YES (p_Holm<0.05)"
            elif pm < 0.05:
                sig = "marginal (raw p<0.05)"
            else:
                sig = "NO (noise)"
            print(
                f"{h:3s} {p:8d} {n:2d} | {mm:10.1f} | {cm:10.1f} | {r:5.3f}x [{lo:5.3f},{hi:5.3f}] | {pm:7.4f} {adj[i]:7.4f} | {sig}"
            )


def load_gb_json(path):
    s = defaultdict(lambda: defaultdict(list))
    with open(path) as f:
        for b in json.load(f)["benchmarks"]:
            if b.get("run_type") != "iteration":
                continue
            _, scen, hdr, pay = b["name"].split("/")
            s[(hdr, int(pay[1:]))][scen].append(float(b["cpu_time"]))
    return s


def load_jsonl(path):
    s = defaultdict(lambda: defaultdict(list))
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("warmup"):
                continue
            hdr = "h" + str(rec["header"])
            s[(hdr, int(rec["payload"]))][rec["scenario"]].append(
                float(rec["cpu_time_ns"])
            )
    return s


if __name__ == "__main__":
    analyze(
        "Full-range winner (run2/candidate-002) - run_experiment.py (n=9)",
        load_jsonl("results/candidate-vs-memcpy/20261126T085505Z/measurements.jsonl"),
    )
    analyze(
        "Full-range winner (run2/candidate-002) - confirm_full.json (n=10)",
        load_gb_json(".loop_exp/confirm_full.json"),
    )
    if len(sys.argv) > 1 and sys.argv[1] == "--with-small":
        analyze(
            "Small <2KB winner (run_small/candidate-004) - confirm_small_20.json (n=20)",
            load_gb_json(".loop_exp/confirm_small_20.json"),
        )
