"""Tests for packaged local web resources."""

import configparser
from email.parser import BytesParser
from email.policy import default
from importlib.resources import files
import re
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


def _editor_source() -> str:
    return files("nextuxlinq").joinpath(
        "static", "free_layout_editor.js"
    ).read_text(encoding="utf-8")


def test_templates_and_static_assets_are_package_resources() -> None:
    package = files("nextuxlinq")
    resources = (
        package.joinpath("templates", "index.html"),
        package.joinpath("static", "app.css"),
        package.joinpath("static", "app.js"),
        package.joinpath("static", "free_layout_editor.js"),
    )
    for resource in resources:
        assert resource.is_file()
        assert resource.read_bytes()


def test_frontend_uses_friendly_reconnecting_state() -> None:
    page, javascript, _ = _frontend_source()

    assert 'const UNAVAILABLE_MESSAGE = "Daemon unavailable. Reconnecting…";' in javascript
    assert "markDaemonUnavailable();" in javascript
    assert "elements.connectionMessage.textContent = UNAVAILABLE_MESSAGE;" in javascript
    assert '"Failed to fetch"' not in javascript
    assert 'id="connection-message"' in page
    assert 'id="message"' in page
    assert 'id="background-message"' in page


def test_polling_retries_and_reconnection_restores_mutation_controls() -> None:
    _, javascript, _ = _frontend_source()

    assert "statusTimer = setTimeoutFunction(pollStatus, 2000);" in javascript
    assert "previewTimer = setTimeoutFunction(pollPreview, 1000);" in javascript
    assert "void pollStatus();" in javascript
    assert "void pollPreview();" in javascript
    assert 'elements.connectionMessage.textContent = "";' in javascript
    assert "state.connection.reachable !== true" in javascript
    assert "elements.applyButton.disabled = mutationDisabled;" in javascript
    assert "elements.saveButton.disabled = mutationDisabled;" in javascript
    assert "reconciliationPromise" in javascript


def test_frontend_state_and_accessibility_resources() -> None:
    page, javascript, _ = _frontend_source()
    editor = _editor_source()

    for state_property in (
        "acceptedFingerprint",
        "pendingRemoteSnapshot",
        "warnedRemoteFingerprint",
        "actionError",
        "backgroundError",
        "fieldErrors",
    ):
        assert state_property in javascript
    assert 'id="configuration-state"' in page
    assert 'role="status" aria-live="polite"' in page
    assert "Draft changes not applied" in javascript
    assert "Applied changes not saved" in javascript
    assert 'primary: "Saved"' in javascript
    assert "Revert unsaved edits" not in page
    assert 'id="fl-editor-state"' in page
    assert "Invalid draft" in editor
    assert "Revision conflict" in editor
    assert "Disconnected · reconnecting" in editor
    assert 'min="0.5" max="20" step="0.1"' in page
    assert "ASCII uppercase" in page
    assert 'windowObject.addEventListener("keydown"' in editor
    assert '["input", "textarea", "select"].includes(tag)' in editor


def test_status_information_uses_compact_main_and_technical_sections() -> None:
    page, _, _ = _frontend_source()
    status_section = page.split(
        '<section aria-labelledby="status-heading">', 1
    )[1].split("</section>", 1)[0]
    main_grid = status_section.split('<dl class="status-grid">', 1)[1].split(
        "</dl>", 1
    )[0]
    assert set(re.findall(r'id="([^"]+)"', main_grid)) == {
        "device-status",
        "active-profile",
        "last-upload",
        "last-error",
    }
    for technical_id in (
        "model-name",
        "vid-pid",
        "device-path",
        "rendering-state",
        "frame-rate",
        "configuration-revision",
        "status-saved-revision",
    ):
        assert f'id="{technical_id}"' not in main_grid

    details = status_section.split(
        '<details class="technical-details">', 1
    )[1].split("</details>", 1)[0]
    assert "<summary>Technical details</summary>" in details
    for technical_id in (
        "model-name",
        "vid-pid",
        "device-path",
        "rendering-state",
        "frame-rate",
        "configuration-revision",
        "status-saved-revision",
    ):
        assert f'id="{technical_id}"' in details
    assert "Metric availability" in details
    assert 'id="metric-availability"' in details
    assert 'aria-live="polite"' in details
    assert "status-dirty" not in status_section


