from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys

from .models import RunReport, Status, TestResult, escape_bytes


class Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def status(self, status: Status) -> str:
        colors = {
            Status.PASS: "32", Status.SKIP: "33", Status.FAIL: "31",
            Status.CRASH: "31", Status.TIMEOUT: "31", Status.OUTPUT_LIMIT: "31",
            Status.BUILD_ERROR: "35", Status.HARNESS_ERROR: "35",
        }
        return self.paint(status.value, colors[status])


def first_difference(expected: bytes, actual: bytes) -> int | None:
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            return index
    if len(expected) != len(actual):
        return min(len(expected), len(actual))
    return None


def render(report: RunReport, verbose: bool = False) -> None:
    palette = Palette(sys.stdout.isatty() and "NO_COLOR" not in os.environ)
    print(f"42check {report.suite}  profile={report.profile} seed={report.seed}")
    category = None
    for result in report.results:
        if result.category != category:
            category = result.category
            print(f"\n[{category}]")
        print(f"  {palette.status(result.status):<18} {result.case_id}: {result.description}")
        if result.status not in (Status.PASS, Status.SKIP) or verbose:
            _render_detail(result)
    counts: dict[Status, int] = {}
    for result in report.results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = " ".join(f"{status.value}={counts[status]}" for status in Status if status in counts)
    print(f"\n{summary}")
    if report.artifacts:
        print(f"Artifacts: {report.artifacts}")


def _render_detail(result: TestResult) -> None:
    if result.format_display:
        print(f"      format: {result.format_display}")
    if result.arguments:
        print(f"      arguments: {result.arguments}")
    if result.expected is not None:
        print(f"      expected[{len(result.expected)}]: {escape_bytes(result.expected)}")
    if result.actual is not None:
        print(f"      actual[{len(result.actual)}]:   {escape_bytes(result.actual)}")
    if result.expected is not None and result.actual is not None:
        difference = first_difference(result.expected, result.actual)
        if difference is not None:
            exp = "EOF" if difference >= len(result.expected) else f"0x{result.expected[difference]:02x}"
            act = "EOF" if difference >= len(result.actual) else f"0x{result.actual[difference]:02x}"
            print(f"      first difference: byte {difference} (expected {exp}, actual {act})")
    if result.expected_return is not None or result.actual_return is not None:
        print(f"      return: expected {result.expected_return}, actual {result.actual_return}")
    if result.signal is not None:
        try:
            name = signal.Signals(result.signal).name
        except ValueError:
            name = "unknown"
        print(f"      signal: {result.signal} ({name})")
    if result.stderr:
        print(f"      stderr[{len(result.stderr)}]: {escape_bytes(result.stderr)}")
    if result.diagnostic:
        print(f"      diagnostic: {result.diagnostic}")
    if result.reproduction:
        print(f"      reproduce: {result.reproduction}")


def write_json(report: RunReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.json_dict(), indent=2) + "\n", encoding="utf-8")
