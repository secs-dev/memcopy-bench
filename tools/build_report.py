#!/usr/bin/env python3
"""Assemble one HTML report from a run's statistics, metadata, and plots.

The experiment orchestrator writes ``stats.json``, ``meta.json`` and a set of
Plotly ``*.html`` charts. This tool combines them into a single page:

* system, experiment, run, and environment characteristics;
* scenarios and payload sizes that were measured;
* every generated plot, embedded so the report works as one file;
* a data table under each plot, taken from ``stats.json``.

Examples
--------
Manual invocation::

    python tools/build_report.py \
        results/demo/20260101T000000Z/stats.json reports \
        --meta results/demo/20260101T000000Z/meta.json \
        --output reports/demo_report.html

Use ``--link-plots`` instead of embedding to keep plot files external.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STATS_PLOT_RE = re.compile(
    r"^(packet_scenario|copy)_(throughput|time)_(cpu|real)"
    r"_(mean_std|median_percentiles)\.html$")
LEGACY_SCENARIO_RE = re.compile(
    r"^packet_scenario_h(\d+)_(throughput|time)(?:_(cpu|real))?\.html$")
LEGACY_METHOD_RE = re.compile(
    r"^copy(_fixed)?_(throughput|time)(?:_(cpu|real))?\.html$")

@dataclass
class Plot:
    path: Path
    category: str          # "scenario" | "dynamic" | "fixed"
    header: int | None     # set only for legacy per-H scenario plots
    fixed: bool
    quantity: str
    metric: str

    @property
    def title(self) -> str:
        is_scenario = self.category == "scenario"
        axis = "Payload Size" if is_scenario else "Buffer Size"
        which = "Packet" if is_scenario else "Memory"
        if is_scenario and self.header is not None:
            suffix = f" (H={self.header})"
        elif not is_scenario:
            suffix = " (Fixed Size)" if self.fixed else " (Dynamic Size)"
        else:
            suffix = ""
        return (f"{which} {QUANTITY_LABELS[self.quantity]} vs {axis}"
                f"{suffix} [{METRIC_LABELS.get(self.metric, self.metric)}]")

def classify_plot_file(path: Path) -> Plot | None:
    name = path.name
    m = STATS_PLOT_RE.match(name)
    if m:
        prefix, quantity, metric, _variant = m.groups()
        return Plot(
            path=path,
            category="scenario" if prefix == "packet_scenario" else "dynamic",
            header=None,
            fixed=False,
            quantity=quantity,
            metric=metric,
        )
    m = LEGACY_SCENARIO_RE.match(name)
    if m:
        return Plot(
            path=path,
            category="scenario",
            header=int(m.group(1)),
            fixed=False,
            quantity=m.group(2),
            metric=m.group(3) or "real",
        )
    m = LEGACY_METHOD_RE.match(name)
    if m:
        fixed = bool(m.group(1))
        return Plot(
            path=path,
            category="fixed" if fixed else "dynamic",
            header=None,
            fixed=fixed,
            quantity=m.group(2),
            metric=m.group(3) or "real",
        )
    return None

PERCENTILE_RE = re.compile(r"^p(\d+(?:\.\d+)?)$")
METHOD_RE = re.compile(r"^(BM_\w+)/(\d+)")

METRIC_LABELS = {"cpu": "CPU time", "real": "Real time"}
QUANTITY_LABELS = {"throughput": "Throughput", "time": "Copy time"}
QUANTITY_UNITS = {"throughput": "GB/s", "time": "ns"}


# @dataclass
# class Plot:
#     path: Path
#     header: int | None
#     fixed: bool
#     quantity: str
#     metric: str
#
#     @property
#     def category(self) -> str:
#         if self.header is not None:
#             return "scenario"
#         return "fixed" if self.fixed else "dynamic"
#
#     @property
#     def title(self) -> str:
#         axis = "Payload Size" if self.header is not None else "Buffer Size"
#         which = "Packet" if self.header is not None else "Memory"
#         suffix = f" (H={self.header})" if self.header is not None else (
#             " (Fixed Size)" if self.fixed else " (Dynamic Size)")
#         return (f"{which} {QUANTITY_LABELS[self.quantity]} vs {axis}"
#                 f"{suffix} [{METRIC_LABELS.get(self.metric, self.metric)}]")
#

# --- Formatting helpers ----------------------------------------------------


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        absolute = abs(value)
        if absolute >= 1000:
            return f"{value:,.2f}"
        if absolute >= 1:
            return f"{value:.3f}"
        if absolute == 0:
            return "0"
        return f"{value:.4g}"
    if isinstance(value, (dict, list, tuple)):
        return f"<pre>{esc(json.dumps(value, indent=2, ensure_ascii=False))}</pre>"
    return esc(value)


def kv_table(items: dict) -> str:
    rows = "".join(
        f"<tr><th>{esc(key)}</th><td>{fmt(value)}</td></tr>"
        for key, value in items.items()
    )
    return f'<table class="kv">{rows}</table>'


def data_table(headers: list[str], rows: list[list]) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{fmt(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (f'<div class="table-wrap"><table class="data">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody>"
            f"</table></div>")


# --- Input -----------------------------------------------------------------


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def discover_plots(plots_dir: Path, output: Path) -> tuple[list[Plot], list[Path]]:
    plots, skipped = [], []
    if not plots_dir.exists():
        return plots, skipped
    for path in sorted(plots_dir.glob("*.html")):
        if path.resolve() == output.resolve() or "report" in path.name.lower():
            continue
        plot = classify_plot_file(path)
        if plot is None:
            skipped.append(path)
            continue
        plots.append(plot)
    plots.sort(key=lambda p: (
        0 if p.category == "scenario" else 1,
        p.header or 0,
        0 if p.quantity == "throughput" else 1,
        0 if p.metric == "cpu" else 1,
    ))
    return plots, skipped


# --- Statistics tables ------------------------------------------------------


def benchmark_meta(name: str, entry: dict) -> dict:
    if entry.get("scenario") is not None:
        return {
            "kind": "scenario",
            "method": entry["scenario"],
            "header": entry.get("header"),
            "size": entry.get("payload"),
        }
    match = METHOD_RE.match(name)
    if not match:
        return {"kind": "other", "method": name, "header": None, "size": None}
    raw, size = match.group(1), int(match.group(2))
    return {
        "kind": "fixed" if raw.endswith("_Fixed") else "dynamic",
        "method": raw.removesuffix("_Fixed"),
        "header": None,
        "size": size,
    }


def percentile_keys(summaries: list[dict]) -> list[str]:
    keys = {
        key for summary in summaries for key in summary
        if PERCENTILE_RE.match(key)
    }
    return sorted(keys, key=lambda key: float(key[1:]))


def plot_rows(stats: dict, plot: Plot):
    rows = []
    for name, entry in sorted(stats.get("benchmarks", {}).items()):
        meta = benchmark_meta(name, entry)
        if plot.category == "scenario":
            if meta["kind"] != "scenario":
                continue
            if plot.header is not None and meta["header"] != plot.header:
                continue
        elif meta["kind"] != plot.category:
            continue

        block = (entry.get("metrics") or {}).get(plot.metric) or {}
        summary = block.get(
            "throughput_gbps" if plot.quantity == "throughput" else "time_ns"
        ) or {}
        if not summary:
            continue
        rows.append((meta, entry, block, summary))

    rows.sort(key=lambda item: (
        str(item[0]["method"]), item[0]["size"] or 0, item[0]["header"] or 0))
    return rows


def render_plot_table(stats: dict, plot: Plot) -> tuple[str, int]:
    rows = plot_rows(stats, plot)
    if not rows:
        return '<p class="muted">No matching rows in stats.json.</p>', 0

    keys = percentile_keys([summary for _, _, _, summary in rows])
    scenario = plot.category == "scenario"
    headers = ["Scenario" if scenario else "Method"]
    if scenario:
        headers.append("H")
    headers += ["Payload" if scenario else "Size", "Bytes", "n", "mean", "std",
                "median", "min", "max", "cv", "CI95"]
    headers += keys
    if plot.quantity == "time":
        headers += ["σ within", "σ between"]

    table_rows = []
    for meta, entry, block, summary in rows:
        ci = summary.get("ci95_mean")
        row = [meta["method"]]
        if scenario:
            row.append(meta["header"])
        row += [
            meta["size"], entry.get("bytes"), summary.get("n"),
            summary.get("mean"), summary.get("std"), summary.get("median"),
            summary.get("min"), summary.get("max"), summary.get("cv"),
            "—" if not ci else f"{fmt(ci[0])} … {fmt(ci[1])}",
        ]
        row += [summary.get(key) for key in keys]
        if plot.quantity == "time":
            row += [block.get("sigma_within_ns"),
                    block.get("sigma_between_ns")]
        table_rows.append(row)

    unit = QUANTITY_UNITS[plot.quantity]
    return (f'<p class="muted">Values in {unit}. '
            f"{len(table_rows)} rows.</p>"
            + data_table(headers, table_rows)), len(table_rows)


# --- Report sections --------------------------------------------------------


def system_section(meta: dict | None, stats: dict) -> str:
    parts = []
    if meta:
        for title, key in (
                ("Experiment", "experiment"),
                ("Run", "run"),
                ("System", "system"),
                ("Environment", "environment")):
            value = meta.get(key)
            if value:
                parts.append(f"<h3>{esc(title)}</h3>{kv_table(value)}")
    else:
        parts.append('<p class="muted">meta.json was not supplied; only '
                     "statistics-side environment data is shown.</p>")

    environment = stats.get("environment") or {}
    rounds = environment.get("rounds") or {}
    if rounds:
        rd_headers = ["Round", "CPUs", "MHz", "Scaling", "Load avg"]
        rd_rows = []
        for rnd, ctx in sorted(rounds.items(), key=lambda kv: int(kv[0])):
            rd_rows.append([
                rnd, ctx.get("num_cpus"), ctx.get("mhz_per_cpu"),
                ctx.get("cpu_scaling_enabled"), ctx.get("load_avg"),
            ])
        parts.append("<h3>Round contexts</h3>")
        parts.append(data_table(rd_headers, rd_rows))

    caches = environment.get("caches") or []
    if caches:
        rows = [
            [c.get("level"), c.get("type"), c.get("size"),
             c.get("num_sharing")]
            for c in caches
        ]
        parts.append("<h3>Cache hierarchy</h3>")
        parts.append(data_table(
            ["Level", "Type", "Size (bytes)", "Sharing"], rows))
    return "\n".join(parts)


def scenario_section(stats: dict) -> str:
    benchmarks = stats.get("benchmarks") or {}
    by_scenario: dict[str, list[dict]] = {}
    methods: dict[str, list[int]] = {}
    for name, entry in benchmarks.items():
        meta = benchmark_meta(name, entry)
        if meta["kind"] == "scenario":
            by_scenario.setdefault(meta["method"], []).append(entry)
        else:
            methods.setdefault(meta["method"], []).append(meta["size"])

    parts = []
    if by_scenario:
        rows = []
        for scenario, entries in sorted(by_scenario.items()):
            headers = sorted({e.get("header") for e in entries})
            payloads = sorted({e.get("payload") for e in entries})
            rows.append([scenario, len(entries), headers, payloads])
        parts.append(data_table(
            ["Scenario", "Benchmarks", "Header sizes", "Payload sizes"], rows))
    if methods:
        rows = [[method, len(sizes), min(sizes), max(sizes)]
                for method, sizes in sorted(methods.items())]
        parts.append("<h3>Method benchmarks</h3>")
        parts.append(data_table(
            ["Method", "Benchmarks", "Min size", "Max size"], rows))

    totals = stats.get("totals") or {}
    config = stats.get("config") or {}
    summary = {
        "experiment": stats.get("experiment"),
        "observations": totals.get("observations"),
        "warmup observations": totals.get("warmup_observations"),
        "benchmarks": totals.get("benchmarks"),
        "failed launches": totals.get("failed_launches"),
        "runs": config.get("runs"),
        "warmup runs": config.get("warmup_runs"),
        "repetitions": config.get("repetitions"),
        "isolation": config.get("isolation"),
        "seed": config.get("seed"),
        "percentiles": (config.get("stats") or {}).get("percentiles"),
    }
    parts.append("<h3>Run summary</h3>")
    parts.append(kv_table(summary))
    return "\n".join(parts)


def warnings_section(stats: dict) -> str:
    warnings = stats.get("warnings") or []
    gaps = stats.get("gaps") or []
    parts = []
    if warnings:
        parts.append("<ul>" + "".join(
            f"<li>{esc(w)}</li>" for w in warnings) + "</ul>")
    else:
        parts.append('<p class="muted">No warnings.</p>')
    if gaps:
        rows = [[g.get("round"), g.get("name"), g.get("reason")]
                for g in gaps]
        parts.append("<h3>Gaps</h3>")
        parts.append(data_table(["Round", "Benchmark", "Reason"], rows))
    return "\n".join(parts)


def embed_plot(plot: Plot, output: Path, link: bool) -> str:
    if link:
        src = os.path.relpath(plot.path, output.parent)
        return (f'<iframe class="plot" loading="lazy" '
                f'src="{esc(src)}"></iframe>')
    content = plot.path.read_text(encoding="utf-8")
    return (f'<iframe class="plot" loading="lazy" '
            f'srcdoc="{html.escape(content, quote=True)}"></iframe>')


CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 32px 64px; font: 14px/1.5 system-ui,
       -apple-system, "Segoe UI", sans-serif; color: #172033;
       background: #f6f7f9; }
header { margin-bottom: 18px; }
h1 { margin: 0 0 4px; font-size: 28px; }
h2 { margin: 36px 0 10px; font-size: 21px; border-bottom: 2px solid #d9dee7;
     padding-bottom: 6px; }
h3 { margin: 22px 0 8px; font-size: 16px; }
a { color: #0b5cad; text-decoration: none; }
a:hover { text-decoration: underline; }
.muted { color: #5d6b82; }
section { background: #fff; border: 1px solid #dfe4ec; border-radius: 10px;
          padding: 18px 22px; margin: 18px 0; box-shadow: 0 1px 2px
          rgba(16, 24, 40, .04); }
nav { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 10px 0 0; }
nav a { font-weight: 600; }
.table-wrap { overflow-x: auto; margin: 10px 0 20px; }
table { border-collapse: collapse; width: 100%; background: #fff; }
table.kv { max-width: 980px; }
table.kv th { width: 220px; text-align: left; vertical-align: top; }
th, td { border: 1px solid #dce2ea; padding: 6px 9px; text-align: left;
         vertical-align: top; }
table.data th { background: #eef2f7; position: sticky; top: 0; }
table.data tbody tr:nth-child(even) { background: #fafbfd; }
table.kv th { background: #f4f6f9; }
pre { margin: 0; white-space: pre-wrap; font-size: 12px; }
iframe.plot { width: 100%; height: 660px; border: 1px solid #dce2ea;
              border-radius: 8px; background: #fff; }
ul { margin: 8px 0; padding-left: 22px; }
"""


