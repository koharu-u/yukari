from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .registry import SUITES
from .report import render, write_json


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="42check",
        description="Build and test a 42 project without modifying its source tree.",
    )
    result.add_argument("suite", choices=sorted(SUITES), help="project suite to run")
    result.add_argument("project", type=Path, help="student project directory")
    result.add_argument("--profile", choices=("smoke", "full"), default="full",
                        help="case set to run (default: full)")
    selection = result.add_mutually_exclusive_group()
    selection.add_argument("--filter", metavar="TEXT", help="select IDs/categories/descriptions containing text")
    selection.add_argument("--case", metavar="CASE_ID", help="run one stable test ID")
    result.add_argument("--seed", type=int, default=42, help="generated-case seed (default: 42)")
    result.add_argument("--list", action="store_true", help="list selected cases without building")
    result.add_argument("--json", type=Path, metavar="PATH", help="write a machine-readable report")
    result.add_argument("--archive", type=Path, help="use this libftprintf.a and skip make")
    result.add_argument("--timeout", type=float, default=2.0, help="seconds allowed per process")
    result.add_argument("--build-timeout", type=float, default=30.0, help="seconds allowed for build/link")
    result.add_argument("--max-output", type=int, default=1_048_576,
                        help="maximum captured bytes per process (default: 1048576)")
    result.add_argument("--keep-artifacts", action="store_true", help="preserve the temporary build tree")
    result.add_argument("--verbose", action="store_true", help="show diagnostics for successful cases")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.command_name = sys.argv[0] if argv is None else "42check"
    if args.timeout <= 0 or args.build_timeout <= 0 or args.max_output <= 0:
        parser().error("timeouts and max-output must be positive")
    suite = SUITES[args.suite]
    if args.list:
        cases = suite.list_cases(args.profile, args.seed, args.filter)
        if args.case:
            cases = [case for case in suite.cases("full", args.seed) if case.case_id == args.case]
        for case in cases:
            marker = "required" if case.required else "optional"
            print(f"{case.case_id:<24} {case.category:<14} {marker:<8} {case.description}")
        return 0 if cases else 2
    if not args.project.is_dir():
        parser().error(f"project is not a directory: {args.project}")
    try:
        report, exit_code = suite.run(args)
    except Exception as exc:
        print(f"HARNESS_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    render(report, args.verbose)
    if args.verbose and (report.build_stdout or report.build_stderr):
        print("\n[build output]")
        if report.build_stdout:
            print(report.build_stdout, end="" if report.build_stdout.endswith("\n") else "\n")
        if report.build_stderr:
            print(report.build_stderr, end="" if report.build_stderr.endswith("\n") else "\n", file=sys.stderr)
    if args.json:
        try:
            write_json(report, args.json)
        except OSError as exc:
            print(f"42check: could not write JSON report: {exc}", file=sys.stderr)
            return 2
    return exit_code
