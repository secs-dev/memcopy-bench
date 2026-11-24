#!/usr/bin/env python3
"""Render interactive Plotly charts from benchmark results.

Accepts either a raw Google Benchmark JSON file (legacy, single-run) or a
``stats.json`` produced by ``tools/bench_stats.py``. With statistics, each
series is drawn as a median line with a p25-p75 band, separately for the
CPU-time and real-time metrics. Raw single-run input keeps the original
file names and uses ``real_time`` / ``bytes_per_second`` as before.
"""
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import plotly.graph_objects as go

# Standard qualitative palette (D3.js / Matplotlib tab10 extended)
PALETTE = [
    "#1f77b4",  # Blue
    "#ff7f0e",  # Orange
    "#2ca02c",  # Green
    "#d62728",  # Red
    "#9467bd",  # Purple
    "#8c564b",  # Brown
    "#e377c2",  # Pink
    "#7f7f7f",  # Gray
    "#bcbd22",  # Olive
    "#17becf",  # Cyan
    "#3366cc",  # Royal Blue
    "#dc3912",  # Vermilion
    "#109618",  # Forest Green
    "#990099",  # Violet
    "#0099c6",  # Deep Teal
    "#dd4477",  # Crimson Rose
]

SCENARIO_RE = re.compile(r"^BM_PacketScenario/([^/]+)/h(\d+)/p(\d+)$")
METHOD_RE = re.compile(r"^(BM_\w+)/(\d+)")

METRIC_LABELS = {"cpu": "CPU time", "real": "Real time"}


@dataclass
class Point:
    metric: str
    category: str
    key: object
    x: int
    time: Optional[float]
    time_lo: Optional[float]
    time_hi: Optional[float]
    tp: Optional[float]
    tp_lo: Optional[float]
    tp_hi: Optional[float]


def fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n // (1024 * 1024)} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


def hex_to_rgba(color: str, alpha: float) -> str:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def add_cache_lines(fig: go.Figure, caches: list[dict]) -> None:
    seen = set()
    for cache in caches:
        level = cache.get("level")
        size = cache.get("size")
        if cache.get("type") in ("Data", "Unified") and level not in seen:
            seen.add(level)
            fig.add_vline(
                x=size,
                line_dash="dash",
                line_color="#444444",
                line_width=1.5,
                annotation_text=f"<b>L{level} Cache ({fmt_bytes(size)})</b>",
                annotation_position="top left",
                annotation=dict(
                    bgcolor="rgba(255, 255, 255, 0.95)",
                    bordercolor="#444444",
                    borderwidth=1,
                    borderpad=4,
                    font=dict(size=11, color="#111111"),
                ),
            )


def classify_method(name: str):
    """Return (category, key, x) for a benchmark name, or None."""
    match = SCENARIO_RE.match(name)
    if match:
        return "scenario", (match.group(1), int(match.group(2))), int(
            match.group(3))

    match = METHOD_RE.match(name)
    if not match:
        return None
    raw_method, size = match.group(1), int(match.group(2))
    category = "fixed" if raw_method.endswith("_Fixed") else "dynamic"
    return category, raw_method.removesuffix("_Fixed"), size