def build_report(stats: dict, meta: dict | None, plots: list[Plot],
                 skipped: list[Path], output: Path, title: str,
                 link: bool, stats_path: Path | None = None,
                 meta_path: Path | None = None,
                 plots_dir: Path | None = None) -> None:
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plot_sections = []
    for index, plot in enumerate(plots, 1):
        table, count = render_plot_table(stats, plot)
        plot_sections.append(f"""
<article id="plot-{index}">
  <h2>{esc(plot.title)}</h2>
  <p class="muted">{esc(plot.path.name)}</p>
  {embed_plot(plot, output, link)}
  <h3>Data table ({count} rows)</h3>
  {table}
</article>""")

    skipped_note = ""
    if skipped:
        skipped_note = (
            '<p class="muted">Skipped non-plot HTML files: '
            + esc(", ".join(p.name for p in skipped)) + "</p>")
    plots_section = "\n".join(plot_sections) or (
        '<p class="muted">No matching Plotly HTML files were found in the '
        "plots directory.</p>")

    sources = {
        "stats.json": str(stats_path) if stats_path else stats.get("experiment"),
        "meta.json": str(meta_path) if meta_path else "not supplied",
        "plots directory": str(plots_dir or output.parent),
        "generated_at": generated,
        "embedded plots": not link,
    }
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>{esc(title)}</h1>
  <p class="muted">Generated {esc(generated)} from {esc(stats.get('experiment') or 'benchmark')} statistics.</p>
  <nav>
    <a href="#overview">Overview</a>
    <a href="#system">System</a>
    <a href="#scenarios">Scenarios</a>
    <a href="#warnings">Warnings</a>
    <a href="#plots">Plots and tables</a>
  </nav>
