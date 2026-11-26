#!/usr/bin/env python3
"""Descriptive statistics for multi-run benchmark experiments.

The orchestrator (``run_experiment.py``) writes one JSON object per
``(round, benchmark, repetition)`` into ``measurements.jsonl``. This module
turns those observations into ``stats.json`` and ``stats.csv``.

It knows nothing about platforms, subprocesses, or environment tuning, so it
can be tested with synthetic observations.

Design notes
------------
* Statistics are computed over the time metric that Google Benchmark
  reports per repetition. Throughput is derived by inverting the time
  summary (``bytes / time``): percentiles, min/max and the median map
  exactly (``p05_tp == bytes / p95_time``). Mean/std/cv/mad use the
  delta-method approximation for a reciprocal and are advisory only.
* Warm-up rounds stay in ``measurements.jsonl`` but never enter statistics.
* Outliers are never dropped silently: whatever ``trim`` removes appears
  under ``discarded`` with a reason.
* ``sigma_within`` (pooled within-round variance) and ``sigma_between``
  (dispersion of per-round medians) separate measurement noise from
  machine drift, which is the point of the runs x repetitions design.
"""
from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats as sps

SCHEMA = "memcopy-bench/stats/v1"

DEFAULT_STATS = {
    "percentiles": [5, 25, 50, 75, 95],
    "trim": "none",              # none | iqr | mad
    "ci_level": 0.95,
    "min_meaningful_n": 10,
    "load_avg_threshold": 0.25,  # fraction of num_cpus
}

# Logical metric name -> field in an observation.
METRICS = {"cpu": "cpu_time_ns", "real": "real_time_ns"}

NAME_RE = re.compile(r"^BM_PacketScenario/([^/]+)/h(\d+)/p(\d+)$")


# --- Configuration --------------------------------------------------------


def stats_config(user=None):
    cfg = dict(DEFAULT_STATS)
    cfg.update(user or {})
    cfg["percentiles"] = sorted(float(p) for p in cfg["percentiles"])
    return cfg


def validate_stats_config(cfg):
    errors = []
    for p in cfg["percentiles"]:
        if not 0.0 <= p <= 100.0:
            errors.append(f"stats.percentiles: {p} outside [0, 100]")
    if cfg["trim"] not in ("none", "iqr", "mad"):
        errors.append(
            f"stats.trim: {cfg['trim']!r} (expected none, iqr, or mad)")
    if not 0.0 < float(cfg["ci_level"]) < 1.0:
        errors.append("stats.ci_level must be between 0 and 1")
    return errors


# --- Observations ---------------------------------------------------------


def read_measurements(path):
    """Read measurements.jsonl, skipping blank/corrupt lines.

    Returns ``(observations, warnings)``. Corrupt lines are reported rather
    than raised so a partially written file still yields an analysis.
    """
    path = Path(path)
    observations, warnings = [], []
    if not path.exists():
        return observations, [f"no measurements at {path}"]
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                warnings.append(f"{path}:{lineno}: malformed JSON line")
                continue
            if not isinstance(rec, dict):
                warnings.append(f"{path}:{lineno}: not an object")
                continue
            observations.append(rec)
    return observations, warnings


def parse_benchmark_name(name):
    m = NAME_RE.match(name or "")
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def _clean(values):
    out = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def _percentile_key(q):
    if float(q).is_integer():
        return f"p{int(q):02d}"
    return f"p{q:g}"


# --- Single-sample statistics --------------------------------------------


def percentile(values, qs):
    """Percentiles with linear interpolation, keyed as ``p05``/``p2.5``."""
    cleaned = _clean(values)
    if not cleaned:
        return {_percentile_key(q): None for q in qs}
    computed = np.percentile(cleaned, qs, method="linear")
    return {_percentile_key(q): float(v) for q, v in zip(qs, computed)}


