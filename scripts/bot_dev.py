"""Run the Telegram BOT with Python-only development reloads."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from watchfiles import PythonFilter, awatch

BOT_SOURCE_DIR = Path("app")
SHUTDOWN_TIMEOUT_SECONDS = 10


async def stop_bot(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(process.wait(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except TimeoutError:
            process.kill()
            await process.wait()


async def start_bot() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(sys.executable, "-m", "app.main")


async def cancel_tasks(*tasks: asyncio.Task[object]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def run() -> int:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop_signal, stop_event.set)

    watcher = awatch(BOT_SOURCE_DIR, watch_filter=PythonFilter())
    process = await start_bot()
    print(f"Watching Python files in {BOT_SOURCE_DIR}; BOT polling process started.", flush=True)
    try:
        while True:
            process_task = asyncio.create_task(process.wait())
            change_task = asyncio.create_task(anext(watcher))
            stop_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {process_task, change_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_task in done:
                await cancel_tasks(process_task, change_task)
                await stop_bot(process)
                return 0

            if process_task in done:
                await cancel_tasks(change_task, stop_task)
                exit_code = process.returncode or 1
                print(f"ERROR: BOT process exited with code {exit_code}", file=sys.stderr, flush=True)
                return exit_code

            await cancel_tasks(process_task, stop_task)
            change_task.result()
            print("Python change detected; restarting BOT...", flush=True)
            await stop_bot(process)
            process = await start_bot()
    finally:
        await watcher.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
