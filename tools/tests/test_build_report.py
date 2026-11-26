#!/usr/bin/env python3
"""Unit tests for tools/build_report.py (stdlib unittest)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_report as br  # noqa: E402


def summary(median=2.0, n=3):
    return {
        "n": n,
        "mean": median * 1.01,
        "median": median,
        "std": median * 0.05,
        "mad": median * 0.02,
        "min": median * 0.9,
        "max": median * 1.1,
        "cv": 0.05,
        "ci95_mean": [median * 0.95, median * 1.05],
        "p05": median * 0.9,
        "p25": median * 0.97,
        "p75": median * 1.03,
        "p95": median * 1.1,
    }


def stats_fixture():
    return {
        "schema": "memcopy-bench/stats/v1",
        "experiment": "demo",
        "config": {
            "runs": 1, "warmup_runs": 0, "repetitions": 3,
            "isolation": "batch", "seed": 0,
            "stats": {"percentiles": [5, 25, 50, 75, 95]},
        },
        "totals": {
            "observations": 3, "warmup_observations": 0,
            "benchmarks": 1, "failed_launches": 0,
        },
        "environment": {
            "rounds": {"0": {
                "num_cpus": 8, "mhz_per_cpu": 4200,
                "cpu_scaling_enabled": False, "load_avg": [0.1, 0.2, 0.3],
            }},
            "caches": [{"level": 1, "type": "Data", "size": 32768,
                        "num_sharing": 2}],
            "num_cpus": 8,
        },
        "benchmarks": {
            "BM_PacketScenario/Memcpy/h8/p64": {
                "scenario": "Memcpy", "header": 8, "payload": 64,
                "bytes": 72, "n": 3,
                "metrics": {
                    "cpu": {
                        "time_ns": summary(),
                        "throughput_gbps": summary(36.0),
                        "sigma_within_ns": 0.05,
                        "sigma_between_ns": 0.02,
                        "per_round_median_ns": {"0": 2.0},
                    },
                },
                "discarded": [],
            },
        },
        "warnings": ["demo warning"],
        "gaps": [],
    }


class BuildReportTest(unittest.TestCase):
    def test_percentile_keys_are_sorted_numerically(self):
        keys = br.percentile_keys([{"p95": 1, "p5": 2, "p25": 3, "name": 4}])
        self.assertEqual(keys, ["p5", "p25", "p95"])

    def test_benchmark_meta_for_scenario_and_method(self):
        scenario = br.benchmark_meta(
            "BM_PacketScenario/Memcpy/h8/p64",
            {"scenario": "Memcpy", "header": 8, "payload": 64})
        self.assertEqual(scenario["kind"], "scenario")
        self.assertEqual(scenario["method"], "Memcpy")

        method = br.benchmark_meta("BM_CopyAVX2_Fixed/256", {})
        self.assertEqual(method["kind"], "fixed")
        self.assertEqual(method["method"], "BM_CopyAVX2")
        self.assertEqual(method["size"], 256)

    def test_end_to_end_report_embeds_plot_and_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            stats_path = base / "stats.json"
            meta_path = base / "meta.json"
            plots_dir = base / "reports"
            plots_dir.mkdir()
            output = plots_dir / "report.html"

            stats_path.write_text(json.dumps(stats_fixture()), encoding="utf-8")
            meta_path.write_text(json.dumps({
                "experiment": {
                    "name": "demo", "runs": 1,
                    "scenarios": ["Memcpy", "ComposedPrefetch"],
                    "min_time": "0.05s",
                    "env": {
                        "taskset_cpus": [2],
                        "governor": "performance",
                        "no_turbo": True,
                    },
                },
                "run": {"benchmarks": 1},
                "system": {
                    "platform": "Linux", "machine": "x86_64",
                    "git_sha": "deadbeef",
                },
                "environment": {"applied": {"taskset_cpus": [2]}},
            }), encoding="utf-8")
            (plots_dir / "packet_scenario_time_cpu_mean_std.html").write_text(
                "<html><body>plot</body></html>", encoding="utf-8")
            (plots_dir / "packet_scenario_time_cpu_median_percentiles.html"
             ).write_text("<html><body>plot</body></html>", encoding="utf-8")

            code = br.main([
                str(stats_path), str(plots_dir),
                "--meta", str(meta_path), "--output", str(output),
            ])
            self.assertEqual(code, 0)
            report = output.read_text(encoding="utf-8")
            self.assertIn("System and environment", report)
            self.assertIn("Scenarios and measurements", report)
            self.assertIn("srcdoc=", report)
            self.assertIn("Packet Copy time vs Payload Size", report)
            self.assertIn("libc memcpy (baseline)", report)
            self.assertIn(
                "AVX2 + 4x unroll + prefetch", report)
            self.assertIn("CPUs 2", report)
            self.assertIn("governor performance", report)
            self.assertIn("turbo disabled", report)
            self.assertIn("mean ± std", report)
            self.assertIn("median and percentiles", report)
            self.assertIn("p05", report)
            self.assertIn("σ within", report)
            self.assertIn("demo warning", report)
            self.assertNotIn("embedded plots", report)
            self.assertIn("min_time", report)
            self.assertNotIn("deadbeef", report)


if __name__ == "__main__":
    unittest.main()