def summarize(values, qs=None, ci_level=DEFAULT_STATS["ci_level"]):
    """Descriptive statistics for one sample, or None when it is empty."""
    qs = DEFAULT_STATS["percentiles"] if qs is None else qs
    cleaned = _clean(values)
    n = len(cleaned)
    if n == 0:
        return None

    summary = {
        "n": n,
        "mean": float(np.mean(cleaned)),
        "median": float(np.median(cleaned)),
        "std": float(np.std(cleaned, ddof=1)) if n > 1 else 0.0,
        "mad": float(sps.median_abs_deviation(cleaned)),
        "min": float(np.min(cleaned)),
        "max": float(np.max(cleaned)),
        "ci95_mean": None,
    }
    mean = summary["mean"]
    summary["cv"] = summary["std"] / mean if mean != 0 else None

    sem = summary["std"] / math.sqrt(n)
    if n >= 3 and sem > 0:
        low, high = sps.t.interval(
            ci_level, n - 1, loc=mean, scale=sem)
        summary["ci95_mean"] = [float(low), float(high)]
    elif n >= 3:
        summary["ci95_mean"] = [mean, mean]

    summary.update(percentile(cleaned, qs))
    return summary


def apply_trim(values, mode):
    """Return ``(kept, dropped)`` where dropped items are ``(value, reason)``."""
    cleaned = _clean(values)
    if mode == "none" or len(cleaned) < 4:
        return cleaned, []

    if mode == "iqr":
        q1, q3 = np.percentile(cleaned, [25, 75])
        iqr = q3 - q1
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        reason = "iqr_outlier"
    else:  # mad
        median = float(np.median(cleaned))
        mad = float(sps.median_abs_deviation(cleaned)) * 1.4826
        low, high = median - 3.0 * mad, median + 3.0 * mad
        reason = "mad_outlier"

    kept = [v for v in cleaned if low <= v <= high]
    dropped = [(v, reason) for v in cleaned if not low <= v <= high]
    # Never trim a sample down to nothing: the caller loses all signal.
    if not kept:
        return cleaned, []
    return kept, dropped


def _variance_decomposition(by_round):
    within = [
        float(np.var(values, ddof=1))
        for values in by_round.values()
        if len(values) >= 2
    ]
    sigma_within = math.sqrt(sum(within) / len(within)) if within else 0.0
    medians = [float(np.median(values)) for values in by_round.values()]
    sigma_between = (
        float(np.std(medians, ddof=1)) if len(medians) >= 2 else 0.0)
    return sigma_within, sigma_between


# --- Experiment-level aggregation ----------------------------------------


def _group_observations(observations):
    groups = defaultdict(list)
    gaps = []
    warmup_count = 0
    for rec in observations:
        name = rec.get("name")
        if not name or rec.get("status", "ok") != "ok":
            gaps.append({
                "round": rec.get("round"),
                "name": name,
                "reason": rec.get("reason") or rec.get("status"),
            })
            continue
        if rec.get("warmup"):
            warmup_count += 1
            continue
        groups[name].append(rec)
    return groups, gaps, warmup_count


def _invert_time_summary(summary, byte_count, qs):
    """Throughput summary obtained by inverting a time summary.

    Percentiles, min/max and median map exactly under ``bytes / time``.
    std/cv/mad use a first-order (delta method) approximation and exist
    only to give a rough spread estimate.
    """
    if not summary or not byte_count:
        return None

    def invert(value):
        if value is None or value <= 0:
            return None
        return byte_count / value

    out = dict(summary)
    out["mean"] = invert(summary.get("mean"))
    out["median"] = invert(summary.get("median"))
    out["min"], out["max"] = invert(summary.get("max")), invert(
        summary.get("min"))
    if summary.get("mean"):
        out["std"] = (
            byte_count * summary["std"] / summary["mean"] ** 2
            if summary.get("std") is not None else None)
    if summary.get("median"):
        out["mad"] = (
            byte_count * summary["mad"] / summary["median"] ** 2
            if summary.get("mad") is not None else None)
    out["cv"] = summary.get("cv")

    for q in qs:
        target = _percentile_key(q)
        source = _percentile_key(100.0 - q)
        out[target] = invert(summary.get(source))

    ci = summary.get("ci95_mean")
    if ci and None not in ci:
        out["ci95_mean"] = [invert(ci[1]), invert(ci[0])]
    else:
        out["ci95_mean"] = None
    return out


