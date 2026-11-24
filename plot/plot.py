#!/usr/bin/env python3
"""Render interactive Plotly charts from benchmark results.

Accepts either a raw Google Benchmark JSON file (legacy, single-run) or a
``stats.json`` produced by ``tools/bench_stats.py``. With statistics, each
series is drawn as a median line with a mean ± std band and whiskers for every
configured percentile, separately for the CPU-time and real-time metrics. Raw
single-run input keeps the original file names and uses ``real_time`` /
``bytes_per_second`` as before.
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
PERCENTILE_RE = re.compile(r"^p(\d+(?:\.\d+)?)$")

METRIC_LABELS = {"cpu": "CPU time", "real": "Real time"}

SCENARIO_LABELS = {
    "Naive": "Naive byte loop",
    "Memcpy": "libc memcpy (baseline)",
    "Small": "Small overlapping copy",
    "Words64": "64-bit word copy",
    "ComposedPrefetch": "AVX2 + 4x unroll + prefetch",
    "ComposedStream": "AVX2 streaming (non-temporal stores)",
    "MemcpyEdgesAVXBody": "libc edges + AVX2/prefetch body",
    "AVXEdgesMemcpyBody": "AVX2/prefetch edges + libc body",
    "SSEEdgesMemcpyBody": "SSE2 edges + libc body",
    "ComposedSSE": "SSE2 aligned copy",
    "MemcpyEdgesSSEBody": "libc edges + SSE2 body",
}

VARIANT_LABELS = {
    "mean_std": "mean ± std",
    "median_percentiles": "median and percentiles",
}


@dataclass
class SeriesValue:
    value: float
    mean: Optional[float] = None
    std: Optional[float] = None
    quantiles: tuple[tuple[float, float], ...] = ()


@dataclass
class Point:
    metric: str
    category: str
    key: object
    x: int
    time: Optional[SeriesValue]
    tp: Optional[SeriesValue]


def fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n // (1024 * 1024)} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


def summary_quantiles(summary: dict) -> list[tuple[float, float]]:
    """Return sorted ``(percentile, value)`` pairs from a stats summary."""
    quantiles = []
    for key, value in summary.items():
        match = PERCENTILE_RE.match(key)
        if match and value is not None:
            quantiles.append((float(match.group(1)), float(value)))
    return sorted(quantiles)


def summary_series(summary: dict) -> Optional[SeriesValue]:
    if not summary or summary.get("median") is None:
        return None
    return SeriesValue(
        value=float(summary["median"]),
        mean=summary.get("mean"),
        std=summary.get("std"),
        quantiles=tuple(summary_quantiles(summary)),
    )


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
                time_value = summary_series(block.get("time_ns") or {})
                tp_value = summary_series(block.get("throughput_gbps") or {})
                if time_value is None:
                    continue
                points.append(Point(
                    metric=metric,
                    category=category,
                    key=key,
                    x=x,
                    time=time_value,
                    tp=tp_value,
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
            time=SeriesValue(value=latency) if latency is not None else None,
            tp=SeriesValue(value=throughput),
        ))
    caches = (data.get("context") or {}).get("caches") or []
    return points, caches, False


def group_points(points):
    """(metric, category, quantity) -> key -> x -> SeriesValue."""
    series = defaultdict(lambda: defaultdict(dict))
    for point in points:
        if point.time is not None:
            series[(point.metric, point.category, "time")][point.key][
                point.x] = point.time
        if point.tp is not None:
            series[(point.metric, point.category, "tp")][point.key][
                point.x] = point.tp
    return series


def _quantile_value(value: SeriesValue, level: float) -> Optional[float]:
    for q, v in value.quantiles:
        if abs(q - level) < 1e-9:
            return v
    return None


def build_figure(
        series: dict,
        color_map: dict,
        caches: list[dict],
        title: str,
        y_title: str,
        y_log: bool,
        label_fn=None,
        variant: Optional[str] = None,
        dash_fn=None,
        symbol_fn=None,
) -> go.Figure:
    """Build a chart for one metric, quantity, and presentation variant.

    ``variant="median_percentiles"`` draws the median with percentile whiskers.
    ``variant="mean_std"`` draws the mean with ± one standard deviation.
    ``variant=None`` keeps the legacy single-line raw JSON behaviour.
    """
    fig = go.Figure()

    for key in sorted(series, key=str):
        sizes = sorted(series[key])
        values = [series[key][s] for s in sizes]
        color = color_map.get(key)
        if color is None and isinstance(key, tuple) and key:
            color = color_map.get(key[0])
        color = color or "#333333"
        if label_fn is None:
            label = str(key).removeprefix("BM_").removeprefix("Copy")
        else:
            label = label_fn(key)
        dash = dash_fn(key) if dash_fn else "solid"
        symbol = symbol_fn(key) if symbol_fn else "circle"

        if variant == "median_percentiles":
            quantile_levels = sorted({
                q for value in values for q, _ in value.quantiles
            })

            # Vertical whiskers span the first to the last configured percentile.
            if len(quantile_levels) >= 2:
                low_level, high_level = quantile_levels[0], quantile_levels[-1]
                medians = [v.value for v in values]
                upper, lower = [], []
                for value, median in zip(values, medians):
                    hi = _quantile_value(value, high_level)
                    lo = _quantile_value(value, low_level)
                    upper.append(max(0.0, (hi or median) - median))
                    lower.append(max(0.0, median - (lo or median)))
                fig.add_trace(go.Scatter(
                    x=sizes, y=medians, mode="markers",
                    marker=dict(color="rgba(0,0,0,0)", size=0),
                    error_y=dict(
                        type="data", symmetric=False,
                        array=upper, arrayminus=lower,
                        color=color, thickness=1, width=5,
                    ),
                    hoverinfo="skip", showlegend=False,
                ))

                # Intermediate percentiles as horizontal ticks on the whisker.
                for level in quantile_levels[1:-1]:
                    if abs(level - 50.0) < 1e-9:
                        continue
                    tick_x, tick_y = [], []
                    for size, value in zip(sizes, values):
                        qv = _quantile_value(value, level)
                        if qv is not None:
                            tick_x.append(size)
                            tick_y.append(qv)
                    if tick_x:
                        fig.add_trace(go.Scatter(
                            x=tick_x, y=tick_y, mode="markers",
                            marker=dict(
                                symbol="line-ew", size=11, color=color,
                                line=dict(width=1.5, color=color),
                            ),
                            opacity=0.85,
                            hovertemplate=(f"{label} p{level:g}: "
                                           "%{y:.4g}<extra></extra>"),
                            showlegend=False,
                        ))

            fig.add_trace(go.Scatter(
                x=sizes,
                y=[v.value for v in values],
                mode="lines+markers",
                name=label,
                line=dict(color=color, width=2, dash=dash),
                marker=dict(color=color, size=6, symbol=symbol),
                hovertemplate=(f"{label} median: %{{y:.4g}}<extra></extra>"),
            ))

        elif variant == "mean_std":
            means = [v.mean for v in values]
            if not any(mean is not None for mean in means):
                continue
            upper, lower = [], []
            for mean, value in zip(means, values):
                std = value.std or 0.0
                upper.append(std)
                if mean is None:
                    lower.append(0.0)
                    continue
                max_lower = mean * (1.0 - 1e-6) if y_log else mean
                lower.append(max(0.0, min(std, max_lower)))
            fig.add_trace(go.Scatter(
                x=sizes,
                y=means,
                mode="lines+markers",
                name=label,
                line=dict(color=color, width=2, dash=dash),
                marker=dict(color=color, size=6, symbol=symbol),
                error_y=dict(
                    type="data", symmetric=False,
                    array=upper, arrayminus=lower,
                    color=color, thickness=1.5, width=4,
                ),
                hovertemplate=(f"{label} mean: %{{y:.4g}}<extra></extra>"),
            ))

        else:
            fig.add_trace(go.Scatter(
                x=sizes,
                y=[v.value for v in values],
                mode="lines+markers",
                name=label,
                line=dict(color=color, width=2),
                marker=dict(color=color, size=6),
            ))

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

    # Consistent color assignment. Scenario colours are shared by both header
    # sizes; the header size is distinguished by line style and marker.
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
    color_map = {**method_color, **scenario_color}

    if is_stats:
        headers = sorted({
            key[1] for (_, cat, _), groups in series.items()
            if cat == "scenario" for key in groups
            if isinstance(key, tuple) and len(key) > 1
        })

        def dash_for(key):
            if (isinstance(key, tuple) and len(key) > 1
                    and len(headers) > 1):
                return "solid" if key[1] == headers[0] else "dash"
            return "solid"

        def symbol_for(key):
            if (isinstance(key, tuple) and len(key) > 1
                    and len(headers) > 1):
                return "circle" if key[1] == headers[0] else "square"
            return "circle"

        def label_for(key):
            if isinstance(key, tuple) and len(key) > 1:
                name = SCENARIO_LABELS.get(key[0], key[0])
                return f"{name} (H={key[1]})"
            return str(key).removeprefix("BM_").removeprefix("Copy")

        prefix = "packet_scenario" if scenarios else "copy"
        kind = "Packet" if scenarios else "Memory"
        axis = "Payload" if scenarios else "Buffer"

        for metric in metrics:
            for quantity, qname, y_title, y_log in (
                    ("tp", "throughput", "Throughput (GB/s)", False),
                    ("time", "time",
                     METRIC_LABELS.get(metric, "Time") + " (ns)", True)):
                merged = {}
                for (series_metric, _cat, series_quantity), groups in (
                        series.items()):
                    if series_metric == metric and series_quantity == quantity:
                        merged.update(groups)
                if not merged:
                    continue

                for variant in ("mean_std", "median_percentiles"):
                    title = (
                        f"{kind} Copy "
                        f"{'Throughput' if quantity == 'tp' else 'Time'} vs "
                        f"{axis} Size — {VARIANT_LABELS[variant]} "
                        f"[{METRIC_LABELS.get(metric, metric)}]")
                    fig = build_figure(
                        merged, color_map, caches, title, y_title, y_log,
                        label_fn=label_for, variant=variant,
                        dash_fn=dash_for, symbol_fn=symbol_for,
                    )
                    path = out_dir / (
                        f"{prefix}_{qname}_{metric}_{variant}.html")
                    fig.write_html(str(path), include_plotlyjs="cdn",
                                   config={"responsive": False})
                    print(f"[{METRIC_LABELS.get(metric, metric)}] "
                          f"{VARIANT_LABELS[variant]} {qname}: {path}")
        return

    # --- Legacy raw single-run JSON: one line per measured point ---
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
            fig_tp = build_figure(
                tp_series, method_color, caches,
                f"Memory Copy Throughput vs Buffer Size ({label})",
                "Throughput (GB/s)", y_log=False,
            )
            fig_time = build_figure(
                time_series, method_color, caches,
                f"Single Copy Time vs Buffer Size ({label})",
                METRIC_LABELS.get(metric, "Time (ns)") + " (ns)",
                y_log=True,
            )
            tp_path = out_dir / f"{prefix}_throughput.html"
            time_path = out_dir / f"{prefix}_time.html"
            fig_tp.write_html(str(tp_path), include_plotlyjs="cdn",
                              config={"responsive": False})
            fig_time.write_html(str(time_path), include_plotlyjs="cdn",
                                config={"responsive": False})
            print(f"[{label}] Saved throughput: {tp_path}")
            print(f"[{label}] Saved copy time:  {time_path}")

    # Raw scenario input still gets one pair per header size.
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

        for header in sorted(tp_by_h):
            fig_tp = build_figure(
                tp_by_h[header], scenario_color, caches,
                f"Packet Copy Throughput vs Payload Size (H={header}, "
                "processed=H+P)",
                "Throughput (GB/s)", y_log=False,
                label_fn=lambda key: str(key),
            )
            fig_time = build_figure(
                time_by_h[header], scenario_color, caches,
                f"Packet Copy Time vs Payload Size (H={header}, "
                "processed=H+P)",
                METRIC_LABELS.get(metric, "Time (ns)") + " (ns)",
                y_log=True,
                label_fn=lambda key: str(key),
            )
            tp_path = out_dir / f"packet_scenario_h{header}_throughput.html"
            time_path = out_dir / f"packet_scenario_h{header}_time.html"
            fig_tp.write_html(str(tp_path), include_plotlyjs="cdn",
                              config={"responsive": False})
            fig_time.write_html(str(time_path), include_plotlyjs="cdn",
                                config={"responsive": False})
            print(f"[Scenario H={header}] "
                  f"{METRIC_LABELS.get(metric, metric)} throughput: {tp_path}")
            print(f"[Scenario H={header}] "
                  f"{METRIC_LABELS.get(metric, metric)} copy time:  "
                  f"{time_path}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "results/example.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "reports"
    plot(src, dst)