def test_profile_state_is_one_compact_status_without_revision_cards() -> None:
    page, _, css = _frontend_source()
    settings = page.split(
        '<section aria-labelledby="profile-heading">', 1
    )[1].split("</section>", 1)[0]

    assert 'id="fl-editor-state" class="editor-state"' in settings
    for obsolete_id in (
        "draft-state",
        "runtime-state",
        "state-active-revision",
        "state-saved-revision",
    ):
        assert f'id="{obsolete_id}"' not in settings
    assert "configuration-state-values" not in settings
    assert ".editor-state {" in css


def test_preview_regions_use_safe_dynamic_dom_rendering() -> None:
    page, javascript, css = _frontend_source()
    editor = _editor_source()

    assert 'id="fl-region-layer" class="layout-region-layer"' in page
    assert 'aria-label="Viewer-perspective 90 by 10 layout canvas"' in page
    assert (
        "Viewer perspective, matching how the installed Matrix900 is seen."
        in page
    )
    assert "logicalToViewerX(region.x, region.width)" in editor
    assert "base.placeRegionsForViewer(regions, CANVAS_WIDTH)" in editor
    assert "base.copyFramebufferForViewer(" in editor
    assert "label.textContent =" in editor
    assert "replaceChildren(...regions);" in editor
    assert "innerHTML" not in editor
    assert "front_clock_width" not in editor
    assert "aspect-ratio: 9 / 1;" in css
    assert "left: calc(var(--viewer-x) * 100% / 90);" in css
    assert "transform: scaleX(-1)" not in css