def _summarize_metric(records, field, byte_count, scfg):
    values, dropped = apply_trim(
        [rec.get(field) for rec in records], scfg["trim"])
    time_summary = summarize(values, scfg["percentiles"], scfg["ci_level"])

    throughput = _invert_time_summary(
        time_summary, byte_count, scfg["percentiles"])

    by_round = defaultdict(list)
    for rec in records:
        value = rec.get(field)
        if value is None:
            continue
        try:
            by_round[rec.get("round", 0)].append(float(value))
        except (TypeError, ValueError):
            continue
    sigma_within, sigma_between = _variance_decomposition(by_round)

    block = {
        "time_ns": time_summary,
        "throughput_gbps": throughput,
        "sigma_within_ns": sigma_within,
        "sigma_between_ns": sigma_between,
        "per_round_median_ns": {
            str(rnd): float(np.median(vals))
            for rnd, vals in sorted(by_round.items())
        },
    }
    return block, dropped


def _contexts(observations):
    """First context seen per round, plus a merged view for plotting."""
    by_round = {}
    caches = None
    num_cpus = None
    for rec in observations:
        ctx = rec.get("context") or {}
        rnd = rec.get("round")
        if rnd is not None and rnd not in by_round and ctx:
            by_round[rnd] = ctx
        if caches is None and ctx.get("caches"):
            caches = ctx["caches"]
        if num_cpus is None and ctx.get("num_cpus"):
            num_cpus = ctx["num_cpus"]
    return {
        "rounds": {str(k): v for k, v in sorted(by_round.items())},
        "caches": caches or [],
        "num_cpus": num_cpus,
    }


def _build_warnings(groups, observations, scfg, cfg):
    warnings = []
    expected = int(cfg.get("runs", 1)) * int(cfg.get("repetitions", 1))
    for name, records in sorted(groups.items()):
        if len(records) < expected:
            warnings.append(
                f"{name}: {len(records)} of {expected} expected observations")

    if len(groups) and expected < int(scfg["min_meaningful_n"]):
        warnings.append(
            f"percentiles are not meaningful: {expected} observations per "
            f"benchmark (< {scfg['min_meaningful_n']}); treat p05/p95 as "
            "min/max")

    contexts = _contexts(observations)["rounds"]
    threshold = float(scfg["load_avg_threshold"])
    scaling = set()
    for rnd, ctx in sorted(contexts.items()):
        if ctx.get("cpu_scaling_enabled") is not None:
            scaling.add(ctx["cpu_scaling_enabled"])
        load = ctx.get("load_avg") or []
        cpus = ctx.get("num_cpus")
        if load and cpus:
            if float(load[0]) > threshold * float(cpus):
                warnings.append(
                    f"round {rnd}: load_avg[0]={load[0]:.2f} on {cpus} cpus "
                    f"(threshold {threshold:.2f} x cpus)")
    if len(scaling) > 1:
        warnings.append(
            f"cpu_scaling_enabled changed between rounds: {sorted(scaling)}")

    for name, records in groups.items():
        byte_count = records[0].get("bytes")
        bps = [r for r in records if r.get("bytes_per_second")]
        if byte_count and bps:
            rec = bps[0]
            time_value = rec.get(rec_field(rec))
            if not time_value:
                continue
            expected_bps = byte_count / time_value * 1e9
            actual_bps = float(rec["bytes_per_second"])
            if actual_bps > 0 and expected_bps > 0:
                delta = abs(actual_bps - expected_bps) / actual_bps
                if delta > 0.05:
                    warnings.append(
                        f"{name}: bytes_per_second disagrees with "
                        f"bytes/time by {delta:.1%}")
    return warnings


def rec_field(rec):
    """Time field used for the bytes_per_second cross-check."""
    if rec.get("real_time_ns") is not None:
        return "real_time_ns"
    return "cpu_time_ns"


