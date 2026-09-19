# yukuri / `42check`

`42check` is an original, small CLI test runner for 42 projects. Its first
implemented suite is `ft_printf`. It builds a disposable copy of a student
project, links `libftprintf.a` to a generated C harness, and compares exact
stdout bytes and return values with the host C library.

The broad command-oriented workflow was conceptually inspired by
[mini-moulinette](https://github.com/koharu-u/mini-moulinette). No source,
tests, scripts, wording, assets, or internal architecture from that project
were used.

## Quick start

Python 3.10 or newer, `make`, and a C compiler are required.

```sh
./42check ft_printf /path/to/project
./42check ft_printf . --profile smoke
./42check ft_printf . --profile full --seed 42
./42check ft_printf . --filter pointer
./42check ft_printf . --case pointer.object
./42check ft_printf . --list
./42check ft_printf . --json results.json
```

Install the command in a virtual environment with:

```sh
python3 -m pip install .
42check --help
```

An archive outside the project, or one with a nonstandard name, can be used
with `--archive /path/to/archive.a`. This skips `make`. `--timeout` controls
each test process, `--build-timeout` controls build and link steps,
`--max-output` bounds captured stdout and stderr, `--verbose` prints build and
diagnostic details, and `--keep-artifacts` retains the temporary build tree
plus byte-exact output files for failures.

Exit status `0` means all required selected tests passed. `1` means the
student implementation failed, crashed, timed out, or exceeded the output
limit. `2` means the build, harness, oracle, configuration, or runner failed.
Optional compatibility checks are reported separately and do not change the
exit status.

## What the suite covers

The default `full` profile tests valid uses of `%c`, `%s`, `%p`, `%d`, `%i`,
`%u`, `%x`, `%X`, and `%%`, plus ordinary text, mixed conversions, repeated
calls, bounded long output, and deterministic generated cases. The `smoke`
profile is a fast representative subset. Generated cases use only correctly
typed arguments and record the seed and generator version.

The repository did not contain a subject document when this suite was
created. The required scope above therefore follows the conventional
mandatory `ft_printf` interface `int ft_printf(const char *, ...)`, rather
than claiming to reproduce any official evaluator. Invalid formats, missing
or mismatched arguments, invalid pointers, flags, width, and precision are
excluded. A null `%s` case is labeled optional because C does not define it.
Null `%p` formatting is host-library-specific and is compared with the local
host. Pointer cases map the same valid virtual address in the reference and
student processes so ASLR cannot change the expected text.

Linux is the verified target. The runner uses standard tool lookup and honors
`CC`, which keeps it friendly to NixOS shells. It has no runtime Python
dependencies. The fixed-address pointer setup and POSIX process-group
supervision are Linux/POSIX assumptions; other platforms are not claimed.

## Architecture

`cli.py` owns argument parsing and orchestration. `build.py` creates and
builds the disposable copy. `process.py` supervises process groups and
captures bounded binary streams. `report.py` compares and renders results.
`registry.py` contains the deliberately small suite registry. Project code
lives under `suites/`; the `ft_printf` module defines cases and emits the C
harness with direct, correctly typed variadic calls.

To add a future suite, implement a suite object with `cases`, `list_cases`, and
`run` methods, then register that one object in `registry.py`. Add genuinely
suite-specific CLI options in `cli.py` when needed. Suite code owns its build
contract and cases; shared subprocess and report structures stay in the core
modules. Empty placeholder suites are intentionally absent.

## Testing the tester

```sh
python3 -m unittest discover -s tests -v
```

The end-to-end tests build original fixtures for a correct libc adapter,
wrong return value, lost embedded NUL, wrong numeric output, crash, hang,
excessive output, and build failure. They also cover a path containing spaces,
JSON output, continued execution after faults, and exact case reproduction.

This tool reports observations, not an official 42 grade. Passing cannot
guarantee success under a school evaluator or on a different libc/platform.
