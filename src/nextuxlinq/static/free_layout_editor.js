"use strict";

(function exposeFreeLayoutEditor(root, factory) {
  const base = (
    root && root.NexTuxLinqFrontend
  ) || (
    typeof module === "object" && module.exports
      ? require("./app.js") : null
  );
  const editor = factory(base);
  if (typeof module === "object" && module.exports) {
    module.exports = editor;
  }
  if (root) root.NexTuxLinqFreeLayout = editor;
  if (root && root.document && root.document.querySelector("#free-layout-root")) {
    editor.bootstrap({
      document: root.document,
      window: root,
      fetch: root.fetch.bind(root),
      setTimeout: root.setTimeout.bind(root),
      clearTimeout: root.clearTimeout.bind(root),
      decodeBase64: root.atob.bind(root),
      encodeBase64: root.btoa.bind(root),
    });
  }
})(typeof globalThis === "object" ? globalThis : this, function freeLayoutFactory(base) {
  const API_PREFIX = "/api/v1/free-layout";
  const CANVAS_WIDTH = 90;
  const CANVAS_HEIGHT = 10;
  const WIDGET_TYPES = Object.freeze([
    "blank",
    "clock",
    "static_text",
    "scrolling_text",
    "date",
    "system_metric",
    "metric_bar",
  ]);
  const REGION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
  const PROFILE_ID = /^[A-Za-z0-9_-]{1,32}$/;
  const COLOR = /^#[0-9A-Fa-f]{6}$/;
  const TIME_ZONE = /^[A-Za-z0-9._+-]+(?:\/[A-Za-z0-9._+-]+)*$/;
  const HORIZONTAL = new Set(["start", "center", "end"]);
  const VERTICAL = new Set(["top", "center", "bottom"]);
  const OVERFLOW = new Set(["error", "truncate", "clip"]);

  function effectSpeedDescription(speed) {
    const value = Number(speed);
    if (!Number.isFinite(value) || value < 0) return "Invalid speed";
    if (value === 0) return "Static";
    const seconds = 1 / value;
    return `One cycle every ${seconds.toFixed(seconds < 10 ? 2 : 1)} seconds`;
  }

  function effectDefault(metadata = {}) {
    return {mode: metadata.display_effect?.default_mode || "none"};
  }

  function foregroundRainbowActive(profile) {
    return profile?.effect?.mode === "foreground_rainbow";
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function normalizeColorDraft(value) {
    const draft = typeof value === "string" ? value : "";
    return COLOR.test(draft) ? draft.toUpperCase() : draft;
  }

  function colorControlState(value, lastValid = "#000000") {
    const draft = normalizeColorDraft(value);
    const fallback = COLOR.test(lastValid)
      ? lastValid.toUpperCase() : "#000000";
    const valid = COLOR.test(draft);
    return Object.freeze({
      valid,
      draft,
      picker: valid ? draft : fallback,
    });
  }

  function synchronizeColorControl(control, value, lastValid = null) {
    if (!control) return colorControlState(value, lastValid || "#000000");
    const picker = control.querySelector('input[type="color"]');
    const hex = control.querySelector("[data-color-hex]");
    const state = colorControlState(
      value,
      lastValid || control.dataset.lastValidColor || "#000000",
    );
    if (state.valid) control.dataset.lastValidColor = state.picker;
    control.dataset.invalid = String(!state.valid);
    if (picker) {
      picker.value = state.picker;
      picker.setAttribute("aria-invalid", String(!state.valid));
    }
    if (hex) {
      hex.value = state.draft;
      hex.setAttribute("aria-invalid", String(!state.valid));
    }
    return state;
  }

  function fingerprint(value) {
    return base.stableStringify(value);
  }

  function logicalToViewerX(logicalX, width, canvasWidth = CANVAS_WIDTH) {
    return base.viewerX(logicalX, width, canvasWidth);
  }

  function viewerToLogicalX(viewerX, width, canvasWidth = CANVAS_WIDTH) {
    return canvasWidth - viewerX - width;
  }

  function clampInteger(value, minimum, maximum) {
    const integer = Math.round(Number(value));
    if (!Number.isFinite(integer)) return minimum;
    return Math.min(maximum, Math.max(minimum, integer));
  }

  function clampGeometry(geometry, canvasWidth = CANVAS_WIDTH,
    canvasHeight = CANVAS_HEIGHT) {
    const width = clampInteger(geometry.width, 1, canvasWidth);
    const height = clampInteger(geometry.height, 1, canvasHeight);
    return {
      x: clampInteger(geometry.x, 0, canvasWidth - width),
      y: clampInteger(geometry.y, 0, canvasHeight - height),
      width,
      height,
    };
  }

  function rectanglesOverlap(left, right) {
    return (
      left.x < right.x + right.width
      && right.x < left.x + left.width
      && left.y < right.y + right.height
      && right.y < left.y + left.height
    );
  }

  const VALIDATION_PRIORITY = Object.freeze({
    out_of_bounds: 0,
    overlap: 1,
    content_too_tall: 2,
    content_too_wide: 3,
    wrong_widget_settings: 4,
    invalid_color: 4,
    invalid_dimensions: 4,
    unsupported_glyph: 5,
    unknown_font: 5,
    time_zone_unavailable: 5,
  });

  function validationPriority(error) {
    return VALIDATION_PRIORITY[error?.category] ?? 6;
  }

  function validationExplanation(error, profile = null) {
    const affectedRegion = profile?.regions?.find(
      (region) => region.id === error?.region_id,
    );
    const selectedFont = affectedRegion?.widget?.settings?.font;
    const explicitFont = selectedFont && selectedFont !== "auto"
      ? selectedFont.replace("x", "×") : null;
    const fit = error?.font_fit;
    if (fit?.no_font_supports_content) {
      return "No available font supports the configured characters";
    }
    if (fit?.no_font_fits && !explicitFont) {
      return "No available font fits this region";
    }
    const actualWidth = Number.isInteger(error?.actual_width)
      ? error.actual_width : null;
    const actualHeight = Number.isInteger(error?.actual_height)
      ? error.actual_height : null;
    const minimumWidth = Number.isInteger(error?.minimum_width)
      ? error.minimum_width : null;
    const minimumHeight = Number.isInteger(error?.minimum_height)
      ? error.minimum_height : null;
    const widthFails = minimumWidth !== null && actualWidth !== null
      && actualWidth < minimumWidth;
    const heightFails = minimumHeight !== null && actualHeight !== null
      && actualHeight < minimumHeight;
    if (widthFails && heightFails) {
      return explicitFont
        ? `${explicitFont} needs minimum size ${minimumWidth} × ${minimumHeight} px`
        : `Minimum size ${minimumWidth} × ${minimumHeight} px`;
    }
    if (error?.category === "out_of_bounds") return "Outside canvas";
    if (error?.category === "overlap") {
      const conflict = profile?.regions?.find(
        (region) => region.id === error.conflicting_region_id,
      );
      return conflict?.name
        ? `Overlaps ${conflict.name}` : "Overlaps another region";
    }
    if (heightFails || error?.category === "content_too_tall") {
      return minimumHeight === null
        ? "Text is too tall"
        : explicitFont
          ? `Selected ${explicitFont} is too tall · needs height ${minimumHeight} px`
          : `Too short · minimum height ${minimumHeight} px`;
    }
    if (widthFails || error?.category === "content_too_wide") {
      return minimumWidth === null
        ? "Text does not fit"
        : explicitFont
          ? `Selected ${explicitFont} is too wide · needs ${minimumWidth} px`
          : `Too narrow · minimum width ${minimumWidth} px`;
    }
    return {
      invalid_dimensions: "Invalid region size",
      wrong_widget_settings: "Invalid widget setting",
      invalid_color: "Invalid color",
      unsupported_glyph: "Unsupported character",
      unknown_font: "Unsupported font",
      time_zone_unavailable: "Invalid time zone",
      date_format_unavailable: "Invalid date format",
      renderer_invariant: "Invalid widget setting",
      unexpected_renderer_failure: "Widget cannot be rendered",
    }[error?.category] || "Invalid region";
  }

  function fontFitCandidate(analysis, fontId) {
    return analysis?.candidates?.find((item) => item.font_id === fontId) || null;
  }

  function smallestCompatibleCandidate(analysis) {
    return [...(analysis?.candidates || [])].reverse().find(
      (item) => item.compatible_with_widget && item.complete_glyph_coverage,
    ) || null;
  }

  function fontGuidanceLines(error) {
    const analysis = error?.font_fit;
    if (!analysis) return [];
    const lines = [];
    if (analysis.best_smaller_alternative) {
      lines.push(`${analysis.best_smaller_alternative.replace("x", "×")} would fit`);
    }
    if (analysis.no_font_fits && !analysis.no_font_supports_content) {
      const smallest = smallestCompatibleCandidate(analysis);
      if (Number.isInteger(smallest?.minimum_width)
          && Number.isInteger(smallest?.minimum_height)) {
        lines.push(
          `Smallest compatible font needs at least ${smallest.minimum_width} × ${
            smallest.minimum_height
          } px`,
        );
      }
    }
    return lines;
  }

  function safeResizeGeometry(profile, regionId, width, height) {
    if (!Number.isInteger(width) || width <= 0
        || !Number.isInteger(height) || height <= 0) return null;
    const region = profile?.regions?.find((item) => item.id === regionId);
    if (!region || region.x + width > CANVAS_WIDTH
        || region.y + height > CANVAS_HEIGHT) return null;
    const resized = {...region, width, height};
    if (profile.regions.some(
      (other) => other.id !== regionId && rectanglesOverlap(resized, other),
    )) return null;
    return {width, height};
  }

  function fontResizeSuggestion(profile, region, analysis) {
    if (!analysis || !region) return null;
    const candidate = analysis.selected_explicit_font
      ? fontFitCandidate(analysis, analysis.selected_explicit_font)
      : smallestCompatibleCandidate(analysis);
    if (!candidate || candidate.complete_glyph_coverage !== true
        || !Number.isInteger(candidate.minimum_width)
        || !Number.isInteger(candidate.minimum_height)
        || candidate.overall_fit) return null;
    return safeResizeGeometry(
      profile, region.id, candidate.minimum_width, candidate.minimum_height,
    );
  }

  function regionValidationErrors(localErrors, serverErrors, regionId) {
    return [...(localErrors || []), ...(serverErrors || [])]
      .filter((error) => error.region_id === regionId)
      .sort((left, right) => (
        validationPriority(left) - validationPriority(right)
      ));
  }

  function fontOptionReason(status) {
    if (!status || status.available) return "";
    if (status.category === "content_too_tall"
        && Number.isInteger(status.minimum_height)) {
      return `Needs height ${status.minimum_height} px`;
    }
    if (status.category === "content_too_wide"
        && Number.isInteger(status.minimum_width)) {
      return `Needs width ${status.minimum_width} px`;
    }
    if (status.category === "unsupported_glyph") {
      return "Unsupported characters";
    }
    return "Not available for this widget";
  }

  function resolvedFontExplanation(
    selectedFont, resolvedFont, definitions, validationState,
  ) {
    const selected = (definitions || []).find(
      (item) => item.id === (
        selectedFont === "auto" ? resolvedFont : selectedFont
      ),
    );
    if (selectedFont !== "auto") {
      return `Selected font: ${
        selected?.label || String(selectedFont).replace("x", "×")
      }`;
    }
    if (selected) return `Resolved font: ${selected.label}`;
    return validationState === "invalid"
      ? "Resolved font: unavailable" : "Resolved font: validating…";
  }

  function temperatureFormatExample(settings, unitDefinitions = []) {
    const precision = Number.isInteger(settings.precision)
      ? Math.min(2, Math.max(0, settings.precision)) : 0;
    let value = 43;
    if (settings.temperature_unit === "fahrenheit") {
      value = value * 9 / 5 + 32;
    }
    const suffix = unitDefinitions.find(
      (item) => item.id === settings.temperature_unit,
    )?.suffix || "";
    const label = settings.display_mode === "label_and_value"
      ? settings.label : "";
    return `${label}${value.toFixed(precision)}${suffix}`;
  }

  function temperatureUnitVisible(metricId, metricDefinitions = []) {
    return metricDefinitions.some(
      (definition) => definition.id === metricId
        && definition.value_type === "temperature",
    );
  }

  function metricDefinitionGroups(metricDefinitions = [], eligibleIds = null) {
    const allowed = eligibleIds === null ? null : new Set(eligibleIds);
    const groups = [];
    const byName = new Map();
    for (const definition of metricDefinitions) {
      if (allowed && !allowed.has(definition.id)) continue;
      let group = byName.get(definition.group);
      if (!group) {
        group = {label: definition.group, metrics: []};
        byName.set(definition.group, group);
        groups.push(group);
      }
      group.metrics.push(definition);
    }
    return groups;
  }

  function totalRegionCount(configuration) {
    return Object.values(configuration.profiles).reduce(
      (total, profile) => total + profile.regions.length,
      0,
    );
  }

  function validateWidgetSettings(widget, metadata = {}) {
    if (!widget || !WIDGET_TYPES.includes(widget.type)
        || !widget.settings || typeof widget.settings !== "object"
        || Array.isArray(widget.settings)) {
      return "widget";
    }
    const definition = metadata.widget_definitions?.find(
      (item) => item.type === widget.type,
    );
    if (definition) {
      const actual = Object.keys(widget.settings).sort();
      const expected = Object.keys(definition.default_settings).sort();
      if (fingerprint(actual) !== fingerprint(expected)) return "settings";
    }
    const settings = widget.settings;
    const fontIds = new Set(
      (metadata.font_definitions || []).map((item) => item.id),
    );
    const fontValid = () => fontIds.has(settings.font);
    const colorValid = () => COLOR.test(settings.color || "");
    const verticalValid = () => VERTICAL.has(settings.vertical_alignment);
    const horizontalValid = () => HORIZONTAL.has(
      settings.horizontal_alignment,
    );
    const timeZoneValid = () => (
      typeof settings.time_zone === "string"
      && settings.time_zone.length >= 1
      && settings.time_zone.length <= 128
      && (
        settings.time_zone === "local"
        || (
          TIME_ZONE.test(settings.time_zone)
          && !settings.time_zone.split("/").some(
            (part) => part === "." || part === "..",
          )
        )
      )
    );
    const contentValid = () => (
      typeof settings.content === "string"
      && settings.content.length >= 1
      && settings.content.length <= 64
    );
    if (widget.type === "blank") return Object.keys(settings).length ? "settings" : null;
    if (widget.type === "system_metric") {
      const metricIds = new Set(
        (metadata.metric_definitions || []).map((item) => item.id),
      );
      const textValid = (value) => (
        typeof value === "string" && value.length >= 1 && value.length <= 64
      );
      if (metricIds.size > 0 && !metricIds.has(settings.metric)) return "metric";
      if (!new Set(["value_only", "label_and_value"]).has(settings.display_mode)) {
        return "display_mode";
      }
      if (!textValid(settings.label)) return "label";
      if (!textValid(settings.unavailable_text)) return "unavailable_text";
      if (!Number.isInteger(settings.precision)
          || settings.precision < 0 || settings.precision > 2) return "precision";
      const temperatureUnitIds = new Set(
        (metadata.temperature_unit_definitions || []).map((item) => item.id),
      );
      if (temperatureUnitIds.size > 0
          && !temperatureUnitIds.has(settings.temperature_unit)) {
        return "temperature_unit";
      }
      if (!fontValid()) return "font";
      if (!colorValid()) return "color";
      if (!verticalValid()) return "vertical_alignment";
      if (!horizontalValid()) return "horizontal_alignment";
      return OVERFLOW.has(settings.overflow) ? null : "overflow";
    }

    if (widget.type === "metric_bar") {
      const metricIds = new Set(metadata.bar_metric_ids || []);
      if (!metricIds.has(settings.metric)) return "metric";
      if (!Number.isFinite(settings.minimum)) return "minimum";
      if (!Number.isFinite(settings.maximum)
          || settings.maximum <= settings.minimum) return "maximum";
      if (!new Set(["horizontal", "vertical"]).has(settings.orientation)) {
        return "orientation";
      }
      if (!new Set(["start_to_end", "end_to_start"]).has(settings.direction)) {
        return "direction";
      }
      if (!COLOR.test(settings.fill_color || "")) return "fill_color";
      if (!COLOR.test(settings.empty_color || "")) return "empty_color";
      if (typeof settings.border_enabled !== "boolean") return "border_enabled";
      return COLOR.test(settings.border_color || "") ? null : "border_color";
    }
    if (!fontValid() || !colorValid() || !verticalValid()) return "settings";
    if (widget.type === "clock") {
      return (
        !new Set(["12h", "24h"]).has(settings.format)
        || typeof settings.show_seconds !== "boolean"
        || typeof settings.blink_separator !== "boolean"
        || !timeZoneValid()
        || !horizontalValid()
      ) ? "settings" : null;
    }
    if (widget.type === "date") {
      return (
        !new Set([
          "day_month_full_year",
          "day_month_short_year",
          "day_month",
          "weekday_day_month",
        ]).has(settings.format)
        || !timeZoneValid()
        || !horizontalValid()
        || !OVERFLOW.has(settings.overflow)
      ) ? "settings" : null;
    }
    if (!contentValid() || typeof settings.uppercase !== "boolean") {
      return "content";
    }
    if (widget.type === "static_text") {
      return (
        !horizontalValid() || !OVERFLOW.has(settings.overflow)
      ) ? "settings" : null;
    }
    return (
      !new Set(["left", "right"]).has(settings.direction)
      || !Number.isFinite(settings.speed)
      || settings.speed < 0.5
      || settings.speed > 20
      || !HORIZONTAL.has(settings.initial_alignment)
      || !Number.isInteger(settings.trailing_gap)
      || settings.trailing_gap < 0
      || settings.trailing_gap > 90
    ) ? "settings" : null;
  }

  function validateLocalCandidate(configuration, metadata = {}) {
    const errors = [];
    const invalidRegionIds = new Set();
    const add = (category, field, regionId = null, details = {}) => {
      errors.push({category, field, region_id: regionId, ...details});
      if (regionId) invalidRegionIds.add(regionId);
    };
    if (!configuration || configuration.schema_version !== 2
        || !configuration.profiles
        || !configuration.profiles[configuration.active_profile]) {
      add("invalid_configuration", "configuration");
      return {valid: false, errors, invalidRegionIds};
    }
    const profiles = Object.entries(configuration.profiles);
    if (profiles.length < 1
        || profiles.length > (metadata.max_profiles ?? 32)) {
      add("profile_limit", "profiles");
    }
    if (totalRegionCount(configuration)
        > (metadata.max_regions_per_document ?? 128)) {
      add("region_limit", "profiles");
    }
    for (const [profileName, profile] of profiles) {
      if (!PROFILE_ID.test(profileName)) {
        add("invalid_profile_id", "profiles");
      }
      if (!profile || !profile.canvas
          || profile.canvas.width !== CANVAS_WIDTH
          || profile.canvas.height !== CANVAS_HEIGHT
          || !COLOR.test(profile.canvas.background_color)) {
        add("invalid_canvas", `profiles.${profileName}.canvas`);
        continue;
      }
      if (profile.effect !== undefined) {
        const effect = profile.effect;
        const effectMetadata = metadata.display_effect || {};
        const modes = new Set((effectMetadata.modes || []).map(
          (item) => item.id,
        ));
        if (!effect || typeof effect !== "object"
            || (modes.size > 0 && !modes.has(effect.mode))) {
          add("unknown_display_effect", `profiles.${profileName}.effect.mode`);
        } else if (effect.mode === "foreground_rainbow") {
          const directions = new Set((effectMetadata.directions || []).map(
            (item) => item.id,
          ));
          if (directions.size > 0 && !directions.has(effect.direction)) {
            add("invalid_effect_direction",
              `profiles.${profileName}.effect.direction`);
          }
          if (!Number.isFinite(effect.speed)
              || effect.speed < effectMetadata.speed_minimum
              || effect.speed > effectMetadata.speed_maximum) {
            add("invalid_effect_speed", `profiles.${profileName}.effect.speed`);
          }
        }
      }
      if (!Array.isArray(profile.regions)
          || profile.regions.length
            > (metadata.max_regions_per_profile ?? 32)) {
        add("region_limit", `profiles.${profileName}.regions`);
        continue;
      }
      const ids = new Set();
      for (const region of profile.regions) {
        const id = typeof region.id === "string" ? region.id : null;
        if (!id || !REGION_ID.test(id) || ids.has(id)) {
          add(ids.has(id) ? "duplicate_region_id" : "invalid_region_id",
            "id", id);
        }
        if (id) ids.add(id);
        const integers = ["x", "y", "width", "height"].every(
          (field) => Number.isInteger(region[field]),
        );
        if (!integers || region.width < 1 || region.height < 1) {
          add("invalid_dimensions", "geometry", id, {
            actual_width: region.width,
            actual_height: region.height,
          });
        } else if (region.x < 0 || region.y < 0
            || region.x + region.width > CANVAS_WIDTH
            || region.y + region.height > CANVAS_HEIGHT) {
          add("out_of_bounds", "geometry", id, {
            actual_width: region.width,
            actual_height: region.height,
          });
        }
        if (typeof region.enabled !== "boolean") {
          add("invalid_enabled", "enabled", id);
        }
        if (region.background_color !== null
            && !COLOR.test(region.background_color || "")) {
          add("invalid_color", "background_color", id);
        }
        const settingsField = validateWidgetSettings(region.widget, metadata);
        if (settingsField) {
          add("wrong_widget_settings", settingsField, id);
        }
      }
      for (let index = 0; index < profile.regions.length; index += 1) {
        const left = profile.regions[index];
        for (const right of profile.regions.slice(index + 1)) {
          if (rectanglesOverlap(left, right)) {
            add("overlap", "geometry", left.id, {
              conflicting_region_id: right.id,
            });
            add("overlap", "geometry", right.id, {
              conflicting_region_id: left.id,
            });
          }
        }
      }
    }
    return {
      valid: errors.length === 0,
      errors,
      invalidRegionIds,
    };
  }

  function nextStableId(existingIds, prefix = "region") {
    const occupied = new Set(existingIds);
    for (let index = 1; index < 100000; index += 1) {
      const candidate = `${prefix}-${index}`;
      if (!occupied.has(candidate)) return candidate;
    }
    throw new Error("No stable identifier is available.");
  }

  function widgetDefaultMap(metadata) {
    return Object.fromEntries(metadata.widget_definitions.map(
      (definition) => [definition.type, clone(definition.default_settings)],
    ));
  }

  function createEmptyProfile(metadata = {}) {
    return {
      brightness: null,
      canvas: {
        width: CANVAS_WIDTH,
        height: CANVAS_HEIGHT,
        background_color: "#000000",
      },
      regions: [],
      effect: effectDefault(metadata),
    };
  }

  function findOpenPlacement(regions, width, height) {
    for (let y = 0; y <= CANVAS_HEIGHT - height; y += 1) {
      for (let x = 0; x <= CANVAS_WIDTH - width; x += 1) {
        const candidate = {x, y, width, height};
        if (!regions.some((region) => rectanglesOverlap(candidate, region))) {
          return {x, y};
        }
      }
    }
    return null;
  }

  function addRegion(profile, defaults, metadata = {}) {
    const result = clone(profile);
    if (result.regions.length >= (metadata.max_regions_per_profile ?? 32)) {
      return {profile: result, regionId: null};
    }
    const id = nextStableId(result.regions.map((region) => region.id));
    const placement = findOpenPlacement(result.regions, 10, 5) || {x: 0, y: 0};
    result.regions.push({
      id,
      name: `Region ${result.regions.length + 1}`,
      x: placement.x,
      y: placement.y,
      width: 10,
      height: 5,
      enabled: true,
      background_color: null,
      widget: {type: "blank", settings: clone(defaults.blank)},
    });
    return {profile: result, regionId: id};
  }

  function duplicateRegion(profile, regionId) {
    const result = clone(profile);
    const source = result.regions.find((region) => region.id === regionId);
    if (!source) return {profile: result, regionId: null};
    const duplicate = clone(source);
    duplicate.id = nextStableId(result.regions.map((region) => region.id));
    duplicate.name = source.name
      ? `${source.name.slice(0, 59)} copy`
      : "Region copy";
    const others = result.regions;
    let placement = null;
    for (let distance = 1; distance < CANVAS_WIDTH && !placement; distance += 1) {
      for (const x of [source.x + distance, source.x - distance]) {
        const candidate = {...source, x};
        if (x >= 0 && x + source.width <= CANVAS_WIDTH
            && !others.some((region) => rectanglesOverlap(candidate, region))) {
          placement = {x, y: source.y};
          break;
        }
      }
    }
    if (placement) Object.assign(duplicate, placement);
    result.regions.push(duplicate);
    return {profile: result, regionId: duplicate.id};
  }

  function reorderRegion(profile, regionId, direction) {
    const result = clone(profile);
    const index = result.regions.findIndex((region) => region.id === regionId);
    const target = index + direction;
    if (index >= 0 && target >= 0 && target < result.regions.length) {
      [result.regions[index], result.regions[target]] = [
        result.regions[target],
        result.regions[index],
      ];
    }
    return result;
  }

  function removeRegion(profile, regionId) {
    const result = clone(profile);
    result.regions = result.regions.filter((region) => region.id !== regionId);
    return result;
  }

  function toggleRegion(profile, regionId) {
    const result = clone(profile);
    const region = result.regions.find((item) => item.id === regionId);
    if (region) region.enabled = !region.enabled;
    return result;
  }

  function shouldConfirmRegionDelete(region) {
    return Boolean(
      region
      && (
        region.widget.type !== "blank"
        || region.background_color !== null
      )
    );
  }

  function isTextEntryTarget(target) {
    const tag = String(target?.tagName || "").toLowerCase();
    return ["input", "textarea", "select"].includes(tag)
      || Boolean(target?.isContentEditable);
  }

  function selectionForProfile(selectedRegionId, profile) {
    if (!selectedRegionId || !profile) return null;
    return profile.regions.some(
      (region) => region.id === selectedRegionId,
    ) ? selectedRegionId : null;
  }

  function selectionForCanvasPointer(
    selectedRegionId,
    eventTarget,
    canvasLayer,
  ) {
    return eventTarget === canvasLayer ? null : selectedRegionId;
  }

  function toggleRegionSelection(selectedRegionId, regionId) {
    return selectedRegionId === regionId ? null : regionId;
  }

  function setRegionWidget(region, widgetType, defaults) {
    if (!WIDGET_TYPES.includes(widgetType)) return clone(region);
    const result = clone(region);
    result.widget = {
      type: widgetType,
      settings: clone(defaults[widgetType]),
    };
    return result;
  }

  function geometryFromViewer(viewerX, y, width, height) {
    return {
      x: viewerToLogicalX(viewerX, width),
      y,
      width,
      height,
    };
  }

  function moveGeometryInViewer(geometry, viewerDeltaX, deltaY) {
    const viewerStart = logicalToViewerX(geometry.x, geometry.width);
    const viewerX = clampInteger(
      viewerStart + viewerDeltaX,
      0,
      CANVAS_WIDTH - geometry.width,
    );
    return {
      x: viewerToLogicalX(viewerX, geometry.width),
      y: clampInteger(
        geometry.y + deltaY,
        0,
        CANVAS_HEIGHT - geometry.height,
      ),
      width: geometry.width,
      height: geometry.height,
    };
  }

  function resizeGeometryInViewer(geometry, handle, deltaX, deltaY) {
    let left = logicalToViewerX(geometry.x, geometry.width);
    let top = geometry.y;
    let right = left + geometry.width;
    let bottom = top + geometry.height;
    if (handle.includes("w")) left = clampInteger(left + deltaX, 0, right - 1);
    if (handle.includes("e")) right = clampInteger(right + deltaX, left + 1,
      CANVAS_WIDTH);
    if (handle.includes("n")) top = clampInteger(top + deltaY, 0, bottom - 1);
    if (handle.includes("s")) bottom = clampInteger(bottom + deltaY, top + 1,
      CANVAS_HEIGHT);
    return geometryFromViewer(left, top, right - left, bottom - top);
  }

  function profileAction(
    configuration, action, sourceName, targetName, metadata = {}
  ) {
    const result = clone(configuration);
    if (action === "create") {
      if (!PROFILE_ID.test(targetName) || result.profiles[targetName]) return null;
      result.profiles[targetName] = createEmptyProfile(metadata);
      result.active_profile = targetName;
    } else if (action === "duplicate") {
      if (!result.profiles[sourceName] || !PROFILE_ID.test(targetName)
          || result.profiles[targetName]) return null;
      result.profiles[targetName] = clone(result.profiles[sourceName]);
      result.active_profile = targetName;
    } else if (action === "rename") {
      if (!result.profiles[sourceName] || !PROFILE_ID.test(targetName)
          || result.profiles[targetName]) return null;
      const entries = Object.entries(result.profiles).map(([name, profile]) => (
        [name === sourceName ? targetName : name, profile]
      ));
      result.profiles = Object.fromEntries(entries);
      result.active_profile = targetName;
    } else if (action === "delete") {
      const names = Object.keys(result.profiles);
      if (!result.profiles[sourceName] || names.length <= 1) return null;
      delete result.profiles[sourceName];
      if (result.active_profile === sourceName) {
        result.active_profile = Object.keys(result.profiles)[0];
      }
    } else {
      return null;
    }
    return result;
  }

  function hasMeaningfulDraft(state) {
    return Boolean(state && state.applied && state.draftDirty);
  }

  function addImportedProfile(configuration, imported) {
    if (!configuration || !imported || !PROFILE_ID.test(imported.profile_name)
        || configuration.profiles[imported.profile_name]) return null;
    const result = clone(configuration);
    result.profiles[imported.profile_name] = clone(imported.profile);
    result.active_profile = imported.profile_name;
    return result;
  }

  function createProfileFromPreset(configuration, preset, profileName) {
    if (!preset || !PROFILE_ID.test(profileName)
        || configuration.profiles[profileName]) return null;
    const result = clone(configuration);
    result.profiles[profileName] = clone(preset.profile);
    result.active_profile = profileName;
    return result;
  }

  function arrayBufferToBase64(buffer, encodeBinary) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return encodeBinary(binary);
  }

  function deriveEditorState(state) {
    if (!state.applied) return {label: "Loading", className: ""};
    if (state.conflict) return {label: "Revision conflict", className: "invalid"};
    if (state.disconnected) {
      return {label: "Disconnected · reconnecting", className: "pending"};
    }
    if (state.localValidation && !state.localValidation.valid) {
      return {label: "Invalid draft", className: "invalid"};
    }
    if (state.serverValidation === "pending") {
      return {label: "Validating draft…", className: "pending"};
    }
    if (state.serverValidation === "invalid") {
      return {label: "Invalid draft", className: "invalid"};
    }
    if (state.draftDirty) return {label: "Local draft", className: "draft"};
    if (state.migrationPending) {
      return {label: "Migration pending · save required", className: "unsaved"};
    }
    if (state.appliedDirty) return {label: "Applied but unsaved", className: "unsaved"};
    return {label: "Saved", className: "saved"};
  }

  class EditorRequestError extends Error {
    constructor(message, status, code, fieldErrors) {
      super(message);
      this.status = status;
      this.code = code;
      this.fieldErrors = Array.isArray(fieldErrors) ? fieldErrors : [];
    }
  }

  function createEditor(dependencies) {
    const documentObject = dependencies.document;
    const windowObject = dependencies.window || {};
    const fetchFunction = dependencies.fetch;
    const setTimeoutFunction = dependencies.setTimeout;
    const clearTimeoutFunction = dependencies.clearTimeout;
    const decodeBase64 = dependencies.decodeBase64;
    const encodeBase64 = dependencies.encodeBase64
      || windowObject.btoa?.bind(windowObject);
    const root = documentObject.querySelector("#free-layout-root");
    const element = (selector) => root.querySelector(selector);
    const elements = {
      profile: element("#fl-profile"),
      canvasBackground: element("#fl-canvas-background"),
      canvasColorControl: element(
        '[data-color-target="canvas_background"]'
      ),
      brightness: element("#fl-brightness"),
      effectMode: element("#fl-effect-mode"),
      effectSettings: element("#fl-effect-settings"),
      effectDirection: element("#fl-effect-direction"),
      effectSpeed: element("#fl-effect-speed"),
      effectDescription: element("#fl-effect-description"),
      foregroundEffectNote: element("#fl-foreground-effect-note"),
      layer: element("#fl-region-layer"),
      canvas: element("#fl-editor-canvas"),
      regionValidationSummary: element("#fl-region-validation-summary"),
      inspector: element("#fl-region-inspector"),
      noSelection: element("#fl-no-selection"),
      selectedPanel: element("#fl-selected-region"),
      regionValidationDetails: element("#fl-region-validation-details"),
      name: element("#fl-region-name"),
      enabled: element("#fl-region-enabled"),
      useBackground: element("#fl-use-region-background"),
      background: element("#fl-region-background"),
      backgroundColorControl: element(
        '[data-color-target="region_background"]'
      ),
      widget: element("#fl-widget-type"),
      x: element("#fl-x"),
      y: element("#fl-y"),
      width: element("#fl-width"),
      height: element("#fl-height"),
      settings: element("#fl-widget-settings"),
      state: element("#fl-editor-state"),
      message: element("#fl-editor-message"),
      errors: element("#fl-validation-errors"),
      preview: element("#draft-preview"),
      previewState: element("#fl-preview-state"),
      apply: element("#fl-apply"),
      save: element("#fl-save"),
      validate: element("#fl-validate"),
      reload: element("#fl-reload"),
      add: element("#fl-add-region"),
      duplicate: element("#fl-duplicate-region"),
      remove: element("#fl-delete-region"),
      earlier: element("#fl-region-earlier"),
      later: element("#fl-region-later"),
      toggle: element("#fl-toggle-region"),
      presets: element("#fl-presets"),
      presetBrowser: element("#fl-preset-browser"),
      presetList: element("#fl-preset-list"),
      createPreset: element("#fl-create-preset"),
      previewPreset: element("#fl-preview-preset"),
      cancelPreset: element("#fl-cancel-preset"),
      importProfile: element("#fl-import-profile"),
      exportProfile: element("#fl-export-profile"),
      profileFile: element("#fl-profile-file"),
      importConfig: element("#fl-import-config"),
      exportConfig: element("#fl-export-config"),
      configFile: element("#fl-config-file"),
      createBackup: element("#fl-create-backup"),
      backupSelect: element("#fl-backup-select"),
      restoreBackup: element("#fl-restore-backup"),
      deleteBackup: element("#fl-delete-backup"),
    };
    const previewContext = elements.preview.getContext("2d");
    const state = {
      metadata: null,
      defaults: null,
      applied: null,
      draft: null,
      activeRevision: null,
      savedRevision: null,
      appliedDirty: false,
      migrationPending: false,
      draftDirty: false,
      selectedRegionId: null,
      localValidation: null,
      serverValidation: "idle",
      serverErrors: [],
      resolvedFonts: {},
      fontOptions: {},
      fontFit: {},
      conflict: false,
      submitting: false,
      lastValidPreview: null,
      previewPolicy: "static",
      validationToken: 0,
      inputErrors: new Map(),
      pointerOperation: null,
      disconnected: false,
      pendingRemote: null,
      presets: [],
      selectedPresetId: null,
      backups: [],
      pendingFullImportBackup: false,
      backupLoading: false,
      regionBackgroundColors: new Map(),
    };
    let validationTimer = null;
    let animationTimer = null;
    let reconciliationTimer = null;
    const metricAvailability = documentObject.querySelector(
      "#metric-availability"
    );
    let renderedMetricAvailability = null;

    function renderMetricAvailability() {
      if (!metricAvailability || !state.metadata) return;
      const labels = {
        available: "Available",
        awaiting_initial_delta: "Awaiting sample",
        awaiting_initial_cpu_delta: "Awaiting sample",
        unavailable: "Unavailable",
      };
      const signature = fingerprint(
        (state.metadata.metric_definitions || []).map((metric) => ({
          label: metric.label,
          availability: metric.availability,
        }))
      );
      if (signature === renderedMetricAvailability) return;
      renderedMetricAvailability = signature;
      const rows = (state.metadata.metric_definitions || []).map((metric) => {
        const row = documentObject.createElement("div");
        const term = documentObject.createElement("dt");
        const status = documentObject.createElement("dd");
        term.textContent = metric.label;
        status.textContent = labels[metric.availability] || "Unavailable";
        status.dataset.state = metric.availability;
        row.append(term, status);
        return row;
      });
      metricAvailability.replaceChildren(...rows);
    }

    async function request(path, options = {}) {
      let response;
      try {
        response = await fetchFunction(`${API_PREFIX}${path}`, options);
      } catch (error) {
        throw new EditorRequestError("Daemon unavailable.", null,
          "unavailable", []);
      }
      let payload = {};
      try {
        payload = JSON.parse(await response.text());
      } catch (error) {
        throw new EditorRequestError("The server response was invalid.",
          response.status, "invalid_response", []);
      }
      if (!response.ok) {
        throw new EditorRequestError(
          typeof payload.message === "string"
            ? payload.message : "The request failed.",
          response.status,
          payload.code,
          payload.field_errors,
        );
      }
      return payload;
    }

    function activeProfile() {
      return state.draft.profiles[state.draft.active_profile];
    }

    function selectedRegion() {
      return activeProfile().regions.find(
        (region) => region.id === state.selectedRegionId,
      ) || null;
    }

    function replaceActiveProfile(profile) {
      state.draft.profiles[state.draft.active_profile] = profile;
    }

    function setMessage(message, kind = "") {
      elements.message.textContent = message;
      elements.message.className = `message ${kind}`.trim();
    }

    function localErrorMessage(error) {
      return {
        overlap: "Regions overlap.",
        out_of_bounds: "A region is outside the 90×10 canvas.",
        invalid_dimensions: "Region geometry must use positive integer pixels.",
        duplicate_region_id: "Region identifiers must be unique.",
        invalid_region_id: "A region identifier is invalid.",
        invalid_color: "Use a six-digit hexadecimal color.",
        wrong_widget_settings: "Widget settings do not match the widget type.",
        region_limit: "The region limit has been reached.",
        unknown_display_effect: "Choose an available display effect.",
        invalid_effect_direction: "Choose an available effect direction.",
        invalid_effect_speed: "Use a valid display-effect speed.",
      }[error.category] || "The draft is not valid.";
    }

    function serverErrorMessage(error) {
      return {
        unsupported_glyph: "The selected font does not support this text.",
        unknown_font: "The selected font is unavailable.",
        content_too_tall: "The widget content is too tall for this region.",
        content_too_wide: "The widget content is too wide for this region.",
        time_zone_unavailable: "The named time zone is unavailable.",
        renderer_invariant: "The widget settings are inconsistent.",
        unexpected_renderer_failure: "The widget could not be rendered.",
        unknown_display_effect: "Choose an available display effect.",
        invalid_effect_direction: "Choose an available effect direction.",
        invalid_effect_speed: "Use a valid display-effect speed.",
        effect_invariant: "The display effect could not be rendered safely.",
      }[error.category] || "Server validation failed.";
    }

    function updateDerivedState() {
      state.draftDirty = Boolean(
        state.applied && fingerprint(state.draft) !== fingerprint(state.applied)
      );
      state.localValidation = validateLocalCandidate(
        state.draft,
        state.metadata || {},
      );
    }

    function mutate(mutator, {validate = true} = {}) {
      if (state.submitting) return;
      const candidate = clone(state.draft);
      mutator(candidate);
      state.draft = candidate;
      state.serverErrors = [];
      state.resolvedFonts = {};
      state.fontOptions = {};
      state.fontFit = {};
      state.inputErrors.clear();
      if (!state.conflict) setMessage("");
      updateDerivedState();
      render();
      if (validate) scheduleValidation();
    }

    function renderProfileControls() {
      const options = Object.keys(state.draft.profiles).map((name) => {
        const option = documentObject.createElement("option");
        option.value = name;
        option.textContent = name;
        option.selected = name === state.draft.active_profile;
        return option;
      });
      elements.profile.replaceChildren(...options);
      const profile = activeProfile();
      synchronizeColorControl(
        elements.canvasColorControl,
        profile.canvas.background_color,
      );
      if (!elements.canvasColorControl) {
        elements.canvasBackground.value = profile.canvas.background_color;
      }
      elements.brightness.value = profile.brightness === null
        ? "" : String(profile.brightness);
      const effectMetadata = state.metadata.display_effect;
      const effect = profile.effect || effectDefault(state.metadata);
      if (elements.effectMode && effectMetadata) {
        elements.effectMode.replaceChildren(...effectMetadata.modes.map(
          (definition) => {
            const option = documentObject.createElement("option");
            option.value = definition.id;
            option.textContent = definition.label;
            return option;
          },
        ));
        elements.effectMode.value = effect.mode;
        elements.effectDirection.replaceChildren(...effectMetadata.directions.map(
          (definition) => {
            const option = documentObject.createElement("option");
            option.value = definition.id;
            option.textContent = definition.label;
            return option;
          },
        ));
        const rainbow = foregroundRainbowActive(profile);
        elements.effectSettings.hidden = !rainbow;
        elements.effectDirection.value = rainbow
          ? effect.direction : effectMetadata.default_direction;
        elements.effectSpeed.min = String(effectMetadata.speed_minimum);
        elements.effectSpeed.max = String(effectMetadata.speed_maximum);
        elements.effectSpeed.step = String(effectMetadata.speed_step);
        elements.effectSpeed.value = rainbow && effect.speed !== null
          ? String(effect.speed) : String(effectMetadata.default_speed);
        elements.effectDescription.textContent = effectSpeedDescription(
          rainbow ? effect.speed : effectMetadata.default_speed,
        );
      }
    }

    function renderPresetControls() {
      if (!elements.presetList) return;
      const options = state.presets.map((preset) => {
        const button = documentObject.createElement("button");
        button.type = "button";
        button.className = "preset-option";
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected",
          String(preset.id === state.selectedPresetId));
        const name = documentObject.createElement("strong");
        name.textContent = preset.name;
        const description = documentObject.createElement("span");
        description.className = "preset-description";
        description.textContent = preset.description;
        const count = documentObject.createElement("span");
        count.textContent = `${preset.region_count} region${
          preset.region_count === 1 ? "" : "s"}`;
        button.append(name, description, count);
        button.addEventListener("click", () => {
          state.selectedPresetId = preset.id;
          renderPresetControls();
        });
        return button;
      });
      elements.presetList.replaceChildren(...options);
      const unavailable = !state.selectedPresetId || state.submitting;
      if (elements.createPreset) elements.createPreset.disabled = unavailable;
      if (elements.previewPreset) elements.previewPreset.disabled = unavailable;
    }

    function renderBackups() {
      if (!elements.backupSelect) return;
      const selected = elements.backupSelect.value;
      const options = state.backups.map((backup) => {
        const option = documentObject.createElement("option");
        option.value = backup.id;
        option.textContent = `${backup.name} · ${backup.created_at}`;
        return option;
      });
      elements.backupSelect.replaceChildren(...options);
      if (state.backups.some((item) => item.id === selected)) {
        elements.backupSelect.value = selected;
      }
    }

    function renderCanvas() {
      const profile = activeProfile();
      elements.canvas.style.setProperty(
        "--canvas-background",
        profile.canvas.background_color,
      );
      const invalidIds = new Set(state.localValidation.invalidRegionIds);
      for (const error of state.serverErrors) {
        if (error.region_id) invalidIds.add(error.region_id);
      }
      const compactMessages = [];
      const regions = profile.regions.map((region) => {
        const regionErrors = regionValidationErrors(
          state.localValidation.errors,
          state.serverErrors,
          region.id,
        );
        const overlay = documentObject.createElement("div");
        overlay.className = [
          "layout-region",
          region.id === state.selectedRegionId ? "selected" : "",
          !region.enabled ? "disabled" : "",
          invalidIds.has(region.id) ? "invalid" : "",
        ].filter(Boolean).join(" ");
        overlay.tabIndex = 0;
        overlay.setAttribute("role", "button");
        overlay.setAttribute("aria-label",
          `${region.name || "Region"}: ${region.widget.type}`);
        overlay.setAttribute("aria-pressed",
          String(region.id === state.selectedRegionId));
        overlay.setAttribute("aria-invalid", String(regionErrors.length > 0));
        overlay.dataset.regionId = region.id;
        overlay.style.setProperty("--viewer-x",
          String(logicalToViewerX(region.x, region.width)));
        overlay.style.setProperty("--region-y", String(region.y));
        overlay.style.setProperty("--region-width", String(region.width));
        overlay.style.setProperty("--region-height", String(region.height));
        overlay.style.setProperty("--region-background",
          region.background_color || "transparent");
        const label = documentObject.createElement("span");
        label.className = "layout-region-label";
        label.textContent = state.metadata.widget_definitions.find(
          (definition) => definition.type === region.widget.type,
        )?.label || region.widget.type;
        overlay.append(label);
        if (regionErrors.length > 0) {
          const explanation = validationExplanation(regionErrors[0], profile);
          const messageId = `fl-region-error-${region.id}`;
          overlay.setAttribute("aria-describedby", messageId);
          if (region.width >= 24 && region.height >= 4) {
            const badge = documentObject.createElement("span");
            badge.id = messageId;
            badge.className = "region-validation-badge";
            badge.setAttribute("role", "note");
            badge.textContent = `Error · ${explanation}`;
            badge.addEventListener("pointerdown", (event) => {
              event.preventDefault();
              event.stopPropagation();
              state.selectedRegionId = region.id;
              render();
            });
            badge.addEventListener("click", (event) => {
              event.preventDefault();
              event.stopPropagation();
              state.selectedRegionId = region.id;
              render();
            });
            overlay.append(badge);
          } else {
            compactMessages.push({region, explanation, messageId});
          }
        }
        for (const handle of ["nw", "ne", "sw", "se"]) {
          const resize = documentObject.createElement("span");
          resize.className = `resize-handle resize-${handle}`;
          resize.dataset.resize = handle;
          resize.setAttribute("aria-hidden", "true");
          overlay.append(resize);
        }
        overlay.addEventListener("click", (event) => {
          event.stopPropagation();
          if (event.detail !== 0) return;
          state.selectedRegionId = toggleRegionSelection(
            state.selectedRegionId,
            region.id,
          );
          render();
        });
        overlay.addEventListener("keydown", (event) => {
          if (!["Enter", " "].includes(event.key)) return;
          event.preventDefault();
          state.selectedRegionId = toggleRegionSelection(
            state.selectedRegionId,
            region.id,
          );
          render();
        });
        overlay.addEventListener("pointerdown", (event) => {
          beginPointerOperation(event, region, event.target.dataset.resize || null);
        });
        return overlay;
      });
      elements.layer.replaceChildren(...regions);
      elements.regionValidationSummary?.replaceChildren(
        ...compactMessages.map(({region, explanation, messageId}) => {
          const button = documentObject.createElement("button");
          button.id = messageId;
          button.type = "button";
          button.className = "region-validation-summary-item";
          button.textContent = `Error · ${region.name || "Region"}: ${explanation}`;
          button.addEventListener("click", () => {
            state.selectedRegionId = region.id;
            render();
          });
          return button;
        }),
      );
    }

    function fieldValue(id, value) {
      const control = element(id);
      if (control) control.value = value;
    }

    function checkboxValue(id, value) {
      const control = element(id);
      if (control) control.checked = Boolean(value);
    }

    function renderWidgetSettings(region) {
      for (const panel of elements.settings.querySelectorAll(
        "[data-widget-panel]"
      )) {
        panel.hidden = panel.dataset.widgetPanel !== region.widget.type;
      }
      const settings = region.widget.settings;
      const temperatureUnitSelect = elements.settings.querySelector?.(
        '[data-widget-panel="system_metric"] [data-temperature-unit-select]',
      );
      if (temperatureUnitSelect) {
        const options = (state.metadata.temperature_unit_definitions || [])
          .map((definition) => {
            const option = documentObject.createElement("option");
            option.value = definition.id;
            option.textContent = definition.label;
            return option;
          });
        temperatureUnitSelect.replaceChildren(...options);
      }
      const optionStatuses = new Map(
        (state.fontOptions[region.id] || []).map((item) => [item.id, item]),
      );
      for (const select of elements.settings.querySelectorAll(
        "[data-font-select]"
      )) {
        if (select.closest("[data-widget-panel]").dataset.widgetPanel
            !== region.widget.type) continue;
        const options = (state.metadata.font_definitions || []).map(
          (definition) => {
            const option = documentObject.createElement("option");
            option.value = definition.id;
            const status = optionStatuses.get(definition.id);
            const reason = fontOptionReason(status);
            option.textContent = reason
              ? `${definition.label} — ${reason}` : definition.label;
            option.title = reason;
            option.disabled = Boolean(
              reason && definition.id !== settings.font
            );
            return option;
          },
        );
        select.replaceChildren(...options);
      }
      for (const select of elements.settings.querySelectorAll(
        "[data-metric-select]"
      )) {
        const barOnly = select.dataset.metricSelect === "bar";
        const groups = metricDefinitionGroups(
          state.metadata.metric_definitions || [],
          barOnly ? state.metadata.bar_metric_ids || [] : null,
        ).map((group) => {
          const optgroup = documentObject.createElement("optgroup");
          optgroup.label = group.label;
          const options = group.metrics.map((metric) => {
            const option = documentObject.createElement("option");
            option.value = metric.id;
            option.textContent = metric.label;
            return option;
          });
          optgroup.append(...options);
          return optgroup;
        });
        select.replaceChildren(...groups);
      }
      for (const control of elements.settings.querySelectorAll("[data-setting]")) {
        if (control.closest("[data-widget-panel]").dataset.widgetPanel
            !== region.widget.type) continue;
        const value = settings[control.dataset.setting];
        if (control.type === "checkbox") control.checked = Boolean(value);
        else control.value = value === null || value === undefined ? "" : String(value);
      }
      for (const control of elements.settings.querySelectorAll(
        "[data-color-control][data-color-setting]"
      )) {
        if (control.closest("[data-widget-panel]").dataset.widgetPanel
            !== region.widget.type) continue;
        synchronizeColorControl(
          control,
          settings[control.dataset.colorSetting],
        );
      }
      const widgetPanel = elements.settings.querySelector?.(
        `[data-widget-panel="${region.widget.type}"]`,
      );
      const resolvedLabel = widgetPanel?.querySelector?.(
        "[data-resolved-font]",
      );
      if (resolvedLabel) {
        resolvedLabel.textContent = resolvedFontExplanation(
          settings.font,
          state.resolvedFonts[region.id],
          state.metadata.font_definitions,
          state.serverValidation,
        );
      }
      const metricPanel = elements.settings.querySelector?.(
        '[data-widget-panel="system_metric"]',
      );
      if (metricPanel && region.widget.type === "system_metric") {
        const unitControl = metricPanel.querySelector(
          "[data-temperature-unit-select]",
        );
        if (unitControl) unitControl.value = settings.temperature_unit;
        const labelVisible = settings.display_mode === "label_and_value";
        metricPanel.querySelector("[data-metric-label-field]")
          ?.toggleAttribute("hidden", !labelVisible);
        const metricDefinition = state.metadata.metric_definitions?.find(
          (item) => item.id === settings.metric,
        );
        metricPanel.querySelector("[data-metric-precision-field]")
          ?.toggleAttribute("hidden", metricDefinition?.value_type === "duration");
        const temperature = temperatureUnitVisible(
          settings.metric,
          state.metadata.metric_definitions,
        );
        metricPanel.querySelector("[data-temperature-unit-field]")
          ?.toggleAttribute("hidden", !temperature);
        const example = metricPanel.querySelector("[data-temperature-example]");
        if (example) {
          example.hidden = !temperature;
          example.textContent = temperature
            ? `Example: ${temperatureFormatExample(
              settings,
              state.metadata.temperature_unit_definitions,
            )}`
            : "";
        }
      }
      const direction = elements.settings.querySelector?.("[data-bar-direction]");
      if (direction && region.widget.type === "metric_bar") {
        const vertical = settings.orientation === "vertical";
        const labels = vertical
          ? ["Bottom to top", "Top to bottom"]
          : ["Left to right", "Right to left"];
        Array.from(direction.options).forEach((option, index) => {
          option.textContent = labels[index];
        });
        elements.settings.querySelector?.("[data-bar-border-color-field]")
          ?.toggleAttribute("hidden", !settings.border_enabled);
      }
    }

    function renderInspector() {
      const region = selectedRegion();
      elements.noSelection.hidden = Boolean(region);
      elements.selectedPanel.hidden = !region;
      if (!region) {
        if (elements.foregroundEffectNote) {
          elements.foregroundEffectNote.hidden = true;
        }
        if (elements.regionValidationDetails) {
          elements.regionValidationDetails.hidden = true;
          elements.regionValidationDetails.replaceChildren();
        }
        return;
      }
      const regionErrors = regionValidationErrors(
        state.localValidation.errors,
        state.serverErrors,
        region.id,
      );
      if (elements.regionValidationDetails) {
        const detailItems = regionErrors.flatMap((error, index) => {
            const item = documentObject.createElement("p");
            item.className = index === 0
              ? "region-validation-primary" : "region-validation-secondary";
            item.textContent = `${index === 0 ? "Error" : "Also"} · ${
              validationExplanation(error, activeProfile())
            }`;
            return [item, ...fontGuidanceLines(error).map(
              (line) => {
                const guidance = documentObject.createElement("p");
                guidance.className = "region-validation-secondary";
                guidance.textContent = line;
                return guidance;
              },
            )];
          });
        const analysis = state.fontFit[region.id]
          || regionErrors.find((error) => error.font_fit)?.font_fit;
        const actions = documentObject.createElement("div");
        actions.className = "font-fit-actions";
        const addAction = (label, action) => {
          const button = documentObject.createElement("button");
          button.type = "button";
          button.className = "secondary small";
          button.textContent = label;
          button.addEventListener("click", action);
          actions.append(button);
        };
        if (analysis?.largest_fitting_font
            && region.widget.settings.font !== "auto") {
          addAction("Use Auto", () => mutate((candidate) => {
            candidate.profiles[candidate.active_profile].regions.find(
              (item) => item.id === region.id,
            ).widget.settings.font = "auto";
          }));
        }
        const alternative = analysis?.best_smaller_alternative;
        if (alternative && alternative !== region.widget.settings.font) {
          addAction(`Use ${alternative.replace("x", "×")}`, () => mutate(
            (candidate) => {
              candidate.profiles[candidate.active_profile].regions.find(
                (item) => item.id === region.id,
              ).widget.settings.font = alternative;
            },
          ));
        }
        const resize = fontResizeSuggestion(activeProfile(), region, analysis);
        if (resize) {
          addAction(`Resize to ${resize.width} × ${resize.height}`, () => mutate(
            (candidate) => {
              const target = candidate.profiles[
                candidate.active_profile
              ].regions.find((item) => item.id === region.id);
              target.width = resize.width;
              target.height = resize.height;
            },
          ));
        }
        if (actions.children.length > 0) detailItems.push(actions);
        elements.regionValidationDetails.hidden = detailItems.length === 0;
        elements.regionValidationDetails.replaceChildren(...detailItems);
      }
      elements.name.value = region.name || "";
      elements.enabled.checked = region.enabled;
      elements.useBackground.checked = region.background_color !== null;
      const backgroundKey = `${state.draft.active_profile}:${region.id}`;
      if (COLOR.test(region.background_color || "")) {
        state.regionBackgroundColors.set(
          backgroundKey,
          region.background_color.toUpperCase(),
        );
      }
      const backgroundValue = region.background_color
        || state.regionBackgroundColors.get(backgroundKey) || "#000000";
      synchronizeColorControl(
        elements.backgroundColorControl,
        backgroundValue,
      );
      elements.background.value = colorControlState(backgroundValue).picker;
      for (const control of elements.backgroundColorControl?.querySelectorAll(
        "input"
      ) || [elements.background]) {
        control.disabled = region.background_color === null;
      }
      elements.widget.value = region.widget.type;
      elements.x.value = String(region.x);
      elements.y.value = String(region.y);
      elements.width.value = String(region.width);
      elements.height.value = String(region.height);
      renderWidgetSettings(region);
      if (elements.foregroundEffectNote) {
        elements.foregroundEffectNote.textContent = region.widget.type
          === "metric_bar"
          ? "Foreground Rainbow overrides fill and enabled border colors while active. Empty color remains unchanged."
          : "Foreground Rainbow overrides this color while the effect is active.";
        elements.foregroundEffectNote.hidden = !(
          foregroundRainbowActive(activeProfile())
          && region.widget.type !== "blank"
        );
      }
    }

    function renderErrors() {
      const messages = [];
      const controls = [
        elements.canvasBackground,
        elements.x, elements.y, elements.width, elements.height,
        elements.name, elements.enabled, elements.background, elements.widget,
        elements.effectMode, elements.effectDirection, elements.effectSpeed,
        ...elements.settings.querySelectorAll("[data-setting]"),
        ...elements.settings.querySelectorAll("[data-color-control] input"),
      ];
      for (const control of controls.filter(Boolean)) {
        control.removeAttribute("aria-invalid");
      }
      for (const field of state.inputErrors.keys()) {
        elements[field]?.setAttribute("aria-invalid", "true");
      }
      const markErrorControl = (error) => {
        const region = selectedRegion();
        if (error.region_id && error.region_id !== region?.id) return;
        const field = String(error.field || "");
        if (error.category === "content_too_wide") {
          elements.width.setAttribute("aria-invalid", "true");
        }
        if (error.category === "content_too_tall") {
          elements.height.setAttribute("aria-invalid", "true");
        }
        if (Number.isInteger(error.minimum_width)
            && Number.isInteger(error.actual_width)
            && error.actual_width < error.minimum_width) {
          elements.width.setAttribute("aria-invalid", "true");
        }
        if (Number.isInteger(error.minimum_height)
            && Number.isInteger(error.actual_height)
            && error.actual_height < error.minimum_height) {
          elements.height.setAttribute("aria-invalid", "true");
        }
        if (error.category === "invalid_canvas"
            && field.includes(state.draft.active_profile)) {
          for (const input of elements.canvasColorControl?.querySelectorAll(
            "input"
          ) || [elements.canvasBackground]) {
            input.setAttribute("aria-invalid", "true");
          }
          if (elements.canvasColorControl) {
            elements.canvasColorControl.dataset.invalid = "true";
          }
        }
        if (field === "geometry") {
          for (const control of [
            elements.x, elements.y, elements.width, elements.height,
          ]) control.setAttribute("aria-invalid", "true");
          return;
        }
        const direct = {
          id: elements.name,
          name: elements.name,
          enabled: elements.enabled,
          background_color: elements.background,
          widget: elements.widget,
        }[field];
        if (direct) direct.setAttribute("aria-invalid", "true");
        if (field.endsWith("effect.mode")) {
          elements.effectMode?.setAttribute("aria-invalid", "true");
        }
        if (field.endsWith("effect.direction")) {
          elements.effectDirection?.setAttribute("aria-invalid", "true");
        }
        if (field.endsWith("effect.speed")) {
          elements.effectSpeed?.setAttribute("aria-invalid", "true");
        }
        if (field === "background_color" && elements.backgroundColorControl) {
          elements.backgroundColorControl.dataset.invalid = "true";
          for (const input of elements.backgroundColorControl.querySelectorAll(
            "input"
          )) input.setAttribute("aria-invalid", "true");
        }
        for (const control of elements.settings.querySelectorAll(
          "[data-setting]"
        )) {
          if (field === control.dataset.setting
              || field.endsWith(`.${control.dataset.setting}`)) {
            control.setAttribute("aria-invalid", "true");
          }
        }
        for (const colorControl of elements.settings.querySelectorAll(
          "[data-color-control][data-color-setting]"
        )) {
          if (field === colorControl.dataset.colorSetting
              || field.endsWith(`.${colorControl.dataset.colorSetting}`)) {
            colorControl.dataset.invalid = "true";
            for (const input of colorControl.querySelectorAll("input")) {
              input.setAttribute("aria-invalid", "true");
            }
          }
        }
      };
      for (const message of state.inputErrors.values()) {
        if (!messages.includes(message)) messages.push(message);
      }
      for (const error of state.localValidation.errors) {
        markErrorControl(error);
        const message = localErrorMessage(error);
        if (!messages.includes(message)) messages.push(message);
      }
      for (const error of state.serverErrors) {
        markErrorControl(error);
        const message = serverErrorMessage(error);
        if (!messages.includes(message)) messages.push(message);
      }
      elements.errors.replaceChildren(...messages.map((message) => {
        const item = documentObject.createElement("li");
        item.textContent = message;
        return item;
      }));
    }

    function updateButtons() {
      const valid = state.localValidation.valid
        && state.inputErrors.size === 0
        && state.serverValidation === "valid";
      const selected = selectedRegion();
      const profile = activeProfile();
      const busy = state.submitting;
      elements.profile.disabled = busy;
      for (const control of elements.canvasColorControl?.querySelectorAll(
        "input"
      ) || [elements.canvasBackground]) control.disabled = busy;
      elements.brightness.disabled = busy;
      if (elements.effectMode) elements.effectMode.disabled = busy;
      if (elements.effectDirection) elements.effectDirection.disabled = busy;
      if (elements.effectSpeed) elements.effectSpeed.disabled = busy;
      elements.inspector.toggleAttribute("inert", busy);
      elements.canvas.setAttribute("aria-disabled", String(busy));
      elements.apply.disabled = busy || !state.draftDirty || !valid;
      elements.apply.disabled = elements.apply.disabled
        || state.conflict || state.disconnected;
      elements.save.disabled = busy || state.conflict || state.disconnected
        || state.draftDirty
        || (!state.appliedDirty && !state.migrationPending);
      elements.validate.disabled = busy || state.inputErrors.size > 0
        || !state.localValidation.valid;
      elements.reload.disabled = busy || !state.draftDirty;
      elements.add.disabled = busy
        || profile.regions.length >= state.metadata.max_regions_per_profile
        || totalRegionCount(state.draft)
          >= state.metadata.max_regions_per_document;
      for (const button of [
        elements.duplicate, elements.remove, elements.earlier,
        elements.later, elements.toggle,
      ]) button.disabled = busy || !selected;
      elements.duplicate.disabled = elements.duplicate.disabled
        || profile.regions.length >= state.metadata.max_regions_per_profile
        || totalRegionCount(state.draft)
          >= state.metadata.max_regions_per_document;
      const profileLimit = Object.keys(state.draft.profiles).length
        >= state.metadata.max_profiles;
      for (const button of root.querySelectorAll("[data-profile-action]")) {
        const action = button.dataset.profileAction;
        button.disabled = busy || (
          ["create", "duplicate"].includes(action) && profileLimit
        ) || (
          action === "duplicate"
          && totalRegionCount(state.draft) + profile.regions.length
            > state.metadata.max_regions_per_document
        ) || (
          action === "delete" && Object.keys(state.draft.profiles).length <= 1
        );
      }
      if (selected) {
        const index = profile.regions.findIndex(
          (region) => region.id === selected.id,
        );
        elements.earlier.disabled = busy || index <= 0;
        elements.later.disabled = busy || index >= profile.regions.length - 1;
        elements.toggle.textContent = selected.enabled ? "Disable" : "Enable";
      }
    }

    function render() {
      if (!state.draft || !state.metadata) return;
      renderMetricAvailability();
      renderProfileControls();
      renderCanvas();
      renderInspector();
      renderErrors();
      renderPresetControls();
      renderBackups();
      const presentation = deriveEditorState(state);
      elements.state.textContent = presentation.label;
      elements.state.className = `editor-state ${presentation.className}`.trim();
      updateButtons();
    }

    function renderPreview(framebufferBase64, label = "Draft") {
      let binary;
      try {
        binary = decodeBase64(framebufferBase64);
      } catch (error) {
        return;
      }
      if (binary.length !== CANVAS_WIDTH * CANVAS_HEIGHT * 3) return;
      const image = previewContext.createImageData(CANVAS_WIDTH, CANVAS_HEIGHT);
      const regions = activeProfile().regions.map((region) => ({
        name: region.name || region.id,
        start_pixel: region.x,
        y: region.y,
        width: region.width,
        height: region.height,
        enabled: region.enabled,
        type: region.widget.type.replaceAll("_", "-"),
      }));
      const viewerRegions = base.placeRegionsForViewer(regions, CANVAS_WIDTH);
      base.copyFramebufferForViewer(
        binary,
        image,
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
        viewerRegions,
      );
      previewContext.putImageData(image, 0, 0);
      elements.previewState.textContent = `${label} · viewer perspective`;
    }

    function clearValidationTimer() {
      if (validationTimer !== null) clearTimeoutFunction(validationTimer);
      validationTimer = null;
      if (animationTimer !== null) clearTimeoutFunction(animationTimer);
      animationTimer = null;
    }

    function stopTimers() {
      clearValidationTimer();
      if (reconciliationTimer !== null) {
        clearTimeoutFunction(reconciliationTimer);
      }
      reconciliationTimer = null;
    }

    function scheduleReconciliation() {
      if (reconciliationTimer !== null) {
        clearTimeoutFunction(reconciliationTimer);
      }
      reconciliationTimer = setTimeoutFunction(() => {
        reconciliationTimer = null;
        void reconcileConfiguration();
      }, 2000);
    }

    async function reconcileConfiguration() {
      try {
        const result = await request("/config");
        state.disconnected = false;
        const changed = (
          result.active_revision !== state.activeRevision
          || result.saved_revision !== state.savedRevision
        );
        if (changed && state.draftDirty) {
          state.conflict = true;
          state.pendingRemote = clone(result);
          setMessage(
            "A newer server revision is available. Discard the draft to reload it.",
            "warning",
          );
        } else if (changed) {
          const selectedRegionId = state.selectedRegionId;
          state.applied = clone(result.configuration);
          state.draft = clone(result.configuration);
          state.activeRevision = result.active_revision;
          state.savedRevision = result.saved_revision;
          state.appliedDirty = Boolean(result.dirty);
          state.migrationPending = Boolean(result.migration_pending);
          state.pendingRemote = null;
          state.conflict = false;
          state.serverErrors = [];
          state.resolvedFonts = {};
          state.fontOptions = {};
          state.fontFit = {};
          state.selectedRegionId = selectionForProfile(
            selectedRegionId,
            activeProfile(),
          );
          updateDerivedState();
          state.serverValidation = "idle";
          render();
          await validateDraft();
        } else {
          state.appliedDirty = Boolean(result.dirty);
          state.migrationPending = Boolean(result.migration_pending);
          render();
        }
      } catch (error) {
        state.disconnected = true;
        setMessage("Daemon unavailable. Reconnecting…", "warning");
        render();
      } finally {
        scheduleReconciliation();
      }
    }

    function scheduleAnimatedPreview() {
      if (animationTimer !== null) clearTimeoutFunction(animationTimer);
      if (!["continuous", "half_second", "second_boundary"].includes(
        state.previewPolicy
      )) return;
      const hidden = Boolean(documentObject.hidden);
      const delay = hidden ? 4000 : (
        state.previewPolicy === "continuous" ? 500 : 1000
      );
      animationTimer = setTimeoutFunction(() => {
        animationTimer = null;
        if (state.localValidation.valid && state.serverValidation === "valid") {
          void validateDraft({animation: true});
        }
      }, delay);
    }

    async function validateDraft({animation = false} = {}) {
      clearValidationTimer();
      updateDerivedState();
      if (!state.localValidation.valid || state.inputErrors.size > 0) {
        state.serverValidation = "invalid";
        render();
        return false;
      }
      const token = ++state.validationToken;
      if (!animation) state.serverValidation = "pending";
      render();
      try {
        const result = await request("/validate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({configuration: state.draft}),
        });
        if (token !== state.validationToken) return false;
        state.serverValidation = "valid";
        state.serverErrors = [];
        state.resolvedFonts = result.resolved_fonts || {};
        state.fontOptions = result.font_options || {};
        state.fontFit = result.font_fit || {};
        state.inputErrors.clear();
        state.lastValidPreview = result.framebuffer_base64;
        state.previewPolicy = result.update_policy;
        renderPreview(result.framebuffer_base64,
          state.draftDirty ? "Draft" : "Applied");
        render();
        scheduleAnimatedPreview();
        return true;
      } catch (error) {
        if (token !== state.validationToken) return false;
        state.serverValidation = "invalid";
        state.serverErrors = error instanceof EditorRequestError
          ? error.fieldErrors : [];
        state.fontFit = {};
        state.fontOptions = {};
        state.resolvedFonts = {};
        for (const fieldError of state.serverErrors) {
          if (!fieldError.region_id || !fieldError.font_fit) continue;
          state.fontFit[fieldError.region_id] = fieldError.font_fit;
          state.fontOptions[fieldError.region_id] = (
            fieldError.font_fit.candidates || []
          ).map((item) => ({
            id: item.font_id,
            available: item.overall_fit,
            category: {
              too_wide: "content_too_wide",
              too_tall: "content_too_tall",
              unsupported_characters: "unsupported_glyph",
              unsupported_by_widget: "unsupported_by_widget",
            }[item.rejection_category] || null,
            minimum_width: item.minimum_width,
            minimum_height: item.minimum_height,
          }));
          if (fieldError.font_fit.resolved_auto_font) {
            state.resolvedFonts[fieldError.region_id] =
              fieldError.font_fit.resolved_auto_font;
          }
        }
        if (state.lastValidPreview) renderPreview(state.lastValidPreview,
          "Last valid");
        setMessage(
          error instanceof EditorRequestError
            ? error.message : "Validation failed.",
          "error",
        );
        render();
        return false;
      }
    }

    function scheduleValidation() {
      clearValidationTimer();
      state.validationToken += 1;
      updateDerivedState();
      if (!state.localValidation.valid || state.inputErrors.size > 0) {
        state.serverValidation = "invalid";
        render();
        return;
      }
      state.serverValidation = "pending";
      render();
      validationTimer = setTimeoutFunction(() => {
        validationTimer = null;
        void validateDraft();
      }, 350);
    }

    async function applyDraft() {
      if (state.submitting || state.serverValidation !== "valid"
          || state.conflict || state.disconnected
          || state.inputErrors.size > 0
          || !state.localValidation.valid || !state.draftDirty) return false;
      state.submitting = true;
      render();
      try {
        const result = await request("/apply", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            configuration: state.draft,
            expected_revision: state.activeRevision,
          }),
        });
        state.applied = clone(result.configuration);
        state.draft = clone(result.configuration);
        state.activeRevision = result.active_revision;
        state.appliedDirty = Boolean(result.dirty);
        state.draftDirty = false;
        state.conflict = false;
        state.pendingRemote = null;
        state.serverValidation = "valid";
        updateDerivedState();
        setMessage("Draft applied to the runtime.", "success");
        render();
        return true;
      } catch (error) {
        if (error instanceof EditorRequestError && error.status === 409) {
          state.conflict = true;
          setMessage(
            "The server revision changed. Reload before applying.",
            "error",
          );
        } else {
          setMessage("The draft could not be applied.", "error");
        }
        render();
        return false;
      } finally {
        state.submitting = false;
        render();
      }
    }

    async function saveApplied() {
      if (state.submitting || state.conflict || state.disconnected
          || state.draftDirty
          || (!state.appliedDirty && !state.migrationPending)) return false;
      state.submitting = true;
      render();
      try {
        if (state.pendingFullImportBackup) {
          await request("/backups", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name: "Before configuration import"}),
          });
          state.pendingFullImportBackup = false;
        }
        const result = await request("/config", {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            configuration: state.applied,
            expected_active_revision: state.activeRevision,
            expected_saved_revision: state.savedRevision,
            apply: false,
          }),
        });
        state.savedRevision = result.saved_revision;
        state.appliedDirty = Boolean(result.dirty);
        state.migrationPending = Boolean(result.migration_pending);
        state.conflict = false;
        state.pendingRemote = null;
        setMessage(result.warning || "Applied configuration saved.",
          result.warning ? "warning" : "success");
        render();
        return true;
      } catch (error) {
        if (error instanceof EditorRequestError && error.status === 409) {
          state.conflict = true;
          setMessage(
            "The saved revision changed. Reload before saving.",
            "error",
          );
        } else {
          setMessage("The applied configuration could not be saved.", "error");
        }
        render();
        return false;
      } finally {
        state.submitting = false;
        render();
      }
    }

    async function reload() {
      const result = await request("/config");
      const selectedRegionId = state.selectedRegionId;
      state.applied = clone(result.configuration);
      state.draft = clone(result.configuration);
      state.activeRevision = result.active_revision;
      state.savedRevision = result.saved_revision;
      state.appliedDirty = Boolean(result.dirty);
      state.migrationPending = Boolean(result.migration_pending);
      state.selectedRegionId = selectionForProfile(
        selectedRegionId,
        activeProfile(),
      );
      state.conflict = false;
      state.serverErrors = [];
      state.resolvedFonts = {};
      state.fontOptions = {};
      state.fontFit = {};
      updateDerivedState();
      state.serverValidation = "idle";
      render();
      await validateDraft();
    }

    function updateSelectedRegion(mutator) {
      mutate((candidate) => {
        const profile = candidate.profiles[candidate.active_profile];
        const index = profile.regions.findIndex(
          (region) => region.id === state.selectedRegionId,
        );
        if (index >= 0) mutator(profile.regions[index]);
      });
    }

    function parseStrictInteger(control) {
      return /^-?(0|[1-9][0-9]*)$/.test(control.value)
        ? Number(control.value) : null;
    }

    function handleInspectorInput(event) {
      const control = event.target;
      if (control.dataset.regionField) {
        const field = control.dataset.regionField;
        if (["x", "y", "width", "height"].includes(field)) {
          const value = parseStrictInteger(control);
          if (value === null) {
            state.inputErrors.set(field, "Use an integer pixel value.");
            state.serverValidation = "invalid";
            render();
            return;
          }
          updateSelectedRegion((region) => {
            region[field] = value;
          });
        } else if (field === "name") {
          updateSelectedRegion((region) => {
            region.name = control.value || null;
          });
        } else if (field === "enabled") {
          updateSelectedRegion((region) => {
            region.enabled = control.checked;
          });
        }
        return;
      }
      if (control.dataset.setting) {
        const key = control.dataset.setting;
        updateSelectedRegion((region) => {
          if (control.type === "checkbox") {
            region.widget.settings[key] = control.checked;
          } else if (control.dataset.kind === "integer") {
            region.widget.settings[key] = parseStrictInteger(control);
          } else if (control.dataset.kind === "number") {
            region.widget.settings[key] = Number(control.value);
          } else {
            region.widget.settings[key] = control.value;
          }
          if (key === "metric") {
            const definition = state.metadata.metric_definitions?.find(
              (item) => item.id === control.value,
            );
            if (region.widget.type === "system_metric" && definition) {
              region.widget.settings.label = definition.default_label;
              region.widget.settings.precision = definition.default_precision;
            }
            if (region.widget.type === "metric_bar" && definition) {
              region.widget.settings.minimum = definition.default_minimum;
              region.widget.settings.maximum = definition.default_maximum;
            }
          }
        });
      }
    }

    function updateColorDraft(control, value) {
      const normalized = normalizeColorDraft(value);
      if (control.dataset.colorTarget === "canvas_background") {
        mutate((candidate) => {
          candidate.profiles[
            candidate.active_profile
          ].canvas.background_color = normalized;
        });
        return;
      }
      if (control.dataset.colorTarget === "region_background") {
        updateSelectedRegion((region) => {
          region.background_color = normalized;
        });
        return;
      }
      const key = control.dataset.colorSetting;
      if (key) {
        updateSelectedRegion((region) => {
          region.widget.settings[key] = normalized;
        });
      }
    }

    function bindColorControls() {
      for (const control of root.querySelectorAll("[data-color-control]")) {
        const picker = control.querySelector('input[type="color"]');
        const hex = control.querySelector("[data-color-hex]");
        picker?.addEventListener("input", () => {
          updateColorDraft(control, picker.value.toUpperCase());
        });
        hex?.addEventListener("input", () => {
          const draft = normalizeColorDraft(hex.value);
          synchronizeColorControl(control, draft);
          updateColorDraft(control, draft);
        });
      }
    }

    function applyGeometryHelper(helper) {
      const presets = {
        full_width: {x: 0, width: 90},
        half_width: {width: 45},
        full_height: {y: 0, height: 10},
        top_half: {y: 0, height: 5},
        bottom_half: {y: 5, height: 5},
        viewer_left: {x: 45, width: 45},
        viewer_right: {x: 0, width: 45},
      };
      if (!presets[helper]) return;
      updateSelectedRegion((region) => Object.assign(region, presets[helper]));
    }

    function beginPointerOperation(event, region, resizeHandle) {
      if (state.submitting) return;
      if (event.button !== undefined && event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      const wasSelected = state.selectedRegionId === region.id;
      state.selectedRegionId = region.id;
      const rect = elements.canvas.getBoundingClientRect();
      state.pointerOperation = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        width: rect.width,
        height: rect.height,
        original: clone(region),
        handle: resizeHandle,
        wasSelected,
        moved: false,
      };
      render();
    }

    function movePointerOperation(event) {
      const operation = state.pointerOperation;
      if (!operation || (event.pointerId !== undefined
          && operation.pointerId !== event.pointerId)) return;
      const dx = Math.round(
        (event.clientX - operation.startX) / operation.width * CANVAS_WIDTH
      );
      const dy = Math.round(
        (event.clientY - operation.startY) / operation.height * CANVAS_HEIGHT
      );
      if (dx !== 0 || dy !== 0) operation.moved = true;
      const geometry = operation.handle
        ? resizeGeometryInViewer(operation.original, operation.handle, dx, dy)
        : moveGeometryInViewer(operation.original, dx, dy);
      updateSelectedRegion((region) => Object.assign(region, geometry));
    }

    function endPointerOperation(event = {}) {
      const operation = state.pointerOperation;
      if (!operation || (event.pointerId !== undefined
          && operation.pointerId !== event.pointerId)) return;
      state.pointerOperation = null;
      if (!operation.moved && operation.wasSelected) {
        state.selectedRegionId = null;
        render();
      }
    }

    function cancelPointerOperation(event = {}) {
      const operation = state.pointerOperation;
      if (!operation || (event.pointerId !== undefined
          && operation.pointerId !== event.pointerId)) return;
      updateSelectedRegion((region) => Object.assign(region, operation.original));
      state.pointerOperation = null;
    }

    function selectedPreset() {
      return state.presets.find(
        (preset) => preset.id === state.selectedPresetId,
      ) || null;
    }

    function confirmDraftTransition(message) {
      if (!hasMeaningfulDraft(state)) return true;
      return typeof windowObject.confirm !== "function"
        || windowObject.confirm(message);
    }

    function replaceDraft(configuration, {fullImport = false} = {}) {
      state.draft = clone(configuration);
      state.selectedRegionId = null;
      state.serverErrors = [];
      state.resolvedFonts = {};
      state.fontOptions = {};
      state.fontFit = {};
      state.inputErrors.clear();
      state.pendingFullImportBackup = fullImport;
      state.conflict = false;
      updateDerivedState();
      state.serverValidation = "idle";
      render();
      void validateDraft();
    }

    async function readImportFile(file) {
      if (!file || !String(file.name || "").toLowerCase().endsWith(".json")) {
        throw new EditorRequestError("Select a JSON file.", 422,
          "unsupported_file_type", []);
      }
      if (file.size === 0) {
        throw new EditorRequestError("The JSON file is empty.", 422,
          "empty_file", []);
      }
      const limit = state.metadata.max_import_bytes || (256 * 1024);
      if (file.size > limit) {
        throw new EditorRequestError("The JSON file is too large.", 413,
          "file_too_large", []);
      }
      if (!encodeBase64) {
        throw new EditorRequestError("File import is unavailable.", null,
          "file_unavailable", []);
      }
      return arrayBufferToBase64(await file.arrayBuffer(), encodeBase64);
    }

    async function importProfileFile(file) {
      if (state.submitting) return false;
      state.submitting = true;
      render();
      try {
        const result = await request("/import/profile/validate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            content_base64: await readImportFile(file),
            existing_profile_names: Object.keys(state.draft.profiles),
            existing_region_count: totalRegionCount(state.draft),
          }),
        });
        renderPreview(result.framebuffer_base64, "Imported profile preview");
        const confirmed = typeof windowObject.confirm !== "function"
          || windowObject.confirm(
            `Add imported profile ${result.profile_name} with ${
              result.region_count} regions?`,
          );
        if (!confirmed) return false;
        const candidate = addImportedProfile(state.draft, result);
        if (!candidate) throw new EditorRequestError(
          "The imported profile cannot be added.", 422,
          "duplicate_profile_name", [],
        );
        state.selectedRegionId = null;
        state.draft = candidate;
        state.serverErrors = [];
        state.resolvedFonts = {};
        state.fontOptions = {};
        state.fontFit = {};
        state.inputErrors.clear();
        updateDerivedState();
        state.serverValidation = "idle";
        render();
        void validateDraft();
        setMessage("Imported profile added to the local draft.", "success");
        return true;
      } catch (error) {
        setMessage(error instanceof EditorRequestError
          ? error.message : "The profile import failed.", "error");
        return false;
      } finally {
        state.submitting = false;
        render();
      }
    }

    async function importConfigurationFile(file) {
      if (state.submitting || !confirmDraftTransition(
        "Discard the current local draft and inspect a full configuration import?"
      )) return false;
      state.submitting = true;
      render();
      try {
        const result = await request("/import/configuration/validate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({content_base64: await readImportFile(file)}),
        });
        const summary = result.summary;
        const confirmed = typeof windowObject.confirm !== "function"
          || windowObject.confirm(
            `Replace the current local draft with schema v${summary.schema_version}, `
            + `${summary.profile_count} profiles, active profile `
            + `${summary.active_profile}? Existing profiles will be replaced.`,
          );
        if (!confirmed) return false;
        replaceDraft(result.configuration, {fullImport: true});
        renderPreview(result.framebuffer_base64, "Imported configuration preview");
        setMessage(
          "Configuration imported as a local draft. Apply, then Save explicitly.",
          "success",
        );
        return true;
      } catch (error) {
        setMessage(error instanceof EditorRequestError
          ? error.message : "The configuration import failed.", "error");
        return false;
      } finally {
        state.submitting = false;
        render();
      }
    }

    async function loadPresets() {
      if (state.submitting) return;
      state.submitting = true;
      render();
      try {
        if (!state.presets.length) {
          const result = await request("/presets");
          state.presets = clone(result.presets);
          state.selectedPresetId = state.presets[0]?.id || null;
        }
        if (elements.presetBrowser) elements.presetBrowser.hidden = false;
        renderPresetControls();
      } finally {
        state.submitting = false;
        render();
      }
    }

    async function loadBackups() {
      if (state.backupLoading) return null;
      state.backupLoading = true;
      try {
        const result = await request("/backups");
        state.backups = clone(result.backups);
        renderBackups();
        return result;
      } finally {
        state.backupLoading = false;
      }
    }

    async function downloadExport(path, fallbackFilename, options = {}) {
      if (state.submitting) return false;
      state.submitting = true;
      render();
      try {
        const response = await fetchFunction(`${API_PREFIX}${path}`, options);
        if (!response.ok) {
          let payload = {};
          try { payload = JSON.parse(await response.text()); } catch (error) {}
          throw new EditorRequestError(payload.message || "Export failed.",
            response.status, payload.code, payload.field_errors);
        }
        const blob = await response.blob();
        const url = windowObject.URL.createObjectURL(blob);
        const anchor = documentObject.createElement("a");
        anchor.href = url;
        anchor.download = fallbackFilename;
        anchor.hidden = true;
        documentObject.body.append(anchor);
        anchor.click();
        anchor.remove();
        windowObject.URL.revokeObjectURL(url);
        return true;
      } catch (error) {
        setMessage(error instanceof EditorRequestError
          ? error.message : "The export failed.", "error");
        return false;
      } finally {
        state.submitting = false;
        render();
      }
    }

    function bind() {
      bindColorControls();
      elements.layer.addEventListener("pointerdown", (event) => {
        const selectedRegionId = selectionForCanvasPointer(
          state.selectedRegionId,
          event.target,
          elements.layer,
        );
        if (selectedRegionId !== state.selectedRegionId) {
          state.selectedRegionId = selectedRegionId;
          render();
        }
      });
      if (!windowObject.addEventListener) {
        elements.layer.addEventListener("pointermove", movePointerOperation);
        elements.layer.addEventListener("pointerup", endPointerOperation);
        elements.layer.addEventListener(
          "pointercancel",
          cancelPointerOperation,
        );
      }
      elements.profile.addEventListener("change", () => {
        const previous = state.draft.active_profile;
        if (elements.profile.value !== previous && !confirmDraftTransition(
          "Discard the current local draft and switch profiles?"
        )) {
          elements.profile.value = previous;
          return;
        }
        const target = elements.profile.value;
        if (state.draftDirty) {
          state.draft = clone(state.applied);
          state.pendingFullImportBackup = false;
        }
        if (!state.draft.profiles[target]) {
          elements.profile.value = state.draft.active_profile;
          render();
          return;
        }
        mutate((candidate) => {
          candidate.active_profile = target;
          state.selectedRegionId = selectionForProfile(
            state.selectedRegionId,
            candidate.profiles[candidate.active_profile],
          );
        });
      });
      elements.brightness.addEventListener("change", () => {
        mutate((candidate) => {
          candidate.profiles[candidate.active_profile].brightness
            = elements.brightness.value === ""
              ? null : Number(elements.brightness.value);
        });
      });
      elements.effectMode?.addEventListener("change", () => {
        mutate((candidate) => {
          const profile = candidate.profiles[candidate.active_profile];
          if (elements.effectMode.value === "foreground_rainbow") {
            const metadata = state.metadata.display_effect;
            profile.effect = {
              mode: elements.effectMode.value,
              direction: metadata.default_direction,
              speed: metadata.default_speed,
            };
          } else {
            profile.effect = {mode: elements.effectMode.value};
          }
        });
      });
      elements.effectDirection?.addEventListener("change", () => {
        mutate((candidate) => {
          candidate.profiles[
            candidate.active_profile
          ].effect.direction = elements.effectDirection.value;
        });
      });
      elements.effectSpeed?.addEventListener("input", () => {
        const raw = elements.effectSpeed.value.trim();
        const speed = raw === "" ? null : Number(raw);
        mutate((candidate) => {
          candidate.profiles[candidate.active_profile].effect.speed = speed;
        });
        if (speed === null || !Number.isFinite(speed)) {
          state.inputErrors.set(
            "effectSpeed", "Use a finite display-effect speed."
          );
          state.serverValidation = "invalid";
          render();
        }
      });
      elements.inspector.addEventListener("input", handleInspectorInput);
      elements.useBackground.addEventListener("change", () => {
        const region = selectedRegion();
        if (!region) return;
        const backgroundKey = `${state.draft.active_profile}:${region.id}`;
        const remembered = elements.backgroundColorControl?.dataset.lastValidColor
          || state.regionBackgroundColors.get(backgroundKey) || "#000000";
        state.regionBackgroundColors.set(backgroundKey, remembered);
        updateSelectedRegion((region) => {
          region.background_color = elements.useBackground.checked
            ? remembered : null;
        });
      });
      elements.widget.addEventListener("change", () => {
        updateSelectedRegion((region) => {
          Object.assign(region, setRegionWidget(
            region,
            elements.widget.value,
            state.defaults,
          ));
        });
      });
      for (const helper of root.querySelectorAll("[data-geometry-helper]")) {
        helper.addEventListener("click", () => {
          applyGeometryHelper(helper.dataset.geometryHelper);
        });
      }
      elements.add.addEventListener("click", () => {
        mutate((candidate) => {
          const profile = candidate.profiles[candidate.active_profile];
          const added = addRegion(profile, state.defaults, state.metadata);
          candidate.profiles[candidate.active_profile] = added.profile;
          state.selectedRegionId = added.regionId;
        });
      });
      elements.duplicate.addEventListener("click", () => {
        mutate((candidate) => {
          const profile = candidate.profiles[candidate.active_profile];
          const duplicated = duplicateRegion(profile, state.selectedRegionId);
          candidate.profiles[candidate.active_profile] = duplicated.profile;
          state.selectedRegionId = duplicated.regionId;
        });
      });
      elements.remove.addEventListener("click", () => {
        const region = selectedRegion();
        if (!region) return;
        if (shouldConfirmRegionDelete(region)
            && typeof windowObject.confirm === "function"
            && !windowObject.confirm("Delete the selected configured region?")) {
          return;
        }
        const id = region.id;
        mutate((candidate) => {
          candidate.profiles[candidate.active_profile] = removeRegion(
            candidate.profiles[candidate.active_profile], id,
          );
        });
        state.selectedRegionId = null;
      });
      elements.earlier.addEventListener("click", () => {
        mutate((candidate) => {
          const name = candidate.active_profile;
          candidate.profiles[name] = reorderRegion(
            candidate.profiles[name], state.selectedRegionId, -1,
          );
        });
      });
      elements.later.addEventListener("click", () => {
        mutate((candidate) => {
          const name = candidate.active_profile;
          candidate.profiles[name] = reorderRegion(
            candidate.profiles[name], state.selectedRegionId, 1,
          );
        });
      });
      elements.toggle.addEventListener("click", () => {
        mutate((candidate) => {
          const name = candidate.active_profile;
          candidate.profiles[name] = toggleRegion(
            candidate.profiles[name],
            state.selectedRegionId,
          );
        });
      });
      for (const button of root.querySelectorAll("[data-profile-action]")) {
        button.addEventListener("click", () => {
          const action = button.dataset.profileAction;
          const source = state.draft.active_profile;
          if (action === "delete") {
            if (Object.keys(state.draft.profiles).length <= 1) return;
            if (typeof windowObject.confirm === "function"
                && !windowObject.confirm(`Delete profile ${source}?`)) return;
            const changed = profileAction(
              state.draft, action, source, "", state.metadata
            );
            if (changed) {
              state.selectedRegionId = selectionForProfile(
                state.selectedRegionId,
                changed.profiles[changed.active_profile],
              );
              mutate((candidate) => Object.assign(candidate, changed));
            }
            return;
          }
          const promptLabel = {
            create: "New profile name",
            duplicate: "Duplicate profile name",
            rename: "New profile name",
          }[action];
          const target = typeof windowObject.prompt === "function"
            ? windowObject.prompt(promptLabel, action === "rename" ? source : "")
            : null;
          if (!target) return;
          const changed = profileAction(
            state.draft, action, source, target, state.metadata
          );
          if (!changed) {
            setMessage("Use a unique profile name with letters, numbers, _ or -.",
              "error");
            return;
          }
          state.selectedRegionId = selectionForProfile(
            state.selectedRegionId,
            changed.profiles[changed.active_profile],
          );
          mutate((candidate) => Object.assign(candidate, changed));
        });
      }
      elements.presets?.addEventListener("click", () => {
        if (state.submitting) return;
        void loadPresets().catch(() => {
          setMessage("The preset library could not be loaded.", "error");
        });
      });
      elements.cancelPreset?.addEventListener("click", () => {
        elements.presetBrowser.hidden = true;
      });
      elements.previewPreset?.addEventListener("click", () => {
        const preset = selectedPreset();
        if (preset) renderPreview(preset.framebuffer_base64,
          `${preset.name} preset`);
      });
      elements.createPreset?.addEventListener("click", () => {
        const preset = selectedPreset();
        if (!preset || state.submitting) return;
        if (Object.keys(state.draft.profiles).length >= state.metadata.max_profiles
            || totalRegionCount(state.draft) + preset.region_count
              > state.metadata.max_regions_per_document) {
          setMessage("The preset would exceed a profile or region limit.", "error");
          return;
        }
        const target = typeof windowObject.prompt === "function"
          ? windowObject.prompt("New profile name", preset.id.replaceAll("-", "_"))
          : null;
        if (!target) return;
        const changed = createProfileFromPreset(state.draft, preset, target);
        if (!changed) {
          setMessage("Use a unique profile name with letters, numbers, _ or -.",
            "error");
          return;
        }
        state.selectedRegionId = null;
        mutate((candidate) => Object.assign(candidate, changed));
        elements.presetBrowser.hidden = true;
        setMessage("Preset added as a new local-draft profile.", "success");
      });
      elements.importProfile?.addEventListener("click", () => {
        if (!state.submitting) elements.profileFile.click();
      });
      elements.profileFile?.addEventListener("change", () => {
        const file = elements.profileFile.files?.[0];
        elements.profileFile.value = "";
        if (file) void importProfileFile(file);
      });
      elements.importConfig?.addEventListener("click", () => {
        if (!state.submitting) elements.configFile.click();
      });
      elements.configFile?.addEventListener("change", () => {
        const file = elements.configFile.files?.[0];
        elements.configFile.value = "";
        if (file) void importConfigurationFile(file);
      });
      elements.exportProfile?.addEventListener("click", () => {
        const name = state.draft.active_profile;
        void downloadExport("/export/profile",
          `nextuxlinq-profile-${name}.json`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
              profile_name: name,
              profile: state.draft.profiles[name],
            }),
          });
      });
      elements.exportConfig?.addEventListener("click", () => {
        void downloadExport("/export/configuration", "nextuxlinq-config-v2.json");
      });
      elements.createBackup?.addEventListener("click", async () => {
        if (state.submitting) return;
        const name = typeof windowObject.prompt === "function"
          ? windowObject.prompt("Backup name", "Manual backup") : null;
        if (!name) return;
        state.submitting = true;
        render();
        try {
          await request("/backups", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name}),
          });
          await loadBackups();
          setMessage("Saved configuration backed up.", "success");
        } catch (error) {
          setMessage(error instanceof EditorRequestError
            ? error.message : "The backup could not be created.", "error");
        } finally {
          state.submitting = false;
          render();
        }
      });
      elements.restoreBackup?.addEventListener("click", async () => {
        const backupId = elements.backupSelect.value;
        if (!backupId || state.submitting || !confirmDraftTransition(
          "Discard the current local draft and inspect this backup?"
        )) return;
        if (typeof windowObject.confirm === "function"
            && !windowObject.confirm("Restore this backup into local draft state?")) {
          return;
        }
        state.submitting = true;
        render();
        try {
          const result = await request(`/backups/${backupId}/restore`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: "{}",
          });
          replaceDraft(result.configuration);
          renderPreview(result.framebuffer_base64, "Backup preview");
          setMessage("Backup restored into the local draft.", "success");
        } catch (error) {
          setMessage(error instanceof EditorRequestError
            ? error.message : "The backup could not be restored.", "error");
        } finally {
          state.submitting = false;
          render();
        }
      });
      elements.deleteBackup?.addEventListener("click", async () => {
        const backupId = elements.backupSelect.value;
        if (!backupId || state.submitting) return;
        if (typeof windowObject.confirm === "function"
            && !windowObject.confirm("Delete this managed backup?")) return;
        state.submitting = true;
        render();
        try {
          await request(`/backups/${backupId}`, {
            method: "DELETE",
            headers: {"Content-Type": "application/json"},
            body: "{}",
          });
          await loadBackups();
          setMessage("Managed backup deleted.", "success");
        } catch (error) {
          setMessage(error instanceof EditorRequestError
            ? error.message : "The backup could not be deleted.", "error");
        } finally {
          state.submitting = false;
          render();
        }
      });
      elements.backupSelect?.addEventListener("focus", () => {
        if (!state.submitting) void loadBackups().catch(() => {
          setMessage("Managed backups could not be listed.", "error");
        });
      });
      elements.validate.addEventListener("click", () => void validateDraft());
      elements.apply.addEventListener("click", () => void applyDraft());
      elements.save.addEventListener("click", () => void saveApplied());
      elements.reload.addEventListener("click", () => {
        const selectedRegionId = state.selectedRegionId;
        if (state.pendingRemote) {
          state.applied = clone(state.pendingRemote.configuration);
          state.activeRevision = state.pendingRemote.active_revision;
          state.savedRevision = state.pendingRemote.saved_revision;
          state.appliedDirty = Boolean(state.pendingRemote.dirty);
          state.migrationPending = Boolean(
            state.pendingRemote.migration_pending,
          );
        }
        state.draft = clone(state.applied);
        state.selectedRegionId = selectionForProfile(
          selectedRegionId,
          activeProfile(),
        );
        state.serverErrors = [];
        state.resolvedFonts = {};
        state.fontOptions = {};
        state.fontFit = {};
        state.inputErrors.clear();
        state.pendingRemote = null;
        state.pendingFullImportBackup = false;
        state.conflict = false;
        updateDerivedState();
        state.serverValidation = "idle";
        render();
        void validateDraft();
      });
      if (windowObject.addEventListener) {
        windowObject.addEventListener("pointermove", movePointerOperation);
        windowObject.addEventListener("pointerup", endPointerOperation);
        windowObject.addEventListener(
          "pointercancel",
          cancelPointerOperation,
        );
        windowObject.addEventListener("keydown", (event) => {
          if (event.key === "Escape") cancelPointerOperation();
          if (event.key !== "Delete" || !state.selectedRegionId) return;
          if (isTextEntryTarget(event.target)) return;
          elements.remove.click();
        });
        windowObject.addEventListener("beforeunload", (event) => {
          stopTimers();
          if (!hasMeaningfulDraft(state)) return undefined;
          event.preventDefault?.();
          event.returnValue = "";
          return "";
        });
      }
      documentObject.addEventListener?.("visibilitychange", () => {
        if (state.serverValidation === "valid") scheduleAnimatedPreview();
      });
    }

    async function initialize() {
      try {
        const [metadata, configuration] = await Promise.all([
          request("/metadata"),
          request("/config"),
        ]);
        state.metadata = metadata;
        state.defaults = widgetDefaultMap(metadata);
        state.applied = clone(configuration.configuration);
        state.draft = clone(configuration.configuration);
        state.activeRevision = configuration.active_revision;
        state.savedRevision = configuration.saved_revision;
        state.appliedDirty = Boolean(configuration.dirty);
        state.migrationPending = Boolean(configuration.migration_pending);
        updateDerivedState();
        bind();
        render();
        await validateDraft();
        scheduleReconciliation();
      } catch (error) {
        setMessage("The free-layout editor could not be loaded.", "error");
      }
    }

    return {
      state,
      elements,
      initialize,
      request,
      render,
      validateDraft,
      applyDraft,
      saveApplied,
      importProfileFile,
      importConfigurationFile,
      loadPresets,
      loadBackups,
      reload,
      mutate,
      cancelPointerOperation,
      movePointerOperation,
    };
  }

  function bootstrap(dependencies) {
    const editor = createEditor(dependencies);
    void editor.initialize();
    return editor;
  }

  return Object.freeze({
    API_PREFIX,
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    WIDGET_TYPES,
    normalizeColorDraft,
    colorControlState,
    synchronizeColorControl,
    logicalToViewerX,
    viewerToLogicalX,
    clampInteger,
    clampGeometry,
    rectanglesOverlap,
    validationPriority,
    validationExplanation,
    fontGuidanceLines,
    safeResizeGeometry,
    fontResizeSuggestion,
    regionValidationErrors,
    fontOptionReason,
    resolvedFontExplanation,
    temperatureFormatExample,
    temperatureUnitVisible,
    effectSpeedDescription,
    effectDefault,
    foregroundRainbowActive,
    metricDefinitionGroups,
    validateLocalCandidate,
    validateWidgetSettings,
    nextStableId,
    widgetDefaultMap,
    createEmptyProfile,
    addRegion,
    duplicateRegion,
    reorderRegion,
    removeRegion,
    toggleRegion,
    shouldConfirmRegionDelete,
    isTextEntryTarget,
    selectionForProfile,
    selectionForCanvasPointer,
    toggleRegionSelection,
    setRegionWidget,
    geometryFromViewer,
    moveGeometryInViewer,
    resizeGeometryInViewer,
    profileAction,
    hasMeaningfulDraft,
    addImportedProfile,
    createProfileFromPreset,
    arrayBufferToBase64,
    deriveEditorState,
    createEditor,
    bootstrap,
  });
});