def test_visual_editor_layout_has_safe_responsive_sizing() -> None:
    page, _, css = _frontend_source()

    assert 'id="free-layout-root"' in page
    assert 'class="editor-workspace"' in page
    assert 'class="region-inspector"' in page
    assert 'class="geometry-grid"' in page
    assert page.count('data-widget-panel="') == 7
    assert ".editor-workspace {" in css
    workspace = css.split(".editor-workspace {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr);" in workspace
    assert ".layout-canvas-shell {" in css
    assert "width: 100%; min-width: 0; overflow-x: auto;" in css
    assert ".region-inspector {" in css
    inspector = css.split(".region-inspector {", 1)[1].split("}", 1)[0]
    assert "width: 100%;" in inspector
    assert "max-height: none;" in inspector
    assert "overflow: visible;" in inspector
    workspace_markup = page.split('<div class="editor-workspace">', 1)[1].split(
        "</section>", 1
    )[0]
    assert workspace_markup.index('class="layout-canvas-shell"') < (
        workspace_markup.index('id="fl-region-inspector"')
    )
    compact = css.split(".compact-controls {", 1)[1].split("}", 1)[0]
    assert "repeat(2, minmax(10rem, 1fr))" in compact
    assert ".profile-actions {\n  grid-column: 1 / -1;" in css
    assert ".profile-actions button { flex: 0 1 auto; white-space: nowrap; }" in css
    inspector_grid = css.split(".inspector-grid {", 1)[1].split("}", 1)[0]
    assert "repeat(auto-fit, minmax(min(100%, 13rem), 1fr))" in inspector_grid
    assert ".inspector-grid input:not([type=\"checkbox\"])," in css
    assert "flex: 0 0 1.15rem;" in css
    assert "box-sizing: border-box;" in css
    assert "* { box-sizing: border-box; }" in css
    assert ".actions { display: flex; flex-wrap: wrap;" in css
    assert ".actions > button { min-width: 0; max-width: 100%; white-space: normal; }" in css
    assert "@media (max-width: 650px)" in css
    assert "grid-template-columns: minmax(0, 1fr);" in css
    assert "overflow-x: auto;" in css
    assert "@media (max-width: 480px)" in css
    assert "grid-template-columns: minmax(0, 1fr);" in css


def test_visual_editor_exposes_only_canonical_widget_controls() -> None:
    page, _, _ = _frontend_source()
    editor = _editor_source()
    inspector = page.split('id="fl-region-inspector"', 1)[1].split(
        "</aside>", 1
    )[0]

    for widget_type in (
        "blank",
        "clock",
        "static_text",
        "scrolling_text",
        "date",
        "system_metric",
        "metric_bar",
    ):
        assert inspector.count(
            f'data-widget-panel="{widget_type}"'
        ) == 1
        assert inspector.count(f'<option value="{widget_type}">') == 1
    assert 'data-widget-panel="blank"' in inspector
    blank = inspector.split('data-widget-panel="blank"', 1)[1].split(
        "</fieldset>", 1
    )[0]
    assert "data-setting" not in blank
    for setting in (
        "show_seconds",
        "blink_separator",
        "time_zone",
        "uppercase",
        "overflow",
        "direction",
        "speed",
        "initial_alignment",
        "trailing_gap",
        "metric",
        "display_mode",
        "precision",
        "minimum",
        "maximum",
        "orientation",
        "border_enabled",
    ):
        assert f'data-setting="{setting}"' in inspector
    for color_setting in ("color", "fill_color", "empty_color", "border_color"):
        assert f'data-color-setting="{color_setting}"' in inspector
    for date_format in (
        "day_month_full_year",
        "day_month_short_year",
        "day_month",
        "weekday_day_month",
    ):
        assert f'value="{date_format}"' in inspector
    assert "innerHTML" not in editor
    assert "setInterval" not in editor
    assert "eval(" not in editor
    assert "http://" not in editor
    assert "https://" not in editor
    assert "documentObject.hidden" in editor


def test_primary_section_order_places_profile_after_physical_preview() -> None:
    page, _, _ = _frontend_source()
    editor = _editor_source()
    positions = [
        page.index('id="status-heading"'),
        page.index('id="editor-heading"'),
        page.index('id="preview-heading"'),
        page.index('id="profile-heading"'),
        page.index('id="validation-heading"'),
    ]
    assert positions == sorted(positions)
    assert page.count('id="profile-heading"') == 1
    assert 'data-widget-panel="system_metric"' in page
    assert 'data-widget-panel="metric_bar"' in page
    assert 'data-metric-select="bar"' in page
    assert "Bottom to top" in editor


def test_free_layout_colors_use_one_accessible_swatch_and_hex_structure() -> None:
    page, _, css = _frontend_source()
    editor = _editor_source()
    free_layout = page.split('id="free-layout-root"', 1)[1].split(
        'id="legacy-compatibility-host"', 1
    )[0]
    controls = re.findall(
        r'<span class="hex-color-control" data-color-control.*?</span>',
        free_layout,
        flags=re.DOTALL,
    )

    assert len(controls) == 10
    assert free_layout.count('type="color"') == len(controls)
    for control in controls:
        assert control.count('class="color-swatch"') == 1
        assert control.count('class="color-hex"') == 1
        assert 'pattern="#[0-9A-Fa-f]{6}"' in control
        assert 'aria-label=' in control
        assert 'aria-describedby="fl-validation-errors"' in control
    for setting in (
        "color",
        "fill_color",
        "empty_color",
        "border_color",
    ):
        assert f'data-color-setting="{setting}"' in free_layout
    assert 'data-color-target="canvas_background"' in free_layout
    assert 'data-color-target="region_background"' in free_layout
    assert "function synchronizeColorControl(" in editor
    assert "normalizeColorDraft" in editor
    assert ".hex-color-control" in css
    assert "flex-wrap: wrap" in css
    assert "width: 2.75rem" in css
    assert "min-width: 7.5rem" in css
    assert "npm" not in editor


def test_region_background_toggle_and_widget_select_are_separate_controls() -> None:
    page, _, css = _frontend_source()
    inspector = page.split('id="fl-region-inspector"', 1)[1].split(
        "</aside>", 1
    )[0]

    assert inspector.count('id="fl-widget-type"') == 1
    assert inspector.count('id="fl-use-region-background"') == 1
    assert 'class="checkbox-label"' in inspector
    assert ".inspector-grid .checkbox-label" in css
    assert "flex-wrap: nowrap" in css


def test_metric_dropdowns_are_backend_driven_without_extended_id_duplication() -> None:
    page, _, _ = _frontend_source()
    editor = _editor_source()

    assert 'data-metric-select="all"' in page
    assert 'data-metric-select="bar"' in page
    assert "state.metadata.metric_definitions" in editor
    assert "state.metadata.bar_metric_ids" in editor
    for metric_id in (
        "load_average_1m",
        "storage_usage",
        "network_receive_rate",
        "network_transmit_rate",
        "gpu_usage",
        "gpu_temperature",
        "gpu_memory_usage",
        "storage_temperature",
    ):
        assert metric_id not in editor
    assert 'id="fl-region-inspector" class="region-inspector"' in page
    assert page.index('id="preview-heading"') < page.index('id="profile-heading"')


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
        "nextuxlinq/static/free_layout_editor.js",
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
        "assert root.joinpath('static','app.js').read_bytes(); "
        "assert root.joinpath('static','free_layout_editor.js').read_bytes()"
    )
    subprocess.run(
        [sys.executable, "-I", "-c", check],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