def build_stats(observations, cfg, extra_meta=None):
    """Build the complete stats document from normalized observations."""
    scfg = stats_config(cfg.get("stats"))
    groups, gaps, warmup_count = _group_observations(observations)

    benchmarks = {}
    for name, records in sorted(groups.items()):
        parsed = parse_benchmark_name(name)
        if parsed:
            scenario, header, payload = parsed
            byte_count = header + payload
        else:
            scenario = header = payload = None
            byte_count = records[0].get("bytes")

        entry = {
            "scenario": scenario,
            "header": header,
            "payload": payload,
            "bytes": byte_count,
            "n": len(records),
            "metrics": {},
            "discarded": [],
        }
        for metric, field in METRICS.items():
            block, dropped = _summarize_metric(
                records, field, byte_count, scfg)
            entry["metrics"][metric] = block
            entry["discarded"].extend(
                {"metric": metric, "value": value, "reason": reason}
                for value, reason in dropped)
        benchmarks[name] = entry

    contexts = _contexts(observations)
    doc = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": cfg.get("name"),
        "config": {
            "runs": cfg.get("runs", 1),
            "warmup_runs": cfg.get("warmup_runs", 0),
            "repetitions": cfg.get("repetitions", 1),
            "isolation": cfg.get("isolation", "batch"),
            "seed": cfg.get("seed", 0),
            "stats": scfg,
        },
        "totals": {
            "observations": sum(len(v) for v in groups.values()),
            "warmup_observations": warmup_count,
            "benchmarks": len(benchmarks),
            "failed_launches": len(gaps),
        },
        "environment": contexts,
        "benchmarks": benchmarks,
        "warnings": _build_warnings(groups, observations, scfg, cfg),
        "gaps": gaps,
    }
    if extra_meta:
        doc["external_meta"] = extra_meta
    return doc


# --- Output ---------------------------------------------------------------


CSV_COLUMNS = [
    "name", "scenario", "header", "payload", "bytes", "metric", "n",
    "mean", "median", "std", "cv", "mad", "min", "max",
    "ci95_lo", "ci95_hi", "p05", "p25", "p75", "p95",
]


def write_stats_json(stats, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return path


def write_stats_csv(stats, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, entry in sorted(stats["benchmarks"].items()):
        for metric in METRICS:
            block = entry["metrics"].get(metric) or {}
            summary = block.get("time_ns")
            if not summary:
                continue
            ci = summary.get("ci95_mean") or [None, None]
            rows.append({
                "name": name,
                "scenario": entry.get("scenario"),
                "header": entry.get("header"),
                "payload": entry.get("payload"),
                "bytes": entry.get("bytes"),
                "metric": metric,
                "n": summary.get("n"),
                "mean": summary.get("mean"),
                "median": summary.get("median"),
                "std": summary.get("std"),
                "cv": summary.get("cv"),
                "mad": summary.get("mad"),
                "min": summary.get("min"),
                "max": summary.get("max"),
                "ci95_lo": ci[0],
                "ci95_hi": ci[1],
                "p05": summary.get("p05"),
                "p25": summary.get("p25"),
                "p75": summary.get("p75"),
                "p95": summary.get("p95"),
            })
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def format_summary(stats, metric="cpu", limit=None):
    """Compact console table: one line per benchmark."""
    lines = [
        f"{'benchmark':44s} {'n':>3s} {'median':>10s} {'p25':>10s} "
        f"{'p75':>10s} {'cv':>7s} {'GB/s':>9s}",
    ]
    names = sorted(stats["benchmarks"])
    if limit:
        names = names[:limit]
    for name in names:
        entry = stats["benchmarks"][name]
        summary = (entry["metrics"].get(metric) or {}).get("time_ns") or {}
        tp = (entry["metrics"].get(metric) or {}).get("throughput_gbps") or {}
        if not summary:
            continue
        cv = summary.get("cv")
        lines.append(
            f"{name:44s} {summary['n']:3d} {summary['median']:10.3f} "
            f"{(summary.get('p25') or 0):10.3f} "
            f"{(summary.get('p75') or 0):10.3f} "
            f"{(cv if cv is not None else float('nan')):7.3f} "
            f"{(tp.get('median') or 0):9.3f}")
    return "\n".join(lines)
