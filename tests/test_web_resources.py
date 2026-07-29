"""Tests for packaged local web resources."""

import configparser
from email.parser import BytesParser
from email.policy import default
from importlib.resources import files
import subprocess
import sys
import zipfile
from pathlib import Path


def _frontend_source() -> tuple[str, str, str]:
    package = files("nextuxlinq")
    return (
        package.joinpath("templates", "index.html").read_text(encoding="utf-8"),
        package.joinpath("static", "app.js").read_text(encoding="utf-8"),
        package.joinpath("static", "app.css").read_text(encoding="utf-8"),
    )


def test_templates_and_static_assets_are_package_resources() -> None:
    package = files("nextuxlinq")
    resources = (
        package.joinpath("templates", "index.html"),
        package.joinpath("static", "app.css"),
        package.joinpath("static", "app.js"),
    )
    for resource in resources:
        assert resource.is_file()
        assert resource.read_bytes()


def test_frontend_uses_friendly_reconnecting_state() -> None:
    _, javascript, _ = _frontend_source()

    assert 'const unavailableMessage = "Daemon unavailable. Reconnecting…";' in javascript
    assert "markDaemonUnavailable();" in javascript
    assert "connectionMessage.textContent = unavailableMessage;" in javascript
    assert '"Failed to fetch"' not in javascript
    assert "showMessage(error.message, \"error\");" not in javascript


def test_polling_retries_and_reconnection_restores_mutation_controls() -> None:
    _, javascript, _ = _frontend_source()

    assert "finally {\n    if (polling) statusTimer = setTimeout(pollStatus, 2000);" in javascript
    assert "finally {\n    if (polling) previewTimer = setTimeout(pollPreview, 1000);" in javascript
    assert ".finally(() => {\n    pollStatus();\n    pollPreview();" in javascript
    assert 'connectionMessage.textContent = "";' in javascript
    assert "daemonReachable !== true || configuration === null" in javascript
    assert 'document.querySelector("[data-action=\'apply\']")' in javascript
    assert 'document.querySelector("[data-action=\'save\']")' in javascript
    assert "button.disabled = daemonControlDisabled;" in javascript
    assert 'showMessage(`${error.message}${suffix}`, "error", "action-error");' in javascript
    assert 'if (messageOwner !== "action-error") {' in javascript


def test_profile_form_layout_has_safe_responsive_sizing() -> None:
    page, _, css = _frontend_source()

    assert page.count('class="profile-field"') == 5
    assert page.count('class="profile-field-label"') == 5
    assert page.count('class="profile-control') == 5
    assert ".profile-field {" in css
    assert (
        "grid-template-rows: var(--profile-label-height) "
        "var(--profile-control-height);"
    ) in css
    assert "--profile-label-height: 2.5rem;" in css
    assert "height: var(--profile-label-height);" in css
    assert "--profile-control-height: 2.8rem;" in css
    assert "height: var(--profile-control-height);" in css
    assert "min-height: var(--profile-control-height);" in css
    assert "max-height: var(--profile-control-height);" in css
    assert "box-sizing: border-box;" in css
    assert '<input class="profile-control" id="scrolling-text"' in page
    assert '<input class="profile-control" id="scroll-speed"' in page
    assert '<select class="profile-control"' in page
    assert page.count('class="profile-control color-control"') == 2
    assert "appearance: none;" in css
    assert "-webkit-appearance: none;" in css
    assert ".color-picker::-webkit-color-swatch-wrapper { padding: 0; }" in css
    assert ".color-picker::-webkit-color-swatch {" in css
    assert ".color-picker::-moz-color-swatch {" in css
    assert "repeat(auto-fit, minmax(min(100%, 190px), 1fr))" in css
    assert ".status-grid > *, .form-grid > * { min-width: 0; }" in css
    assert "* { box-sizing: border-box; }" in css
    assert ".actions { display: flex; flex-wrap: wrap;" in css
    assert ".actions > button { min-width: 0; max-width: 100%; white-space: normal; }" in css
    assert "@media (max-width: 480px)" in css
    assert ".status-grid, .form-grid { grid-template-columns: minmax(0, 1fr); }" in css
    assert "repeat(5," not in css


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
        "nextuxlinq/service_manager.py",
        "nextuxlinq/templates/index.html",
        "nextuxlinq/static/app.css",
        "nextuxlinq/static/app.js",
    }
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        obsolete_name = "nex" + "linq"
        assert expected <= names
        assert not any(name.startswith(f"{obsolete_name}/") for name in names)
        import_roots = {
            name.partition("/")[0]
            for name in names
            if name.endswith(".py") and ".dist-info/" not in name
        }
        assert import_roots == {"nextuxlinq"}

        metadata_names = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        assert len(metadata_names) == 1
        metadata = BytesParser(policy=default).parsebytes(
            archive.read(metadata_names[0])
        )
        assert metadata["Name"] == "nextuxlinq"

        entry_point_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        assert len(entry_point_names) == 1
        entry_points = configparser.ConfigParser()
        entry_points.read_string(
            archive.read(entry_point_names[0]).decode("utf-8")
        )
        assert dict(entry_points["console_scripts"]) == {
            "nextuxlinqctl": "nextuxlinq.cli:main",
            "nextuxlinqd": "nextuxlinq.daemon:main",
        }
        assert f"{obsolete_name}ctl" not in entry_points["console_scripts"]
        assert f"{obsolete_name}d" not in entry_points["console_scripts"]

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
        "import importlib.util; "
        "assert importlib.util.find_spec('nex' + 'linq') is None; "
        "root=files('nextuxlinq'); "
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