</header>

<section id="overview">
  <h2>Overview</h2>
  {kv_table(sources)}
</section>

<section id="system">
  <h2>System and environment</h2>
  {system_section(meta, stats)}
</section>

<section id="scenarios">
  <h2>Scenarios and measurements</h2>
  {scenario_section(stats)}
</section>

<section id="warnings">
  <h2>Warnings and gaps</h2>
  {warnings_section(stats)}
</section>

<section id="plots">
  <h2>Plots and data tables</h2>
  {skipped_note}
  {plots_section}
</section>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stats", help="path to stats.json")
    parser.add_argument("plots", help="directory with Plotly HTML files")
    parser.add_argument("--meta", help="path to meta.json")
    parser.add_argument("-o", "--output",
                        help="output HTML (default: <plots>/<name>_report.html)")
    parser.add_argument("--title", help="report title")
    parser.add_argument("--link-plots", action="store_true",
                        help="link plots instead of embedding them")
    args = parser.parse_args(argv)

    stats_path = Path(args.stats)
    plots_dir = Path(args.plots)
    if not stats_path.exists():
        print(f"stats not found: {stats_path}", file=sys.stderr)
        return 2
    if not plots_dir.is_dir():
        print(f"plots directory not found: {plots_dir}", file=sys.stderr)
        return 2

    stats = read_json(stats_path)
    if "schema" not in stats or not isinstance(stats.get("benchmarks"), dict):
        print("input is not a memcopy-bench stats.json", file=sys.stderr)
        return 2

    name = stats.get("experiment") or stats_path.parent.name
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_")
    output = Path(args.output) if args.output else (
        plots_dir / f"{safe_name or 'report'}_report.html")
    meta = None
    meta_path = Path(args.meta) if args.meta else None
    if meta_path:
        if meta_path.exists():
            meta = read_json(meta_path)
        else:
            print(f"warning: meta not found: {meta_path}", file=sys.stderr)

    plots, skipped = discover_plots(plots_dir, output)
    title = args.title or f"{name} benchmark report"
    build_report(stats, meta, plots, skipped, output, title, args.link_plots,
                 stats_path=stats_path, meta_path=meta_path,
                 plots_dir=plots_dir)
    print(f"[ok] report: {output}")
    print(f"     plots: {len(plots)} embedded"
          if not args.link_plots else f"     plots: {len(plots)} linked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
