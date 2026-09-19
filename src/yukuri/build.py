from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import tempfile

from .models import Invocation, ProcessResult
from .process import run_process


@dataclass
class BuildResult:
    root: Path
    archive: Path | None
    process: ProcessResult | None
    error: str | None = None


class BuildWorkspace:
    def __init__(self, keep: bool):
        self.keep = keep
        self._temp: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None

    def __enter__(self) -> "BuildWorkspace":
        if self.keep:
            self.root = Path(tempfile.mkdtemp(prefix="42check-"))
        else:
            self._temp = tempfile.TemporaryDirectory(prefix="42check-")
            self.root = Path(self._temp.name)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._temp is not None:
            self._temp.cleanup()


def prepare_project(
    project: Path,
    workspace: Path,
    archive_override: Path | None,
    timeout: float,
    max_output: int,
) -> BuildResult:
    destination = workspace / "project"
    try:
        shutil.copytree(project, destination, symlinks=True)
    except OSError as exc:
        return BuildResult(destination, None, None, f"could not copy project: {exc}")

    if archive_override is not None:
        archive = archive_override.resolve()
        if not archive.is_file():
            return BuildResult(destination, None, None, f"archive does not exist: {archive}")
        return BuildResult(destination, archive, None)

    makefile = destination / "Makefile"
    if not makefile.is_file():
        return BuildResult(destination, None, None, "project has no Makefile")
    env = os.environ.copy()
    process = run_process(Invocation(["make"], destination, timeout, max_output, env))
    if process.error:
        return BuildResult(destination, None, process, f"could not run make: {process.error}")
    if process.timed_out:
        return BuildResult(destination, None, process, "build timed out")
    if process.output_limited:
        return BuildResult(destination, None, process, "build output exceeded capture limit")
    if process.returncode != 0:
        return BuildResult(destination, None, process, f"make exited with {process.returncode}")
    archive = destination / "libftprintf.a"
    if not archive.is_file():
        return BuildResult(destination, None, process, "make did not produce libftprintf.a")
    return BuildResult(destination, archive, process)


def compile_harness(
    source: Path,
    output: Path,
    archive: Path | None,
    cwd: Path,
    timeout: float,
    max_output: int,
) -> ProcessResult:
    compiler = shlex.split(os.environ.get("CC", "cc"))
    argv = compiler + [
        "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-fno-builtin", "-fno-builtin-printf",
    ]
    if archive is not None:
        argv.append("-DSTUDENT_HARNESS")
    argv.append(str(source))
    if archive is not None:
        argv.append(str(archive))
    argv.extend([
        "-o", str(output),
    ])
    return run_process(Invocation(argv, cwd, timeout, max_output, os.environ.copy()))