def collect_points(data) -> tuple[list[Point], list[dict], bool]:
    """Normalize raw GBench JSON or stats.json into a list of points.

    Returns (points, caches, is_stats).
    """
    benchmarks = data.get("benchmarks")
    is_stats = isinstance(benchmarks, dict) and "schema" in data
    points: list[Point] = []

    if is_stats:
        for name, entry in benchmarks.items():
            scenario = entry.get("scenario")
            header = entry.get("header")
            payload = entry.get("payload")
            if scenario is not None:
                category, key, x = "scenario", (scenario, header), payload
            else:
                classified = classify_method(name)
                if not classified:
                    continue
                category, key, x = classified
            if x is None:
                continue
            for metric, block in (entry.get("metrics") or {}).items():
                time_summary = block.get("time_ns") or {}
                tp_summary = block.get("throughput_gbps") or {}
                if time_summary.get("median") is None:
                    continue
                points.append(Point(
                    metric=metric,
                    category=category,
                    key=key,
                    x=x,
                    time=time_summary.get("median"),
                    time_lo=time_summary.get("p25"),
                    time_hi=time_summary.get("p75"),
                    tp=tp_summary.get("median"),
                    tp_lo=tp_summary.get("p25"),
                    tp_hi=tp_summary.get("p75"),
                ))
        caches = (data.get("environment") or {}).get("caches") or []
        return points, caches, True

    for bm in benchmarks or []:
        # Aggregates are derived from repetitions; plotting them as separate
        # series would silently shadow the real observations.
        if bm.get("run_type") not in (None, "iteration"):
            continue
        name = bm.get("name") or bm.get("run_name") or ""
        classified = classify_method(name)
        if not classified:
            continue
        category, key, x = classified
        latency = bm.get("real_time")
        throughput = (bm.get("bytes_per_second") or 0) / 1e9
        points.append(Point(
            metric="real",
            category=category,
            key=key,
            x=x,
            time=latency,
            time_lo=None,
            time_hi=None,
            tp=throughput,
            tp_lo=None,
            tp_hi=None,
        ))
    caches = (data.get("context") or {}).get("caches") or []
    return points, caches, False


def group_points(points):
    """(metric, category, quantity) -> key -> x -> (value, lo, hi)."""
    series = defaultdict(lambda: defaultdict(dict))
    for point in points:
        if point.time is not None:
            series[(point.metric, point.category, "time")][point.key][
                point.x] = (point.time, point.time_lo, point.time_hi)
        if point.tp is not None:
            series[(point.metric, point.category, "tp")][point.key][
                point.x] = (point.tp, point.tp_lo, point.tp_hi)
    return series


def build_figure(
        series: dict,
        color_map: dict,
        caches: list[dict],
        title: str,
        y_title: str,
        y_log: bool,
        label_fn=None,
) -> go.Figure:
    """Build a line plot with an optional p25-p75 band."""
    fig = go.Figure()
    has_band = any(
        lo is not None or hi is not None
        for values in series.values()
        for _, lo, hi in values.values()
    )

    for key in sorted(series, key=str):
        sizes = sorted(series[key])
        values = [series[key][s] for s in sizes]
        y = [v[0] for v in values]
        color = color_map.get(key)
        if color is None and isinstance(key, tuple) and key:
            color = color_map.get(key[0])
        color = color or "#333333"
        if label_fn is None:
            label = str(key).removeprefix("BM_").removeprefix("Copy")
        else:
            label = label_fn(key)

        if has_band:
            upper = [v[2] for v in values]
            lower = [v[1] for v in values]
            if any(u is not None for u in upper):
                fig.add_trace(go.Scatter(
                    x=sizes, y=upper, mode="lines",
                    line=dict(width=0), hoverinfo="skip",
                    showlegend=False,
                ))
            if any(low is not None for low in lower):
                fig.add_trace(go.Scatter(
                    x=sizes, y=lower, mode="lines",
                    line=dict(width=0), fill="tonexty",
                    fillcolor=hex_to_rgba(color, 0.18),
                    hoverinfo="skip", showlegend=False,
                ))

        fig.add_trace(
            go.Scatter(
                x=sizes,
                y=y,
                mode="lines+markers",
                name=label,
                line=dict(color=color, width=2),
                marker=dict(color=color, size=6),
            )
        )

    add_cache_lines(fig, caches)
    fig.update_layout(
        title=title,
        xaxis=dict(title="Buffer Size (Bytes)", type="log"),
        yaxis=dict(title=y_title, type="log" if y_log else "linear"),
        template="plotly_white",
        width=960,
        height=540,
        margin=dict(l=70, r=40, t=60, b=60),
    )
    return fig


