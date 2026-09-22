"""Bounded subprocess streaming and POSIX process-group cleanup; never uses a shell."""

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path


async def run_process(
    argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    output: Callable[[bytes, bool], Awaitable[None]],
    deadline_seconds: float,
) -> int:
    if os.name != "posix":
        raise OSError("Execution requires POSIX process groups")
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    )

    async def drain(stream, stderr):
        # Bounded chunks, including a child emitting megabytes without a newline.
        while chunk := await stream.read(4096):
            await output(chunk, stderr)

    async def finish():
        async with asyncio.TaskGroup() as group:
            group.create_task(drain(process.stdout, False))
            group.create_task(drain(process.stderr, True))
            group.create_task(process.wait())

    async def cleanup():
        try:
            process = await spawning
        except Exception:
            return  # Spawn failed; no owned process exists.
        # Kill descendants even if the leader exited while a child retained its pipes.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            if sig == signal.SIGTERM:
                await asyncio.sleep(0.2)

        async def discard(stream):
            # Cancelled consumers can leave a paused pipe transport. Drain after
            # killing so Process.wait cannot hang behind pipe backpressure.
            if stream is not None:
                while await stream.read(4096):
                    pass

        await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())

    try:
        async with asyncio.timeout(deadline_seconds):
            # Cancellation during spawn still owns and cleans up the resulting child.
            process = await asyncio.shield(spawning)
            await finish()
        assert process.returncode is not None
        return process.returncode
    finally:
        cleaning = asyncio.create_task(cleanup())
        interrupted = False
        # Repeated cancellation must not release secrets/temp files before cleanup.
        while not cleaning.done():
            try:
                await asyncio.shield(cleaning)
            except asyncio.CancelledError:
                interrupted = True
                continue
        cleaning.result()
        if interrupted:
            raise asyncio.CancelledError
