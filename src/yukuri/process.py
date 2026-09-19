from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time

from .models import Invocation, ProcessResult


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if process.poll() is None:
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_process(invocation: Invocation, metadata: bool = False) -> ProcessResult:
    started = time.monotonic()
    meta_read = meta_write = None
    argv = list(invocation.argv)
    try:
        if metadata:
            meta_read, meta_write = os.pipe()
            argv.append(str(meta_write))
        process = subprocess.Popen(
            argv,
            cwd=invocation.cwd,
            env=invocation.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(() if meta_write is None else (meta_write,)),
            start_new_session=True,
        )
    except OSError as exc:
        if meta_read is not None:
            os.close(meta_read)
        if meta_write is not None:
            os.close(meta_write)
        return ProcessResult(argv=argv, elapsed=time.monotonic() - started, error=str(exc))

    if meta_write is not None:
        os.close(meta_write)
    selector = selectors.DefaultSelector()
    streams = {
        process.stdout: bytearray(),
        process.stderr: bytearray(),
    }
    if meta_read is not None:
        os.set_blocking(meta_read, False)
        streams[meta_read] = bytearray()
    for stream in streams:
        selector.register(stream, selectors.EVENT_READ)

    timed_out = output_limited = False
    total = 0
    while selector.get_map():
        remaining = invocation.timeout - (time.monotonic() - started)
        if remaining <= 0:
            timed_out = True
            _terminate_group(process)
            remaining = 0.05
        events = selector.select(min(max(remaining, 0.01), 0.1))
        for key, _ in events:
            stream = key.fileobj
            fd = stream if isinstance(stream, int) else stream.fileno()
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                selector.unregister(stream)
                if not isinstance(stream, int):
                    stream.close()
                else:
                    os.close(stream)
                continue
            room = max(0, invocation.max_output - total)
            streams[stream].extend(chunk[:room])
            total += len(chunk)
            if total > invocation.max_output and not output_limited:
                output_limited = True
                _terminate_group(process)
        if (timed_out or output_limited) and process.poll() is not None and not events:
            # Pipes normally close immediately; keep draining until EOF.
            pass

    returncode = process.wait()
    keys = list(streams)
    stdout = bytes(streams[keys[0]])
    stderr = bytes(streams[keys[1]])
    metadata_bytes = bytes(streams[keys[2]]) if meta_read is not None else b""
    return ProcessResult(
        argv=argv,
        stdout=stdout,
        stderr=stderr,
        metadata=metadata_bytes,
        returncode=returncode,
        elapsed=time.monotonic() - started,
        timed_out=timed_out,
        output_limited=output_limited,
    )
