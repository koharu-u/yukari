# yukari

`yukari` is an original, dependency-free Python CLI test runner for 42
projects. Its first implemented suite is `ft_printf`. It builds a disposable
copy of a student project, links `libftprintf.a` into small generated C
harnesses, and compares exact stdout bytes and return values with the host C
library.

The broad command-oriented workflow was conceptually inspired by
[mini-moulinette](https://github.com/koharu-u/mini-moulinette). No source,
tests, scripts, wording, assets, or internal architecture from that project
were used.

## Quick start

Python 3.10 or newer, `make`, and a C compiler are required.

```sh
./yukari ft_printf /path/to/project
./yukari ft_printf . --profile smoke
./yukari ft_printf . --profile full --seed 42
./yukari ft_printf . --profile stress
./yukari ft_printf . --filter pointer
./yukari ft_printf . --case pointer.object
./yukari ft_printf . --list
./yukari ft_printf . --json results.json
```

Install the command in a virtual environment with:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
yukari --help
```

The `42check` executable remains available as a compatible alias for existing scripts.

The project should have a `Makefile` that creates `libftprintf.a`. To use an
existing archive and skip `make`:

```sh
yukari ft_printf . --archive /path/to/libftprintf.a
```

`--timeout` controls each isolated test, `--build-timeout` controls build and
link steps, and `--max-output` bounds captured process output. `--verbose`
shows successful cases and build diagnostics. `--keep-artifacts` retains the
temporary build tree and byte-exact failure files. Large or output-limited
failures preserve artifacts automatically.

## Profiles and selection

`smoke` is a fast representative set. `full` includes broad deterministic
boundaries and 24 seeded, boundary-biased generated cases. `stress` includes
the full deterministic suite, 80 generated cases, and a 1,024-call state and
capture test.

Case IDs are stable and can be combined with the seed for exact reproduction:

```sh
yukari ft_printf . --case char.nul
yukari ft_printf . --filter boundary
yukari ft_printf . --profile full --seed 2026
```

Generated cases use only valid formats and correctly typed arguments. Reports
record the seed, generator version, format, typed arguments, executed cases,
and actual assertion count.

## Terminal and JSON output

The normal display shows a compact project/profile/build header, one row per
category, expanded failures, and separate totals for required tests,
compatibility checks, and undefined-behavior observations. Passing cases are
shown individually only with `--verbose`.

Color defaults to interactive terminals and honors `NO_COLOR`:

```sh
yukari ft_printf . --color auto
yukari ft_printf . --color always
yukari ft_printf . --color never --ascii
```

The live progress line appears only on an interactive terminal. Redirected
output is stable and append-only. Displayed bytes and diagnostics escape NUL,
newlines, tabs, terminal escapes, and other control bytes.

Write JSON to a file, or use `-` for stdout. With JSON on stdout, all human
output and interactive progress go to stderr, so the stdout stream remains
valid JSON.

```sh
yukari ft_printf . --json report.json
yukari ft_printf . --json - | jq '.section_counts'
```

Exit status `0` means all required tests passed. `1` means a required test
failed, crashed, timed out, or exceeded the output limit. `2` means the build,
harness, oracle, configuration, or runner failed. Compatibility differences
and UB observation differences do not change a successful required-test exit
status.

## Defined-behavior coverage

The suite covers `%c`, `%s`, `%p`, `%d`, `%i`, `%u`, `%x`, `%X`, and `%%`.
Highlights include:

- every value from zero through `UCHAR_MAX`, with individual return checks;
- NUL bytes at the beginning, middle, and end, including repeated NULs;
- string sizes around powers of two and common buffer boundaries through
  4,097 bytes, UTF-8 bytes, embedded input NULs, substrings, repeated strings,
  and a terminator immediately before a protected page;
- `INT_MIN`, `INT_MAX`, their neighbors, `-1`, `0`, `1`, and safe neighbors of
  every representable power of ten through both signed conversions;
- unsigned neighbors of powers of 2, 10, and 16, alternating bits, the highest
  bit, `UINT_MAX`, and paired lowercase/uppercase hexadecimal output;
- live stack, heap, and static objects, repeated and distinct pointers,
  interior and one-past pointers, and a null pointer;
- dense mixed conversions, percent adjacency, repeated calls with an empty
  call between them, and output larger than typical pipe capacity.

Integer ranges come from the compiler's `limits.h`; the suite does not assume
32-bit `int`. For defined pointer cases, libc is captured immediately before
the student call in the same isolated process. Both calls therefore receive
the exact same real stack, heap, static, interior, or one-past pointer value,
independent of ASLR.

The repository did not contain a project subject when the suite was created.
The required scope follows the conventional mandatory interface
`int ft_printf(const char *, ...)`; it does not claim to reproduce an official
evaluator. Flags, width, and precision remain outside the default scope.

Null `%p` spelling comes from the local host libc. Null `%s` is reported only
as a separate compatibility observation and never counts as required success.

## Undefined-behavior exploration

Undefined behavior is excluded from ordinary `smoke`, `full`, and `stress`
runs. Add `--ub` to observe isolated probes:

```sh
yukari ft_printf . --profile smoke --ub
yukari ft_printf . --ub --case ub.trailing_percent
```

The current probes cover a null format pointer, trailing percent, unsupported
conversion, missing or mismatched variadic arguments, a nonterminated string
at a protected-page boundary, and an inaccessible string pointer. The null
`%s` input remains solely in compatibility checks to avoid a duplicate and
confusing report.

Libc and student probes run in separate disposable processes with independent
timeouts, output limits, process groups, stdout, stderr, and return metadata.
Reports use observational labels such as `RETURNED`, `SIGNAL`, `TIMEOUT`,
`SAME_OBSERVATION`, and `DIFFERENT_OBSERVATION`. They never call a UB result a
pass or failure. Matching libc does not establish correctness: results may
change with compiler, optimization, libc, environment, and execution.

Sanitizer messages already present in captured stderr are identified. A
prebuilt student archive is ordinarily uninstrumented, so this is not complete
sanitizer coverage. UBSan also cannot detect every form of undefined behavior.

## Architecture and portability

`cli.py` owns arguments and stream routing. `build.py` creates the disposable
copy and links harnesses. `process.py` supervises process groups and captures
bounded binary streams. `report.py` owns terminal and JSON presentation.
`registry.py` is the small suite registry. `suites/ft_printf.py` defines cases,
generators, direct typed calls, and C harness sources.

To add a future suite, implement `cases`, `list_cases`, and `run`, register the
suite in `registry.py`, and add genuinely suite-specific CLI options only when
needed. Shared build, process, result, and reporting structures stay in the
core modules.

Linux is the verified target. Tool lookup honors `CC`, including NixOS shell
environments. The process-group supervision and guarded-page cases require
POSIX/Linux facilities (`mmap` and `mprotect`); unsupported setup is reported
as an infrastructure error rather than a student failure. Other platforms are
not claimed.

## Testing the tester

```sh
python3 -m unittest discover -s tests -v
```

The original fixtures include a correct libc-backed adapter plus faulty
implementations for NUL output and return counts, signed and unsigned
boundaries, hexadecimal case, retained state, incorrect general returns,
crash, hang, excessive output, terminal-control injection, and build failure.
The end-to-end checks also cover continued execution, spaces in paths, archive
overrides, exact reproduction, valid JSON stdout, color policy, stable plain
output, artifacts, and separation of the three result sections.

This tool reports observations, not an official 42 grade. Passing cannot
guarantee success under a school evaluator or on a different libc/platform.
