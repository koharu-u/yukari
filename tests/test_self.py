from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "yukari"
FIXTURES = ROOT / "tests" / "fixtures"


class RunnerSelfTests(unittest.TestCase):
    maxDiff = None

    def run_check(self, fixture: str, *arguments: str, env: dict[str, str] | None = None):
        command_env = os.environ.copy()
        command_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env:
            command_env.update(env)
        return subprocess.run(
            [str(COMMAND), "ft_printf", str(FIXTURES / fixture),
             "--color", "never", *arguments],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=45, env=command_env,
        )

    def test_good_fixture_full_json_and_space_path(self):
        with tempfile.TemporaryDirectory(prefix="yukari self test ") as directory:
            root = Path(directory)
            project = root / "project with spaces"
            report_path = root / "result with spaces.json"
            shutil.copytree(FIXTURES / "good", project)
            result = subprocess.run(
                [str(COMMAND), "ft_printf", str(project), "--profile", "full",
                 "--seed", "19", "--color", "never", "--json", str(report_path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(report_path.read_text())
            required = [item for item in report["results"] if item["kind"] == "required"]
            self.assertTrue(all(item["status"] == "PASS" for item in required))
            self.assertGreaterEqual(len(required), 90)
            self.assertGreater(report["assertions"], len(required) * 2)
            self.assertEqual(report["seed"], 19)
            self.assertEqual(report["generator_version"], "2")

    def test_legacy_42check_command_remains_compatible(self):
        result = subprocess.run(
            [str(ROOT / "42check"), "ft_printf", str(FIXTURES / "good"),
             "--case", "text.plain", "--color", "never"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_compact_plain_output_is_stable_and_hides_passes(self):
        result = self.run_check("good", "--profile", "smoke", "--ascii")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Required tests", result.stdout)
        self.assertIn("Overall  PASSED", result.stdout)
        self.assertNotIn("text.empty", result.stdout)
        self.assertNotIn("\r", result.stdout)
        self.assertNotIn("\x1b", result.stdout)

    def test_wrong_return_and_exact_reproduction(self):
        result = self.run_check("bad_return", "--case", "text.plain")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL  text.plain", result.stdout)
        self.assertIn("expected   9 bytes, return 9", result.stdout)
        self.assertIn("actual     9 bytes, return 10", result.stdout)
        line = next(line for line in result.stdout.splitlines() if "rerun" in line)
        reproduction = shlex.split(line.split("rerun", 1)[1].strip())
        repeated = subprocess.run(reproduction, cwd=ROOT, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(repeated.returncode, 1, repeated.stdout + repeated.stderr)

    def test_embedded_nul_and_return_count_are_binary_safe(self):
        result = self.run_check("bad_nul", "--case", "char.nul")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("expected   3 bytes, return 3", result.stdout)
        self.assertIn("actual     2 bytes, return 2", result.stdout)
        self.assertIn(r'"A\x00B"', result.stdout)

    def test_signed_unsigned_and_hex_boundary_faults(self):
        for case_id in ("signed.bounds", "unsigned.core", "hex.values"):
            with self.subTest(case_id=case_id):
                result = self.run_check("bad_boundaries", "--case", case_id)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"FAIL  {case_id}", result.stdout)

    def test_state_leak_checks_each_call_return(self):
        result = self.run_check("state_leak", "--case", "state.multiple_calls")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("call returns", result.stdout)
        self.assertIn("expected [7, 0, 10], actual [7, 0, 4]", result.stdout)

    def test_crash_isolated_and_suite_continues(self):
        result = self.run_check("crash", "--profile", "smoke")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertGreater(result.stdout.count("CRASH"), 1)
        self.assertIn("long.pipe_capacity", result.stdout)

    def test_timeout_isolated_and_suite_continues(self):
        result = self.run_check("hang", "--profile", "smoke", "--timeout", "0.05")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertGreater(result.stdout.count("TIMEOUT"), 1)
        self.assertIn("long.pipe_capacity", result.stdout)

    def test_output_limit_is_reported_and_artifacts_preserved(self):
        result = self.run_check("excessive", "--case", "text.empty", "--max-output", "8192")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OUTPUT_LIMIT", result.stdout)
        artifact_line = next(line for line in result.stdout.splitlines() if line.startswith("artifacts  "))
        artifact_root = Path(artifact_line.split("  ", 1)[1])
        self.assertTrue((artifact_root / "failures" / "text.empty" / "actual.bin").is_file())

    def test_build_error_is_infrastructure_exit(self):
        result = self.run_check("build_error")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("BUILD_ERROR", result.stdout)
        self.assertIn("make exited", result.stdout)

    def test_list_filter_and_stable_ids(self):
        result = self.run_check("build_error", "--list", "--filter", "pointer")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pointer.object", result.stdout)
        self.assertNotIn("text.plain", result.stdout)

    def test_explicit_archive_skips_project_makefile(self):
        with tempfile.TemporaryDirectory(prefix="yukari archive ") as directory:
            root = Path(directory)
            library_project = root / "library"
            empty_project = root / "empty project"
            shutil.copytree(FIXTURES / "good", library_project)
            empty_project.mkdir()
            built = subprocess.run(["make", "-C", str(library_project)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run(
                [str(COMMAND), "ft_printf", str(empty_project), "--case", "text.plain",
                 "--archive", str(library_project / "libftprintf.a"), "--color", "never"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_json_stdout_keeps_human_output_on_stderr(self):
        result = self.run_check("good", "--case", "text.plain", "--json", "-")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["results"][0]["status"], "PASS")
        self.assertIn("yukari", result.stderr)
        self.assertNotIn("yukari ·", result.stdout)

    def test_required_compatibility_and_ub_results_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "ub.json"
            result = self.run_check("good", "--profile", "smoke", "--ub",
                                    "--json", str(report_path))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(report_path.read_text())
            self.assertEqual(set(report["section_counts"]), {"required", "compatibility", "ub"})
            self.assertTrue(all(item["status"] == "PASS" for item in report["results"]
                                if item["kind"] == "required"))
            self.assertTrue(all(item["status"] in {"SAME_OBSERVATION", "DIFFERENT_OBSERVATION"}
                                for item in report["results"] if item["kind"] == "ub"))
            self.assertIn("Undefined-behavior observations", result.stdout)

    def test_terminal_control_bytes_are_escaped(self):
        result = self.run_check("escape_output", "--case", "text.plain", "--ascii")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn(r"\x1b[31mINJECT\n\t\x01", result.stdout)

    def test_color_policy_and_no_color(self):
        color_env = os.environ.copy()
        color_env.pop("NO_COLOR", None)
        colored = subprocess.run(
            [str(COMMAND), "ft_printf", str(FIXTURES / "good"), "--case", "text.plain",
             "--color", "always"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30, env=color_env,
        )
        self.assertIn("\x1b[", colored.stdout)
        plain = self.run_check("good", "--case", "text.plain", env={"NO_COLOR": "1"})
        self.assertNotIn("\x1b[", plain.stdout)


if __name__ == "__main__":
    unittest.main()
