"""Verify the verification gate in child pytest runs, never with a real backend."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("body", "fails", "evidence"),
    [
        (
            """
            async def test_probe():
                asyncio.create_task(crash(), name="dropped-probe")
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            """,
            True,
            "injected-background-crash",
        ),
        (
            """
            async def test_probe():
                held.append(asyncio.create_task(crash(), name="retained-probe"))
                await asyncio.sleep(0)
            """,
            True,
            "injected-background-crash",
        ),
        (
            """
            def callback():
                print("injected-callback-crash")
                raise RuntimeError("injected-callback-crash")
            async def test_probe():
                asyncio.get_running_loop().call_soon(callback)
                await asyncio.sleep(0)
            """,
            True,
            "injected-callback-crash",
        ),
        (
            """
            def test_probe():
                crash()
                gc.collect()
            """,
            True,
            "was never awaited",
        ),
        (
            """
            @pytest.fixture
            async def failing_background_teardown():
                yield
                held.append(asyncio.create_task(crash(), name="teardown-probe"))
                await asyncio.sleep(0)
            async def test_probe(failing_background_teardown):
                pass
            """,
            True,
            "injected-background-crash",
        ),
        (
            """
            async def test_probe():
                task = asyncio.create_task(crash())
                with pytest.raises(RuntimeError, match="injected-background-crash"):
                    await task
            """,
            False,
            "1 passed",
        ),
        (
            """
            async def fail_when_cancelled():
                try:
                    await asyncio.sleep(100)
                finally:
                    print("injected-cancellation-crash")
                    raise RuntimeError("injected-cancellation-crash")
            async def test_probe():
                held.append(asyncio.create_task(fail_when_cancelled()))
                await asyncio.sleep(0)
            """,
            True,
            "injected-cancellation-crash",
        ),
        (
            """
            async def test_probe():
                task = asyncio.create_task(crash())
                await asyncio.sleep(0)
                assert isinstance(task.exception(), RuntimeError)
            """,
            False,
            "1 passed",
        ),
        (
            """
            async def test_probe():
                task = asyncio.create_task(asyncio.sleep(100))
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            """,
            False,
            "1 passed",
        ),
    ],
    ids=[
        "dropped",
        "retained",
        "callback",
        "unawaited",
        "teardown",
        "handled",
        "cancellation-crash",
        "retrieved",
        "cancelled",
    ],
)
def test_async_failures_change_pytest_exit_status(
    tmp_path: Path, body: str, fails: bool, evidence: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / "tests/conftest.py", tmp_path / "conftest.py")
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "import asyncio\nimport gc\nimport pytest\nheld = []\n"
        "async def crash():\n    print('injected-background-crash')\n"
        "    raise RuntimeError('injected-background-crash')\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_asyncio.plugin",
            "-p",
            "pytest_timeout",
            "-q",
            "-s",
            "-c",
            str(root / "pyproject.toml"),
            str(probe),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert evidence in output, output
    assert (result.returncode != 0) is fails, output
