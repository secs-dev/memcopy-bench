#!/usr/bin/env python3
"""Unit tests for environment topology and niceness support."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_experiment as rx  # noqa: E402


def base_config(**env):
    cfg = {**rx.DEFAULT_CONFIG, "name": "test", "scenarios": ["Memcpy"]}
    cfg["env"] = {**rx.DEFAULT_CONFIG["env"], **env}
    return cfg


class CpuListTests(unittest.TestCase):
    def test_ranges_and_single_cpus(self):
        self.assertEqual(rx.parse_cpu_list("0-3,6,8-9"),
                         [0, 1, 2, 3, 6, 8, 9])
        self.assertEqual(rx.parse_cpu_list("2"), [2])
        self.assertEqual(rx.parse_cpu_list(""), [])
        self.assertEqual(rx.parse_cpu_list("0-2,bad,4"), [0, 1, 2, 4])

    def test_nice_config_bounds(self):
        self.assertEqual(rx.validate_config(base_config(nice=-5)), [])
        self.assertEqual(rx.validate_config(base_config(nice=19)), [])
        self.assertTrue(rx.validate_config(base_config(nice=20)))
        self.assertTrue(rx.validate_config(base_config(nice=-21)))
        self.assertTrue(rx.validate_config(base_config(nice="high")))


class LinuxTopologyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        cpu = root / "cpu"
        node = root / "node"
        (cpu / "smt").mkdir(parents=True)
        (node / "node0").mkdir(parents=True)
        (cpu / "online").write_text("0-3\n", encoding="utf-8")
        (cpu / "isolated").write_text("3\n", encoding="utf-8")
        (cpu / "smt" / "control").write_text("on\n", encoding="utf-8")
        (node / "node0" / "cpulist").write_text("0-3\n", encoding="utf-8")
        topology = {
            0: (0, [0, 1]),
            1: (0, [0, 1]),
            2: (1, [2, 3]),
            3: (1, [2, 3]),
        }
        for cpu_id, (core_id, siblings) in topology.items():
            topo = cpu / f"cpu{cpu_id}" / "topology"
            topo.mkdir(parents=True)
            (topo / "core_id").write_text(str(core_id), encoding="utf-8")
            (topo / "physical_package_id").write_text("0", encoding="utf-8")
            (topo / "thread_siblings_list").write_text(
                ",".join(str(s) for s in siblings), encoding="utf-8")
        (root / "cmdline").write_text(
            "BOOT_IMAGE=/vmlinuz isolcpus=3\n", encoding="utf-8")

        self.adapter = rx.LinuxEnvironment()
        self.adapter.CPU_BASE = cpu
        self.adapter.NODE_BASE = node
        self.adapter.PROC_CMDLINE = root / "cmdline"
        self.adapter.CGROUP_V2_CPUSET = root / "missing-v2"
        self.adapter.CGROUP_V1_CPUSET = root / "missing-v1"

    def tearDown(self):
        self.tmp.cleanup()

    def test_topology_reports_smt_siblings_and_isolation(self):
        topo = self.adapter.topology_info([1, 2])
        self.assertTrue(topo["available"])
        self.assertEqual(topo["online_cpus"], [0, 1, 2, 3])
        self.assertEqual(topo["isolated_cpus"], [3])
        self.assertEqual(topo["isolcpus_boot"], "3")
        self.assertEqual(topo["smt_control"], "on")
        self.assertEqual(topo["numa_nodes"], {"node0": [0, 1, 2, 3]})
        self.assertEqual(topo["pinned"]["siblings"]["1"], [0, 1])
        self.assertTrue(topo["pinned"]["all_online"])
        self.assertFalse(topo["pinned"]["any_isolated"])
        self.assertEqual(topo["pinned"]["co_located_cpus"], [])

    def test_topology_detects_co_located_pinned_cpus(self):
        topo = self.adapter.topology_info([0, 1])
        self.assertEqual(topo["pinned"]["co_located_cpus"], [0, 1])

    def test_topology_handles_missing_sysfs(self):
        self.adapter.CPU_BASE = Path(self.tmp.name) / "missing"
        self.assertEqual(self.adapter.topology_info([0]),
                         {"source": "sysfs", "available": False})


class NiceTests(unittest.TestCase):
    def test_preexec_hook_is_posix_only(self):
        if os.name != "posix":
            self.assertIsNone(rx._nice_preexec(5))
            return
        self.assertIsNone(rx._nice_preexec(None))
        self.assertTrue(callable(rx._nice_preexec(5)))

    def test_child_inherits_requested_nice(self):
        if os.name != "posix" or not hasattr(os, "nice"):
            self.skipTest("POSIX niceness is unavailable")
        parent = os.nice(0)
        expected = min(parent + 5, 19)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nice.txt"
            code = (
                "import os, pathlib, sys; "
                f"pathlib.Path({str(target)!r}).write_text(str(os.nice(0)))"
            )
            rx.EnvironmentAdapter().run(
                [sys.executable, "-c", code], [], nice=5)
            self.assertEqual(target.read_text(encoding="utf-8"), str(expected))

    def test_nice_is_recorded_in_applied_environment(self):
        if os.name != "posix" or not hasattr(os, "nice"):
            self.skipTest("POSIX niceness is unavailable")
        result = rx.LinuxEnvironment().apply({"nice": 5})
        self.assertEqual(result["applied"]["nice"], 5)
        self.assertNotIn("nice", result["skipped"])

    def test_launch_reports_start_failures(self):
        class FailingAdapter(rx.EnvironmentAdapter):
            def run(self, cmd, cpus, timeout=None, nice=None):
                raise subprocess.SubprocessError("preexec failed")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            cfg = base_config(nice=-20)
            ok, reason = rx.launch(FailingAdapter(), "bench", cfg, out, None)
            self.assertFalse(ok)
            self.assertIn("failed to start", reason)


if __name__ == "__main__":
    unittest.main()
