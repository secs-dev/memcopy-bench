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
        series: dict,
        color_map: dict,
        caches: list[dict],
        title: str,
        y_title: str,
        y_log: bool,
        label_fn=None,
) -> go.Figure:
    """Build a line plot.

    ``series`` maps a key to {x: y}. Default label logic strips the BM_ / Copy
    prefixes used by method benchmarks; callers with different key shapes can
    pass ``label_fn`` to format labels themselves.
    """
    fig = go.Figure()
    for key in sorted(series):
        sizes = sorted(series[key])
        y = [series[key][s] for s in sizes]
        if label_fn is None:
            label = key.removeprefix("BM_").removeprefix("Copy")
        else:
            label = label_fn(key)
        color = color_map.get(key, "#333333")
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

    # Categories: dynamic (runtime size), fixed (compile-time size),
    # scenario (compile-time scenario composed over payload-copy methods).
    data_by_cat = {
        "dynamic": {"tp": defaultdict(dict), "time": defaultdict(dict)},
        "fixed": {"tp": defaultdict(dict), "time": defaultdict(dict)},
        "scenario": {"tp": defaultdict(dict), "time": defaultdict(dict)},
    }

    scenario_re = re.compile(r"^BM_PacketScenario/(\w+)/h(\d+)/p(\d+)$")

    for bm in data.get("benchmarks", []):
        name = bm.get("name", "")
        tp = bm.get("bytes_per_second", 0) / 1e9  # GB/s
        latency = bm.get("real_time", 0)  # ns

        m_scn = scenario_re.match(name)
        if m_scn:
            scenario = m_scn.group(1)
            header = int(m_scn.group(2))
            payload = int(m_scn.group(3))
            key = (scenario, header)
            data_by_cat["scenario"]["tp"][key][payload] = tp
            data_by_cat["scenario"]["time"][key][payload] = latency
            continue

        m = re.match(r"^(BM_\w+)/(\d+)", name)
        if not m:
            continue
        raw_method, size = m.group(1), int(m.group(2))
        is_fixed = raw_method.endswith("_Fixed")
        cat = "fixed" if is_fixed else "dynamic"

        # Base method name without _Fixed suffix for consistent coloring
        base_method = raw_method.removesuffix("_Fixed")
        data_by_cat[cat]["tp"][base_method][size] = tp
        data_by_cat[cat]["time"][base_method][size] = latency

    caches = data.get("context", {}).get("caches", [])

    # Consistent color assignment within each category
    all_methods = sorted(
        set(data_by_cat["dynamic"]["tp"].keys())
        | set(data_by_cat["fixed"]["tp"].keys())
    )
    method_color = {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(all_methods)}

    all_scenarios = sorted({key[0] for key in data_by_cat["scenario"]["tp"]})
    scenario_color = {
        s: PALETTE[i % len(PALETTE)] for i, s in enumerate(all_scenarios)
    }

    target = Path(output_target)
    out_dir = target.parent if target.suffix == ".html" else target
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Existing dynamic / fixed plots (unchanged behavior) ---
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
            method_color,
            caches,
            f"Memory Copy Throughput vs Buffer Size ({label})",
            "Throughput (GB/s)",
            y_log=False,
        )
        fig_time = build_figure(
            time_series,
            method_color,
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

    # --- New scenario plots: one pair per header size ---
    # Each key is (scenario, header). We split by header so users can compare
    # scenarios at a fixed H; X axis is payload size.
    scenario_tp = data_by_cat["scenario"]["tp"]
    scenario_time = data_by_cat["scenario"]["time"]
    if not scenario_tp:
        return

    tp_by_h: dict[int, dict] = defaultdict(dict)
    time_by_h: dict[int, dict] = defaultdict(dict)
    for (scenario, header), payload_map in scenario_tp.items():
        tp_by_h[header][scenario] = payload_map
    for (scenario, header), payload_map in scenario_time.items():
        time_by_h[header][scenario] = payload_map

    for header in sorted(tp_by_h):
        tp_series = tp_by_h[header]
        time_series = time_by_h[header]

        fig_tp = build_figure(
            tp_series,
            scenario_color,
            caches,
            f"Packet Copy Throughput vs Payload Size "
            f"(H={header}, processed=H+P)",
            "Throughput (GB/s)",
            y_log=False,
            label_fn=lambda key: key,
        )
        fig_time = build_figure(
            time_series,
            scenario_color,
            caches,
            f"Packet Copy Time vs Payload Size (H={header}, processed=H+P)",
            "Time (ns)",
            y_log=True,
            label_fn=lambda key: key,
        )

        tp_path = out_dir / f"packet_scenario_h{header}_throughput.html"
        time_path = out_dir / f"packet_scenario_h{header}_time.html"

        fig_tp.write_html(str(tp_path), include_plotlyjs="cdn", config={"responsive": False})
        fig_time.write_html(str(time_path), include_plotlyjs="cdn", config={"responsive": False})

        print(f"[Scenario H={header}] Saved throughput: {tp_path}")
        print(f"[Scenario H={header}] Saved copy time:  {time_path}")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "results/all_methods_benchmark.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "reports"
    plot(src, dst)