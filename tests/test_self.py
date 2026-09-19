from __future__ import annotations

import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "42check"
FIXTURES = ROOT / "tests" / "fixtures"


class RunnerSelfTests(unittest.TestCase):
    maxDiff = None

    def run_check(self, fixture: str, *arguments: str, cwd: Path | None = None):
        project = FIXTURES / fixture
        return subprocess.run(
            [str(COMMAND), "ft_printf", str(project), *arguments],
            cwd=cwd or ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

    def test_good_fixture_full_json_and_space_path(self):
        with tempfile.TemporaryDirectory(prefix="42check self test ") as directory:
            root = Path(directory)
            project = root / "project with spaces"
            report_path = root / "result with spaces.json"
            shutil.copytree(FIXTURES / "good", project)
            result = subprocess.run(
                [str(COMMAND), "ft_printf", str(project), "--profile", "full",
                 "--seed", "19", "--json", str(report_path)],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["counts"], {"PASS": len(report["results"])})
            self.assertGreaterEqual(len(report["results"]), 35)
            self.assertEqual(report["seed"], 19)

    def test_wrong_return_and_exact_reproduction(self):
        result = self.run_check("bad_return", "--case", "text.plain")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("return: expected 9, actual 10", result.stdout)
        line = next(line for line in result.stdout.splitlines() if "reproduce:" in line)
        reproduction = shlex.split(line.split("reproduce:", 1)[1].strip())
        repeated = subprocess.run(reproduction, cwd=ROOT, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(repeated.returncode, 1, repeated.stdout + repeated.stderr)
        self.assertIn("text.plain", repeated.stdout)

    def test_embedded_nul_is_compared_as_bytes(self):
        result = self.run_check("bad_nul", "--case", "char.nul")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("expected[3]", result.stdout)
        self.assertIn(r'"A\x00B"', result.stdout)
        self.assertIn("actual[2]", result.stdout)

    def test_wrong_numeric_output(self):
        result = self.run_check("bad_numeric", "--case", "signed.values")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("first difference", result.stdout)

    def test_crash_isolated_and_suite_continues(self):
        result = self.run_check("crash", "--profile", "smoke")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertGreater(result.stdout.count("CRASH"), 1)
        self.assertIn("state.multiple_calls", result.stdout)

    def test_timeout_isolated_and_suite_continues(self):
        result = self.run_check("hang", "--profile", "smoke", "--timeout", "0.05")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertGreater(result.stdout.count("TIMEOUT"), 1)
        self.assertIn("state.multiple_calls", result.stdout)

    def test_output_limit(self):
        result = self.run_check("excessive", "--case", "text.empty", "--max-output", "8192")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("OUTPUT_LIMIT", result.stdout)
        self.assertIn("exceeded 8192 bytes", result.stdout)

    def test_build_error_is_infrastructure_exit(self):
        result = self.run_check("build_error")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("BUILD_ERROR", result.stdout)
        self.assertIn("make exited", result.stdout)

    def test_list_and_filter_do_not_build(self):
        result = self.run_check("build_error", "--list", "--filter", "pointer")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pointer.object", result.stdout)
        self.assertNotIn("text.plain", result.stdout)

    def test_explicit_archive_skips_project_makefile(self):
        with tempfile.TemporaryDirectory(prefix="42check archive ") as directory:
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
                 "--archive", str(library_project / "libftprintf.a")],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
