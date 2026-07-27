"""Tests for packaged local web resources."""

from importlib.resources import files
import subprocess
import sys
import zipfile
from pathlib import Path


def test_templates_and_static_assets_are_package_resources() -> None:
    package = files("nexlinq")
    resources = (
        package.joinpath("templates", "index.html"),
        package.joinpath("static", "app.css"),
        package.joinpath("static", "app.js"),
    )
    for resource in resources:
        assert resource.is_file()
        assert resource.read_bytes()


def test_built_wheel_contains_and_exposes_web_resources(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    wheel_directory = tmp_path / "wheel"
    target_directory = tmp_path / "installed"
    wheel_directory.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--no-index",
            "--wheel-dir",
            str(wheel_directory),
            str(repository),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(wheel_directory.glob("*.whl"))
    assert len(wheels) == 1
    expected = {
        "nexlinq/templates/index.html",
        "nexlinq/static/app.css",
        "nexlinq/static/app.js",
    }
    with zipfile.ZipFile(wheels[0]) as archive:
        assert expected <= set(archive.namelist())

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(target_directory),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    check = (
        "import sys; from importlib.resources import files; "
        f"sys.path.insert(0, {str(target_directory)!r}); "
        "root=files('nexlinq'); "
        "assert root.joinpath('templates','index.html').read_bytes(); "
        "assert root.joinpath('static','app.css').read_bytes(); "
        "assert root.joinpath('static','app.js').read_bytes()"
    )
    subprocess.run(
        [sys.executable, "-I", "-c", check],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
