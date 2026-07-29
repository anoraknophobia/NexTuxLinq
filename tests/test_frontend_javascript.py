"""Run the dependency-free frontend state tests through pytest."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


REPOSITORY = Path(__file__).resolve().parents[1]
FRONTEND_TEST = REPOSITORY / "tests" / "frontend_state.test.js"


def test_frontend_state_with_node() -> None:
    node = shutil.which("node")
    assert node is not None, "Frontend tests require Node.js 20 or newer."

    version = subprocess.run(
        [node, "--version"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    major = int(version.removeprefix("v").split(".", 1)[0])
    assert major >= 20, f"Frontend tests require Node.js 20 or newer; found {version}."

    result = subprocess.run(
        [node, "--test", "--test-reporter=spec", str(FRONTEND_TEST)],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    assert result.returncode == 0, output
