#!/usr/bin/env python3
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

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


def fmt_bytes(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n // (1024 * 1024)} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


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


def build_figure(
    series: dict[str, dict[int, float]],
    color_map: dict[str, str],
    caches: list[dict],
    title: str,
    y_title: str,
    y_log: bool,
) -> go.Figure:
    fig = go.Figure()
    for method in sorted(series):
        sizes = sorted(series[method])
        y = [series[method][s] for s in sizes]
        label = method.removeprefix("BM_").removeprefix("Copy")
        color = color_map.get(method, "#333333")
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

    # Categories: dynamic (unknown size) vs fixed (compile-time constant size)
    data_by_cat = {
        "dynamic": {"tp": defaultdict(dict), "time": defaultdict(dict)},
        "fixed": {"tp": defaultdict(dict), "time": defaultdict(dict)},
    }

    for bm in data.get("benchmarks", []):
        m = re.match(r"^(BM_\w+)/(\d+)", bm.get("name", ""))
        if not m:
            continue
        raw_method, size = m.group(1), int(m.group(2))
        is_fixed = raw_method.endswith("_Fixed")
        cat = "fixed" if is_fixed else "dynamic"

        # Base method name without _Fixed suffix for consistent coloring
        base_method = raw_method.removesuffix("_Fixed")
        tp = bm.get("bytes_per_second", 0) / 1e9  # GB/s
        latency = bm.get("real_time", 0)  # ns

        data_by_cat[cat]["tp"][base_method][size] = tp
        data_by_cat[cat]["time"][base_method][size] = latency

    caches = data.get("context", {}).get("caches", [])

    # Consistent color assignment across base methods
    all_methods = sorted(
        set(data_by_cat["dynamic"]["tp"].keys()) | set(data_by_cat["fixed"]["tp"].keys())
    )
    color_map = {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(all_methods)}

    target = Path(output_target)
    out_dir = target.parent if target.suffix == ".html" else target
    out_dir.mkdir(parents=True, exist_ok=True)

    configs = [
        ("dynamic", "Dynamic Size", "copy"),
        ("fixed", "Fixed Size", "copy_fixed"),
    ]

    for cat, label, prefix in configs:
        tp_series = data_by_cat[cat]["tp"]
        time_series = data_by_cat[cat]["time"]

        if not tp_series:
            continue

        fig_tp = build_figure(
            tp_series,
            color_map,
            caches,
            f"Memory Copy Throughput vs Buffer Size ({label})",
            "Throughput (GB/s)",
            y_log=False,
        )
        fig_time = build_figure(
            time_series,
            color_map,
            caches,
            f"Single Copy Time vs Buffer Size ({label})",
            "Time (ns)",
            y_log=True,
        )

        tp_path = out_dir / f"{prefix}_throughput.html"
        time_path = out_dir / f"{prefix}_time.html"

        fig_tp.write_html(str(tp_path), include_plotlyjs="cdn", config={"responsive": False})
        fig_time.write_html(str(time_path), include_plotlyjs="cdn", config={"responsive": False})

        print(f"[{label}] Saved throughput: {tp_path}")
        print(f"[{label}] Saved copy time:  {time_path}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "results/all_methods_benchmark.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "reports"
    plot(src, dst)
