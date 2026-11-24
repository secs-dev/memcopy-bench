#!/usr/bin/env python3
"""Unit tests for tools/bench_stats.py (stdlib unittest, numpy/scipy)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bench_stats as bs  # noqa: E402


def observation(round_, name, cpu, real=None, warmup=False, status="ok",
                repetition=0, context=None, bytes_per_second=None):
    parsed = bs.parse_benchmark_name(name)
    rec = {
        "round": round_,
        "warmup": warmup,
        "name": name,
        "repetition": repetition,
        "status": status,
        "scenario": parsed[0] if parsed else None,
        "header": parsed[1] if parsed else None,
        "payload": parsed[2] if parsed else None,
        "bytes": (parsed[1] + parsed[2]) if parsed else None,
        "cpu_time_ns": cpu,
        "real_time_ns": cpu if real is None else real,
        "context": context or {},
    }
    if bytes_per_second is not None:
        rec["bytes_per_second"] = bytes_per_second
    if status != "ok":
        rec["reason"] = status
    return rec


class PercentileTests(unittest.TestCase):
    def test_matches_numpy_linear(self):
        values = list(range(1, 11))
        got = bs.percentile(values, [5, 25, 50, 75, 95])
        expected = np.percentile(values, [5, 25, 50, 75, 95],
                                 method="linear")
        for key, want in zip(["p05", "p25", "p50", "p75", "p95"], expected):
            self.assertAlmostEqual(got[key], float(want))

    def test_empty_and_non_finite(self):
        got = bs.percentile([], [5, 50])
        self.assertIsNone(got["p05"])
        self.assertIsNone(got["p50"])
        got = bs.percentile([float("nan"), 1.0, float("inf")], [50])
        self.assertAlmostEqual(got["p50"], 1.0)

    def test_fractional_key(self):
        got = bs.percentile([1.0, 2.0, 3.0], [2.5])
        self.assertIn("p2.5", got)


class SummarizeTests(unittest.TestCase):
    def test_single_value_has_no_ci(self):
        summary = bs.summarize([3.0], [50])
        self.assertEqual(summary["n"], 1)
        self.assertEqual(summary["std"], 0.0)
        self.assertIsNone(summary["ci95_mean"])
        self.assertEqual(summary["median"], 3.0)

    def test_identical_values_zero_width_ci(self):
        summary = bs.summarize([5.0, 5.0, 5.0, 5.0], [50])
        self.assertEqual(summary["ci95_mean"], [5.0, 5.0])
        self.assertEqual(summary["std"], 0.0)

    def test_ci_contains_mean(self):
        summary = bs.summarize([1.0, 2.0, 3.0, 4.0, 5.0], [50])
        low, high = summary["ci95_mean"]
        self.assertLess(low, summary["mean"])
        self.assertGreater(high, summary["mean"])


class TrimTests(unittest.TestCase):
    def test_none_keeps_everything(self):
        kept, dropped = bs.apply_trim([1, 2, 3, 1000], "none")
        self.assertEqual(len(kept), 4)
        self.assertEqual(dropped, [])

    def test_iqr_drops_outlier(self):
        kept, dropped = bs.apply_trim([10, 10.1, 10.2, 10.3, 1000], "iqr")
        self.assertNotIn(1000, kept)
        self.assertEqual(dropped[0][1], "iqr_outlier")

    def test_mad_drops_outlier(self):
        kept, dropped = bs.apply_trim([10, 10.1, 10.2, 10.3, 1000], "mad")
        self.assertNotIn(1000, kept)
        self.assertEqual(dropped[0][1], "mad_outlier")

    def test_never_trims_to_empty(self):
        kept, dropped = bs.apply_trim([1, 1, 1, 1], "iqr")
        self.assertTrue(kept)
        self.assertEqual(dropped, [])


class BuildStatsTests(unittest.TestCase):
    def setUp(self):
        self.name = "BM_PacketScenario/Memcpy/h8/p64"
        self.other = "BM_PacketScenario/Small/h8/p64"
        self.observations = []
        for rnd, value in enumerate([3.0, 3.2, 2.8]):
            self.observations.append(
                observation(rnd, self.name, value, real=value * 1.1,
                            context={"num_cpus": 8, "load_avg": [0.1],
                                     "caches": [{"level": 1, "size": 32768,
                                                 "type": "Data"}]}))
        # Same benchmark, second repetition in round 0.
        self.observations.append(
            observation(0, self.name, 3.1, real=3.4, repetition=1))
        # Benchmark that only appears in one round: unequal n.
        self.observations.append(observation(0, self.other, 9.0))
        # Warm-up must not contribute.
        self.observations.append(
            observation(3, self.name, 1000.0, warmup=True))
        # Failed launch must be reported as a gap.
        self.observations.append(
            observation(1, None, None, status="timeout"))

    def build(self, stats_cfg=None, runs=3, repetitions=2):
        cfg = {"name": "unit", "runs": runs, "repetitions": repetitions,
               "stats": stats_cfg or {}}
        return bs.build_stats(self.observations, cfg)

    def test_structure_and_warmup(self):
        stats = self.build()
        self.assertEqual(stats["schema"], bs.SCHEMA)
        self.assertEqual(stats["totals"]["warmup_observations"], 1)
        self.assertEqual(stats["totals"]["observations"], 5)
        self.assertIn(self.name, stats["benchmarks"])
        entry = stats["benchmarks"][self.name]
        self.assertEqual(entry["scenario"], "Memcpy")
        self.assertEqual(entry["header"], 8)
        self.assertEqual(entry["payload"], 64)
        self.assertEqual(entry["bytes"], 72)
        self.assertEqual(entry["n"], 4)
        self.assertEqual(entry["metrics"]["cpu"]["time_ns"]["n"], 4)

    def test_median_and_rounds(self):
        stats = self.build()
        block = stats["benchmarks"][self.name]["metrics"]["cpu"]
        self.assertAlmostEqual(block["time_ns"]["median"],
                               float(np.median([3.0, 3.1, 3.2, 2.8])))
        self.assertEqual(sorted(block["per_round_median_ns"]), ["0", "1", "2"])
        self.assertAlmostEqual(block["per_round_median_ns"]["0"], 3.05)
        self.assertGreater(block["sigma_between_ns"], 0.0)

    def test_throughput_is_time_transform(self):
        stats = self.build()
        block = stats["benchmarks"][self.name]["metrics"]["cpu"]
        time_summary = block["time_ns"]
        tp = block["throughput_gbps"]
        expected = 72 / time_summary["median"]
        self.assertAlmostEqual(tp["median"], expected, places=3)
        # Monotone transform: higher throughput corresponds to lower time.
        self.assertAlmostEqual(tp["p05"], 72 / time_summary["p95"],
                               places=3)
        self.assertAlmostEqual(tp["p95"], 72 / time_summary["p05"],
                               places=3)

    def test_gaps_and_warnings(self):
        stats = self.build()
        self.assertEqual(len(stats["gaps"]), 1)
        self.assertEqual(stats["gaps"][0]["reason"], "timeout")
        joined = " ".join(stats["warnings"])
        self.assertIn("expected observations", joined)
        self.assertIn(self.other, joined)

    def test_load_average_warning(self):
        observations = [
            observation(i, self.name, 3.0,
                        context={"num_cpus": 4, "load_avg": [3.0]})
            for i in range(3)
        ]
        stats = bs.build_stats(
            observations,
            {"name": "load", "runs": 3, "stats": {"load_avg_threshold": 0.25}})
        self.assertTrue(
            any("load_avg" in warning for warning in stats["warnings"]))

    def test_quiet_load_average(self):
        stats = self.build()
        self.assertFalse(
            any("load_avg" in warning for warning in stats["warnings"]))

    def test_discarded_outliers_are_recorded(self):
        stats = self.build({"trim": "iqr"}, runs=5, repetitions=1)
        # The other benchmark has a single sample: nothing to trim.
        self.assertEqual(stats["benchmarks"][self.other]["discarded"], [])

    def test_bytes_per_second_cross_check(self):
        observations = [observation(
            0, self.name, 3.0, bytes_per_second=1.0) for _ in range(2)]
        stats = bs.build_stats(observations,
                               {"name": "bps", "runs": 2, "repetitions": 1})
        self.assertTrue(
            any("bytes_per_second" in w for w in stats["warnings"]))

    def test_contexts_preserved(self):
        stats = self.build()
        self.assertEqual(stats["environment"]["num_cpus"], 8)
        self.assertEqual(stats["environment"]["caches"][0]["level"], 1)


class ReadMeasurementsTests(unittest.TestCase):
    def test_malformed_lines_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "measurements.jsonl"
            path.write_text(
                '{"name": "a"}\n\nnot json\n[1, 2]\n{"name": "b"}\n',
                encoding="utf-8")
            observations, warnings = bs.read_measurements(path)
            self.assertEqual([o["name"] for o in observations], ["a", "b"])
            self.assertEqual(len(warnings), 2)

    def test_missing_file(self):
        observations, warnings = bs.read_measurements("/nope/missing.jsonl")
        self.assertEqual(observations, [])
        self.assertTrue(warnings)


class OutputTests(unittest.TestCase):
    def test_csv_and_json_roundtrip(self):
        observations = [observation(0, "BM_PacketScenario/Memcpy/h8/p64",
                                    3.0) for _ in range(3)]
        stats = bs.build_stats(observations,
                               {"name": "out", "runs": 3, "repetitions": 1})
        with tempfile.TemporaryDirectory() as tmp:
            json_path = bs.write_stats_json(stats, Path(tmp) / "stats.json")
            csv_path = bs.write_stats_csv(stats, Path(tmp) / "stats.csv")
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema"], bs.SCHEMA)
            header = csv_path.read_text(encoding="utf-8").splitlines()[0]
            self.assertTrue(header.startswith("name,scenario,header"))

    def test_format_summary(self):
        observations = [observation(0, "BM_PacketScenario/Memcpy/h8/p64",
                                    3.0) for _ in range(3)]
        stats = bs.build_stats(observations,
                               {"name": "fmt", "runs": 3, "repetitions": 1})
        text = bs.format_summary(stats)
        self.assertIn("BM_PacketScenario/Memcpy/h8/p64", text)


if __name__ == "__main__":
    unittest.main()