def plot(input_path: str, output_target: str) -> None:
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    points, caches, is_stats = collect_points(data)
    series = group_points(points)

    metrics = sorted({metric for metric, _, _ in series})
    if not metrics:
        print("No plottable benchmarks found.", file=sys.stderr)
        return

    target = Path(output_target)
    out_dir = target.parent if target.suffix == ".html" else target
    out_dir.mkdir(parents=True, exist_ok=True)

    # Consistent color assignment within each category.
    methods = sorted(
        {key for (_, cat, _), groups in series.items()
         if cat in ("dynamic", "fixed") for key in groups}
    )
    method_color = {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(methods)}

    scenarios = sorted(
        {key[0] for (_, cat, _), groups in series.items()
         if cat == "scenario" for key in groups})
    scenario_color = {
        s: PALETTE[i % len(PALETTE)] for i, s in enumerate(scenarios)
    }

    suffix_for = (lambda metric: "") if not is_stats else (
        lambda metric: f"_{metric}")

    def metric_title(base: str, metric: str) -> str:
        if not is_stats:
            return base
        return f"{base} [{METRIC_LABELS.get(metric, metric)}, median with IQR]"

    # --- Dynamic / fixed method benchmarks ---
    configs = [
        ("dynamic", "Dynamic Size", "copy"),
        ("fixed", "Fixed Size", "copy_fixed"),
    ]
    for metric in metrics:
        for cat, label, prefix in configs:
            tp_series = series.get((metric, cat, "tp"))
            time_series = series.get((metric, cat, "time"))
            if not tp_series:
                continue
            suffix = suffix_for(metric)
            fig_tp = build_figure(
                tp_series, method_color, caches,
                metric_title(f"Memory Copy Throughput vs Buffer Size "
                             f"({label})", metric),
                "Throughput (GB/s)", y_log=False,
            )
            fig_time = build_figure(
                time_series, method_color, caches,
                metric_title(f"Single Copy Time vs Buffer Size ({label})",
                             metric),
                METRIC_LABELS.get(metric, "Time (ns)") + " (ns)",
                y_log=True,
            )
            tp_path = out_dir / f"{prefix}_throughput{suffix}.html"
            time_path = out_dir / f"{prefix}_time{suffix}.html"
            fig_tp.write_html(str(tp_path), include_plotlyjs="cdn",
                              config={"responsive": False})
            fig_time.write_html(str(time_path), include_plotlyjs="cdn",
                                config={"responsive": False})
            print(f"[{label}] Saved throughput: {tp_path}")
            print(f"[{label}] Saved copy time:  {time_path}")

    # --- Scenario benchmarks: one pair per header size ---
    for metric in metrics:
        scenario_tp = series.get((metric, "scenario", "tp"))
        scenario_time = series.get((metric, "scenario", "time"))
        if not scenario_tp:
            continue

        tp_by_h = defaultdict(dict)
        time_by_h = defaultdict(dict)
        for (scenario, header), payload_map in scenario_tp.items():
            tp_by_h[header][scenario] = payload_map
        for (scenario, header), payload_map in (scenario_time or {}).items():
            time_by_h[header][scenario] = payload_map

        suffix = suffix_for(metric)
        for header in sorted(tp_by_h):
            fig_tp = build_figure(
                tp_by_h[header], scenario_color, caches,
                metric_title(f"Packet Copy Throughput vs Payload Size "
                             f"(H={header}, processed=H+P)", metric),
                "Throughput (GB/s)", y_log=False,
                label_fn=lambda key: str(key),
            )
            fig_time = build_figure(
                time_by_h[header], scenario_color, caches,
                metric_title(f"Packet Copy Time vs Payload Size "
                             f"(H={header}, processed=H+P)", metric),
                METRIC_LABELS.get(metric, "Time (ns)") + " (ns)",
                y_log=True,
                label_fn=lambda key: str(key),
            )
            tp_path = out_dir / (
                f"packet_scenario_h{header}_throughput{suffix}.html")
            time_path = out_dir / (
                f"packet_scenario_h{header}_time{suffix}.html")
            fig_tp.write_html(str(tp_path), include_plotlyjs="cdn",
                              config={"responsive": False})
            fig_time.write_html(str(time_path), include_plotlyjs="cdn",
                                config={"responsive": False})
            print(f"[Scenario H={header}] {METRIC_LABELS.get(metric, metric)} "
                  f"throughput: {tp_path}")
            print(f"[Scenario H={header}] {METRIC_LABELS.get(metric, metric)} "
                  f"copy time:  {time_path}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "results/example.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "reports"
    plot(src, dst)
