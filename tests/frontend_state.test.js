"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const frontend = require("../src/nextuxlinq/static/app.js");
const freeLayout = require(
  "../src/nextuxlinq/static/free_layout_editor.js"
);

const widgetMetadata = {
  canvas_width: 90,
  canvas_height: 10,
  max_profiles: 32,
  max_regions_per_profile: 32,
  max_regions_per_document: 128,
  widget_definitions: [
    {
      type: "blank", label: "Nothing / Gap", default_settings: {},
      minimum_width: 1, minimum_height: 1,
    },
    {
      type: "clock", label: "Clock",
      default_settings: {
        format: "24h", show_seconds: false, blink_separator: false,
        time_zone: "local", color: "#FFFFFF",
        horizontal_alignment: "center", vertical_alignment: "center",
        font: "auto",
      },
      minimum_width: 13, minimum_height: 5,
    },
    {
      type: "static_text", label: "Static Text",
      default_settings: {
        content: "TEXT", color: "#FFFFFF",
        horizontal_alignment: "center", vertical_alignment: "center",
        font: "auto", uppercase: true, overflow: "error",
      },
      minimum_width: 1, minimum_height: 5,
    },
    {
      type: "scrolling_text", label: "Scrolling Text",
      default_settings: {
        content: "TEXT", direction: "left", speed: 5, color: "#FFFFFF",
        initial_alignment: "end", vertical_alignment: "center",
        font: "auto", uppercase: true, trailing_gap: 6,
      },
      minimum_width: 1, minimum_height: 5,
    },
    {
      type: "date", label: "Date",
      default_settings: {
        format: "day_month_full_year", time_zone: "local",
        color: "#FFFFFF", horizontal_alignment: "center",
        vertical_alignment: "center", font: "auto", overflow: "error",
      },
      minimum_width: 19, minimum_height: 5,
    },
    {
      type: "system_metric", label: "System Metric",
      default_settings: {
        metric: "cpu_usage", display_mode: "label_and_value", label: "CPU",
        color: "#FFFFFF", font: "auto", horizontal_alignment: "center",
        vertical_alignment: "center", overflow: "error", precision: 0,
        unavailable_text: "--",
      },
      minimum_width: 1, minimum_height: 5,
    },
    {
      type: "metric_bar", label: "Metric Bar",
      default_settings: {
        metric: "cpu_usage", minimum: 0, maximum: 100,
        orientation: "horizontal", direction: "start_to_end",
        fill_color: "#00D4C7", empty_color: "#000000",
        border_enabled: false, border_color: "#FFFFFF",
      },
      minimum_width: 1, minimum_height: 1,
    },
  ],
  metric_definitions: [
    {id: "cpu_usage", label: "CPU", value_type: "percentage",
      unit: "%", default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
    {id: "memory_usage", label: "RAM", value_type: "percentage",
      unit: "%", default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
    {id: "cpu_temperature", label: "CPU TEMP", value_type: "temperature",
      unit: "C", default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
    {id: "uptime", label: "UPTIME", value_type: "duration", unit: "",
      default_precision: 0, supports_bar: false,
      default_minimum: null, default_maximum: null},
    {id: "load_average_1m", label: "LOAD", value_type: "number", unit: "",
      default_precision: 2, supports_bar: false,
      default_minimum: null, default_maximum: null},
    {id: "storage_usage", label: "DISK", value_type: "percentage", unit: "%",
      default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
    {id: "network_receive_rate", label: "NET RX", value_type: "rate", unit: "B/S",
      default_precision: 1, supports_bar: false,
      default_minimum: null, default_maximum: null},
    {id: "network_transmit_rate", label: "NET TX", value_type: "rate", unit: "B/S",
      default_precision: 1, supports_bar: false,
      default_minimum: null, default_maximum: null},
    {id: "gpu_usage", label: "GPU", value_type: "percentage", unit: "%",
      default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
    {id: "gpu_temperature", label: "GPU TEMP", value_type: "temperature", unit: "C",
      default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
    {id: "gpu_memory_usage", label: "VRAM", value_type: "percentage", unit: "%",
      default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
    {id: "storage_temperature", label: "DISK TEMP", value_type: "temperature",
      unit: "C", default_precision: 0, supports_bar: true,
      default_minimum: 0, default_maximum: 100},
  ],
  bar_metric_ids: [
    "cpu_usage", "memory_usage", "cpu_temperature", "storage_usage",
    "gpu_usage", "gpu_temperature", "gpu_memory_usage", "storage_temperature",
  ],
};
for (const metric of widgetMetadata.metric_definitions) {
  metric.availability = metric.id === "cpu_usage"
    ? "available" : metric.id === "network_receive_rate"
      ? "awaiting_initial_delta" : "unavailable";
  metric.availability_reason = metric.availability === "available"
    ? null : metric.availability === "awaiting_initial_delta"
      ? "awaiting_initial_sample" : "source_not_present";
}

function freeProfile(regions = []) {
  return {
    brightness: null,
    canvas: {width: 90, height: 10, background_color: "#000000"},
    regions,
  };
}

function freeRegion(changes = {}) {
  return {
    id: "region-1", name: "Region 1",
    x: 0, y: 0, width: 10, height: 5,
    enabled: true, background_color: null,
    widget: {type: "blank", settings: {}},
    ...changes,
  };
}

function freeConfiguration(profileValue = freeProfile()) {
  return {
    schema_version: 2,
    active_profile: "default",
    profiles: {default: profileValue},
  };
}

function freeEditorFixture({
  migrationPending = false,
  saveStatus = 200,
  profiles = null,
  validationFrames = null,
} = {}) {
  const ids = [
    "#fl-profile",
    "#fl-canvas-background",
    "#fl-brightness",
    "#fl-region-layer",
    "#fl-editor-canvas",
    "#fl-region-validation-summary",
    "#fl-region-inspector",
    "#fl-no-selection",
    "#fl-selected-region",
    "#fl-region-validation-details",
    "#fl-region-name",
    "#fl-region-enabled",
    "#fl-use-region-background",
    "#fl-region-background",
    "#fl-widget-type",
    "#fl-x",
    "#fl-y",
    "#fl-width",
    "#fl-height",
    "#fl-widget-settings",
    "#fl-editor-state",
    "#fl-editor-message",
    "#fl-validation-errors",
    "#draft-preview",
    "#fl-preview-state",
    "#fl-apply",
    "#fl-save",
    "#fl-validate",
    "#fl-reload",
    "#fl-add-region",
    "#fl-duplicate-region",
    "#fl-delete-region",
    "#fl-region-earlier",
    "#fl-region-later",
    "#fl-toggle-region",
  ];
  const elements = new Map(ids.map((id) => [id, new FakeElement()]));
  for (const [id, field] of [
    ["#fl-region-name", "name"],
    ["#fl-region-enabled", "enabled"],
    ["#fl-x", "x"],
    ["#fl-y", "y"],
    ["#fl-width", "width"],
    ["#fl-height", "height"],
  ]) {
    elements.get(id).dataset.regionField = field;
  }
  elements.get("#fl-region-enabled").type = "checkbox";
  elements.get("#fl-use-region-background").type = "checkbox";
  function colorControl(target, picker) {
    const control = new FakeElement();
    const hex = new FakeElement("input");
    picker.type = "color";
    hex.type = "text";
    hex.dataset.colorHex = "";
    control.dataset.colorTarget = target;
    control.querySelector = (selector) => (
      selector === 'input[type="color"]' ? picker
        : selector === "[data-color-hex]" ? hex : null
    );
    control.querySelectorAll = (selector) => (
      selector === "input" ? [picker, hex] : []
    );
    return {control, picker, hex};
  }
  const canvasColor = colorControl(
    "canvas_background",
    elements.get("#fl-canvas-background"),
  );
  const regionColor = colorControl(
    "region_background",
    elements.get("#fl-region-background"),
  );
  const root = new FakeElement();
  const metricAvailability = new FakeElement();
  root.querySelector = (selector) => ({
    '[data-color-target="canvas_background"]': canvasColor.control,
    '[data-color-target="region_background"]': regionColor.control,
  })[selector] || elements.get(selector) || null;
  root.querySelectorAll = (selector) => (
    selector === "[data-color-control]"
      ? [canvasColor.control, regionColor.control] : []
  );
  const documentListeners = {};
  const document = {
    hidden: false,
    querySelector(selector) {
      if (selector === "#free-layout-root") return root;
      if (selector === "#metric-availability") return metricAvailability;
      return null;
    },
    querySelectorAll() {
      return [];
    },
    createElement(tagName) {
      return new FakeElement(tagName);
    },
    addEventListener(name, callback) {
      documentListeners[name] = callback;
    },
  };
  const windowListeners = {};
  const window = {
    addEventListener(name, callback) {
      windowListeners[name] = callback;
    },
    confirm() {
      return true;
    },
  };
  const documentValue = {
    schema_version: 2,
    active_profile: "default",
    profiles: profiles || {
      default: freeProfile([freeRegion()]),
    },
  };
  let activeRevision = 1;
  let savedRevision = 1;
  let pending = migrationPending;
  const calls = [];
  const frame = Buffer.alloc(90 * 10 * 3).toString("base64");
  let validationFrameIndex = 0;
  const fetch = async (url, options = {}) => {
    calls.push({url, options});
    if (url.endsWith("/metadata")) return response(widgetMetadata);
    if (url.endsWith("/validate")) {
      const validationFrame = validationFrames
        ? validationFrames[Math.min(
          validationFrameIndex,
          validationFrames.length - 1,
        )] : frame;
      validationFrameIndex += 1;
      return response({
        valid: true,
        update_policy: "static",
        animated_content: false,
        framebuffer_base64: validationFrame,
      });
    }
    if (url.endsWith("/config") && options.method === "PUT") {
      if (saveStatus !== 200) {
        return response({
          code: "save_failed",
          message: "The configuration could not be saved.",
          field_errors: null,
        }, {status: saveStatus});
      }
      savedRevision += 1;
      pending = false;
      return response({
        configuration: documentValue,
        active_revision: activeRevision,
        saved_revision: savedRevision,
        dirty: false,
        migration_pending: false,
        warning: null,
      });
    }
    if (url.endsWith("/config")) {
      return response({
        configuration: documentValue,
        built_in_default: documentValue,
        active_revision: activeRevision,
        saved_revision: savedRevision,
        dirty: false,
        migration_pending: pending,
        legacy_editor_supported: true,
      });
    }
    throw new Error(`Unexpected editor request: ${url}`);
  };
  let timerId = 0;
  const editor = freeLayout.createEditor({
    document,
    window,
    fetch,
    setTimeout() {
      timerId += 1;
      return timerId;
    },
    clearTimeout() {},
    decodeBase64(value) {
      return Buffer.from(value, "base64").toString("binary");
    },
  });
  return {
    editor,
    elements,
    canvasColor,
    regionColor,
    metricAvailability,
    windowListeners,
    documentListeners,
    calls,
  };
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.value = "";
    this._textContent = "";
    this.textContentWrites = 0;
    Object.defineProperty(this, "textContent", {
      get: () => this._textContent,
      set: (value) => {
        this._textContent = String(value);
        this.textContentWrites += 1;
      },
    });
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.children = [];
    this.listeners = {};
    this.attributes = new Map();
    this.dataset = {};
    this.focused = false;
    this.replaceCalls = 0;
    this.styleValues = {};
    this.style = {
      setProperty: (name, value) => {
        this.styleValues[name] = value;
      },
    };
    this.context = {
      image: null,
      createImageData(width, height) {
        return {data: new Uint8ClampedArray(width * height * 4)};
      },
      putImageData: (image) => {
        this.context.image = image;
      },
    };
  }

  addEventListener(name, callback) {
    if (!this.listeners[name]) this.listeners[name] = [];
    this.listeners[name].push(callback);
  }

  dispatch(name, event = {}) {
    const dispatched = {
      target: this,
      currentTarget: this,
      preventDefault() {},
      stopPropagation() {},
      ...event,
    };
    for (const callback of this.listeners[name] || []) callback(dispatched);
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
    this.replaceCalls += 1;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  toggleAttribute(name, force) {
    if (force) this.setAttribute(name, "");
    else this.removeAttribute(name);
  }

  querySelectorAll() {
    return [];
  }

  getBoundingClientRect() {
    return {width: 900, height: 100};
  }

  click() {
    this.dispatch("click");
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  focus() {
    this.focused = true;
  }

  getContext() {
    return this.context;
  }
}

class FakeForm extends FakeElement {
  constructor() {
    super("form");
    this.valid = true;
    this.reportCalls = 0;
    this.invalidElement = null;
  }

  checkValidity() {
    return this.valid;
  }

  reportValidity() {
    this.reportCalls += 1;
    return this.valid;
  }

  querySelector(selector) {
    return selector === ":invalid" ? this.invalidElement : null;
  }
}

function fakeDocument() {
  const selectors = [
    "#message",
    "#background-message",
    "#connection-message",
    "#reconciliation-panel",
    "#reconciliation-message",
    "#configuration-state",
    "#configuration-state-summary",
    "#configuration-state-detail",
    "#region-labels",
    "#preview",
    "#daemon-state",
    "#device-status",
    "#model-name",
    "#vid-pid",
    "#device-path",
    "#active-profile",
    "#configuration-revision",
    "#status-saved-revision",
    "#rendering-state",
    "#frame-rate",
    "#last-upload",
    "#last-error",
    "#multiple-device-warning",
    "#preview-revision",
    "#scrolling-text",
    "#scrolling-text-error",
    "#text-color",
    "#text-color-error",
    "#clock-color",
    "#clock-color-error",
    "#scroll-speed",
    "#scroll-speed-error",
    "#brightness",
    "#brightness-error",
    "[data-action='apply']",
    "[data-action='save']",
    "[data-action='defaults']",
    "[data-action='reload-applied']",
    "[data-action='keep-local-draft']",
    "[data-action='reload-reconciliation']",
  ];
  const elements = new Map(
    selectors.map((selector) => [selector, new FakeElement()])
  );
  elements.set("#profile-form", new FakeForm());
  return {
    elements,
    querySelector(selector) {
      const element = elements.get(selector);
      if (!element) throw new Error(`Missing fake selector: ${selector}`);
      return element;
    },
    createElement(tagName) {
      return new FakeElement(tagName);
    },
  };
}

function profile(changes = {}) {
  return {
    scrolling_text: "CACHYOS",
    text_color: "#00D4C7",
    clock_color: "#FFFFFF",
    scroll_speed: 5,
    brightness: null,
    layout: {
      front_clock_width: 24,
      gap_width: 4,
      scroll_text_width: 31,
      rear_clock_width: 31,
    },
    ...changes,
  };
}

function configuration(changes = {}) {
  const selectedProfile = changes.profile || profile();
  return {
    schema_version: 1,
    active_profile: changes.active_profile || "default",
    profiles: {
      default: selectedProfile,
      ...(changes.profiles || {}),
    },
  };
}

function configPayload(changes = {}) {
  const document = changes.configuration || configuration();
  return {
    configuration: document,
    built_in_default: configuration(),
    active_revision: changes.active_revision ?? 1,
    saved_revision: changes.saved_revision === undefined
      ? 1 : changes.saved_revision,
    dirty: changes.dirty ?? false,
  };
}

function statusPayload(changes = {}) {
  return {
    daemon_state: "running",
    device_available: false,
    model_name: null,
    device_path: null,
    usb_vid: "2e3c",
    usb_pid: "6500",
    multiple_device_warning: null,
    active_profile: "default",
    active_revision: 1,
    saved_revision: 1,
    dirty: false,
    rendering_state: "rendering",
    frame_rate: 5,
    last_frame_generation_time: 1,
    last_successful_upload_time: null,
    last_display_error: null,
    configuration_warning: null,
    ...changes,
  };
}

function previewPayload(changes = {}) {
  return {
    width: 90,
    height: 10,
    pixel_format: "rgb888",
    orientation: "physical-front-to-rear",
    framebuffer_base64: "frame",
    active_revision: 1,
    generation_timestamp: 100,
    regions: frontend.FALLBACK_REGIONS,
    ...changes,
  };
}

function response(payload, {status = 200, raw = null} = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() {
      return raw === null ? JSON.stringify(payload) : raw;
    },
  };
}

function fetchQueue(items) {
  const calls = [];
  const queue = [...items];
  const fetch = async (url, options = {}) => {
    calls.push({url, options});
    if (!queue.length) throw new Error(`Unexpected fetch: ${url}`);
    const item = queue.shift();
    if (item instanceof Error) throw item;
    return item;
  };
  fetch.calls = calls;
  fetch.remaining = () => queue.length;
  fetch.push = (...newItems) => queue.push(...newItems);
  return fetch;
}

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return {promise, resolve};
}

function controllerFixture(responses = [], overrides = {}) {
  const document = fakeDocument();
  const fetch = fetchQueue(responses);
  const timers = [];
  const controller = frontend.createController({
    document,
    fetch,
    setTimeout(callback, delay) {
      timers.push({callback, delay});
      return timers.length;
    },
    clearTimeout() {},
    decodeBase64: overrides.decodeBase64 || (() => "\0".repeat(2700)),
    window: {addEventListener() {}},
  });
  controller.state.connection.reachable = true;
  controller.acceptRemoteSnapshot({
    status: statusPayload(),
    config: configPayload(),
  });
  return {controller, document, fetch, timers};
}

test("draft detection covers every editable field", () => {
  const changes = {
    scrolling_text: "lowercase",
    text_color: "#112233",
    clock_color: "#445566",
    scroll_speed: "0.6",
    brightness: "70",
  };
  for (const [field, value] of Object.entries(changes)) {
    const {controller} = controllerFixture();
    controller.controls[field].value = value;
    controller.updateDraftFromForm();
    assert.equal(controller.state.draft.dirty, true, field);
  }
});

test("color case and numeric representation do not create false drafts", () => {
  assert.equal(
    frontend.editableValuesEqual(
      profile({text_color: "#AABBCC", scroll_speed: 5}),
      profile({text_color: "#aabbcc", scroll_speed: "5.0"}),
    ),
    true,
  );
  assert.equal(
    frontend.editableValuesEqual(
      profile({layout: {front_clock_width: 1}}),
      profile({layout: {front_clock_width: 89}}),
    ),
    true,
  );
});

test("configuration presentation uses API dirty and supports combined state", () => {
  const state = frontend.createInitialState();
  state.runtime.configuration = configuration();
  state.runtime.activeRevision = 7;
  state.runtime.savedRevision = 7;
  state.runtime.dirty = true;
  let presentation = frontend.deriveConfigurationState(state);
  assert.equal(presentation.primary, "Applied changes not saved");
  state.draft.dirty = true;
  presentation = frontend.deriveConfigurationState(state);
  assert.equal(
    presentation.primary,
    "Draft changes not applied; applied settings are also not saved.",
  );
  state.runtime.dirty = false;
  assert.equal(
    frontend.deriveConfigurationState(state).primary,
    "Draft changes not applied",
  );
  state.draft.dirty = false;
  assert.equal(
    frontend.deriveConfigurationState(state).primary,
    "Saved",
  );
});

test("arbitrary free-layout profiles make the legacy editor safely read-only", () => {
  const {controller} = controllerFixture();
  const canonical = {
    schema_version: 2,
    active_profile: "free",
    profiles: {
      free: {
        brightness: null,
        canvas: {width: 90, height: 10, background_color: "#000000"},
        regions: [],
      },
    },
  };

  controller.acceptRemoteSnapshot({
    status: statusPayload({active_profile: "free"}),
    config: {
      ...configPayload(),
      configuration: null,
      canonical_configuration: canonical,
      legacy_editor_supported: false,
      legacy_editor_message: "Free layout is read-only.",
    },
  });

  assert.equal(controller.state.runtime.configuration, null);
  assert.equal(controller.state.runtime.activeProfileName, "free");
  assert.equal(controller.state.runtime.legacyEditorSupported, false);
  assert.equal(controller.elements.configurationSummary.textContent,
    "Free-layout profile is read-only");
  assert.equal(controller.elements.applyButton.disabled, true);
  assert.equal(controller.elements.saveButton.disabled, true);
  assert.equal(controller.elements.defaultsButton.disabled, true);
  for (const control of Object.values(controller.controls)) {
    assert.equal(control.disabled, true);
  }
});

test("reconnect preserves a draft until unsupported free layout is accepted", async () => {
  const canonical = {
    schema_version: 2,
    active_profile: "free",
    profiles: {
      free: {
        brightness: null,
        canvas: {width: 90, height: 10, background_color: "#000000"},
        regions: [],
      },
    },
  };
  const remoteConfig = {
    ...configPayload({active_revision: 2}),
    configuration: null,
    canonical_configuration: canonical,
    legacy_editor_supported: false,
  };
  const {controller} = controllerFixture([
    response(statusPayload({active_profile: "free", active_revision: 2})),
    response(remoteConfig),
  ]);
  controller.controls.scrolling_text.value = "LOCAL DRAFT";
  controller.updateDraftFromForm();

  await controller.beginReconciliation("reconnect");

  assert.equal(controller.state.connection.reconciliationState, "pending");
  assert.equal(controller.controls.scrolling_text.value, "LOCAL DRAFT");
  assert.equal(controller.elements.keepDraftButton.disabled, true);
  assert.equal(controller.elements.reloadReconciliationButton.disabled, false);

  controller.reloadPendingAppliedSettings();
  assert.equal(controller.state.runtime.configuration, null);
  assert.equal(controller.state.runtime.activeProfileName, "free");
  assert.equal(controller.state.runtime.legacyEditorSupported, false);
});

test("Apply success updates runtime while preserving saved revision", async () => {
  const normalized = profile({scrolling_text: "LOWERCASE"});
  const fetch = [
    response({
      profile_name: "default",
      profile: normalized,
      active_revision: 2,
      dirty: true,
    }),
  ];
  const {controller} = controllerFixture(fetch);
  controller.controls.scrolling_text.value = "lowercase";
  controller.updateDraftFromForm();
  const savedRevision = controller.state.runtime.savedRevision;

  assert.equal(await controller.runAction(controller.applyProfile), true);
  assert.equal(controller.state.runtime.activeRevision, 2);
  assert.equal(controller.state.runtime.savedRevision, savedRevision);
  assert.equal(controller.state.runtime.dirty, true);
  assert.equal(controller.controls.scrolling_text.value, "LOWERCASE");
  assert.equal(controller.state.draft.dirty, false);
  assert.equal(
    controller.elements.configurationSummary.textContent,
    "Applied changes not saved",
  );
});

test("Apply failure preserves draft values and revisions", async () => {
  const {controller} = controllerFixture([
    response(
      {
        code: "validation_failed",
        message: "The request contains invalid fields.",
        field_errors: [{field: "profile.scrolling_text", message: "Invalid text"}],
        request_id: "request-1",
      },
      {status: 422},
    ),
  ]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();

  assert.equal(await controller.runAction(controller.applyProfile), false);
  assert.equal(controller.controls.scrolling_text.value, "local draft");
  assert.equal(controller.state.runtime.activeRevision, 1);
  assert.equal(controller.state.runtime.savedRevision, 1);
  assert.equal(controller.state.draft.dirty, true);
  assert.equal(
    controller.controls.scrolling_text.getAttribute("aria-invalid"),
    "true",
  );
  assert.equal(
    controller.elements.message.textContent,
    "The request contains invalid fields.",
  );
  controller.controls.scrolling_text.dispatch("input");
  assert.equal(
    controller.controls.scrolling_text.getAttribute("aria-invalid"),
    null,
  );
  assert.equal(controller.state.ui.fieldErrors.scrolling_text, undefined);
});

test("Save success accepts complete normalized configuration", async () => {
  const saved = configuration({
    profile: profile({scrolling_text: "SAVED"}),
    profiles: {secondary: profile({scrolling_text: "SECONDARY"})},
  });
  const {controller, fetch} = controllerFixture([
    response({
      configuration: saved,
      active_revision: 2,
      saved_revision: 2,
      dirty: false,
      warning: null,
    }),
  ]);
  controller.acceptRemoteSnapshot({
    status: statusPayload(),
    config: configPayload({
      configuration: configuration({
        profiles: {secondary: profile({scrolling_text: "SECONDARY"})},
      }),
    }),
  });
  controller.controls.scrolling_text.value = "saved";
  controller.updateDraftFromForm();

  assert.equal(await controller.runAction(controller.saveConfiguration), true);
  assert.equal(controller.state.runtime.activeRevision, 2);
  assert.equal(controller.state.runtime.savedRevision, 2);
  assert.equal(controller.state.runtime.dirty, false);
  assert.equal(controller.state.draft.dirty, false);
  assert.equal(controller.controls.scrolling_text.value, "SAVED");
  assert.equal(controller.elements.configurationSummary.textContent, "Saved");
  const candidate = JSON.parse(fetch.calls[0].options.body).configuration;
  assert.deepEqual(candidate.profiles.default.layout, profile().layout);
  assert.equal(candidate.profiles.secondary.scrolling_text, "SECONDARY");
  assert.equal(candidate.active_profile, "default");
});

test("Save retains a durability warning from the API", async () => {
  const warning = "Saved, but final durability synchronization failed.";
  const {controller} = controllerFixture([
    response({
      configuration: configuration(),
      active_revision: 2,
      saved_revision: 2,
      dirty: false,
      warning,
    }),
  ]);
  assert.equal(await controller.runAction(controller.saveConfiguration), true);
  assert.equal(controller.elements.message.textContent, warning);
  assert.match(controller.elements.message.className, /warning/);
});

test("invalid Save reports validity, focuses invalid field, and performs no fetch", async () => {
  const {controller, document, fetch} = controllerFixture();
  const form = document.elements.get("#profile-form");
  form.valid = false;
  form.invalidElement = controller.controls.scroll_speed;

  assert.equal(await controller.runAction(controller.saveConfiguration), false);
  assert.equal(form.reportCalls, 1);
  assert.equal(controller.controls.scroll_speed.focused, true);
  assert.equal(fetch.calls.length, 0);
});

test("scroll speed 0.6 is accepted and serialized numerically", async () => {
  const saved = configuration({profile: profile({scroll_speed: 0.6})});
  const {controller, fetch} = controllerFixture([
    response({
      configuration: saved,
      active_revision: 2,
      saved_revision: 2,
      dirty: false,
      warning: null,
    }),
  ]);
  controller.controls.scroll_speed.value = "0.6";
  controller.updateDraftFromForm();

  assert.equal(await controller.runAction(controller.saveConfiguration), true);
  const body = JSON.parse(fetch.calls[0].options.body);
  assert.equal(body.configuration.profiles.default.scroll_speed, 0.6);
});

test("Reload Applied Settings discards only local edits and retains unsaved state", async () => {
  const remote = configPayload({
    configuration: configuration({profile: profile({scrolling_text: "RUNTIME"})}),
    active_revision: 4,
    saved_revision: 2,
    dirty: true,
  });
  const {controller, document} = controllerFixture([response(remote)]);
  controller.controls.scrolling_text.value = "local";
  controller.updateDraftFromForm();

  await controller.runAction(controller.reloadAppliedSettings);
  assert.equal(controller.controls.scrolling_text.value, "RUNTIME");
  assert.equal(controller.state.draft.dirty, false);
  assert.equal(controller.state.runtime.dirty, true);
  assert.match(
    document.elements.get("#message").textContent,
    /still differ from the saved configuration/,
  );
});

async function reconnectController({
  draft = null,
  remoteConfig = configPayload(),
  remoteStatus = statusPayload(),
} = {}) {
  const {controller, fetch} = controllerFixture([
    response(remoteStatus),
    response(remoteStatus),
    response(remoteConfig),
  ]);
  if (draft !== null) {
    controller.controls.scrolling_text.value = draft;
    controller.updateDraftFromForm();
  }
  controller.state.connection.reachable = false;
  await controller.request("/status");
  await controller.beginReconciliation();
  return {controller, fetch};
}

test("reconnect without a draft accepts remote state", async () => {
  const remote = configPayload({
    configuration: configuration({profile: profile({scrolling_text: "REMOTE"})}),
    active_revision: 2,
  });
  const {controller} = await reconnectController({remoteConfig: remote});
  assert.equal(controller.controls.scrolling_text.value, "REMOTE");
  assert.equal(controller.state.runtime.activeRevision, 2);
  assert.equal(controller.state.connection.reconciliationState, "idle");
});

test("reconnect with unchanged remote state preserves a draft without warning", async () => {
  const {controller} = await reconnectController({draft: "local draft"});
  assert.equal(controller.controls.scrolling_text.value, "local draft");
  assert.equal(controller.state.draft.dirty, true);
  assert.equal(controller.state.connection.reconciliationState, "idle");
  assert.equal(controller.elements.reconciliationPanel.hidden, true);
});

test("reconnect with changed revisions preserves draft and requires reconciliation", async () => {
  const remote = configPayload({active_revision: 8, saved_revision: 6, dirty: true});
  const remoteStatus = statusPayload({
    active_revision: 8,
    saved_revision: 6,
    dirty: true,
  });
  const {controller} = await reconnectController({
    draft: "local draft",
    remoteConfig: remote,
    remoteStatus,
  });
  assert.equal(controller.controls.scrolling_text.value, "local draft");
  assert.equal(controller.state.runtime.activeRevision, 1);
  assert.equal(controller.state.connection.reconciliationState, "pending");
  assert.equal(controller.elements.applyButton.disabled, true);
});

test("reconnect detects structural change with reused revisions", async () => {
  const remote = configPayload({
    configuration: configuration({profile: profile({scrolling_text: "OTHER"})}),
  });
  const {controller} = await reconnectController({
    draft: "local draft",
    remoteConfig: remote,
  });
  assert.equal(controller.state.connection.reconciliationState, "pending");
});

test("Keep local draft accepts remote baselines and rebases editable values", async () => {
  const remote = configPayload({
    configuration: configuration({profile: profile({scrolling_text: "REMOTE"})}),
    active_revision: 5,
  });
  const {controller} = await reconnectController({
    draft: "local draft",
    remoteConfig: remote,
    remoteStatus: statusPayload({active_revision: 5}),
  });
  controller.keepLocalDraft();
  assert.equal(controller.state.runtime.activeRevision, 5);
  assert.equal(controller.controls.scrolling_text.value, "local draft");
  assert.equal(controller.state.draft.baseline.scrolling_text, "REMOTE");
  assert.equal(controller.state.draft.dirty, true);
  assert.equal(controller.state.connection.reconciliationState, "idle");
});

test("Keep local draft reports and rebases an active-profile change", async () => {
  const remoteConfiguration = configuration({
    active_profile: "evening",
    profiles: {
      evening: profile({
        scrolling_text: "EVENING",
        layout: {
          front_clock_width: 20,
          gap_width: 5,
          scroll_text_width: 40,
          rear_clock_width: 25,
        },
      }),
    },
  });
  const {controller} = await reconnectController({
    draft: "local draft",
    remoteConfig: configPayload({
      configuration: remoteConfiguration,
      active_revision: 5,
    }),
    remoteStatus: statusPayload({
      active_profile: "evening",
      active_revision: 5,
    }),
  });
  controller.keepLocalDraft();
  assert.equal(controller.state.runtime.configuration.active_profile, "evening");
  assert.equal(
    controller.state.runtime.configuration.profiles.evening.layout.gap_width,
    5,
  );
  assert.match(controller.elements.message.textContent, /profile evening/);
});

test("Reload applied reconciliation discards the preserved draft", async () => {
  const remote = configPayload({
    configuration: configuration({profile: profile({scrolling_text: "REMOTE"})}),
    active_revision: 5,
  });
  const {controller} = await reconnectController({
    draft: "local draft",
    remoteConfig: remote,
    remoteStatus: statusPayload({active_revision: 5}),
  });
  controller.reloadPendingAppliedSettings();
  assert.equal(controller.controls.scrolling_text.value, "REMOTE");
  assert.equal(controller.state.draft.dirty, false);
  assert.equal(controller.state.connection.reconciliationState, "idle");
});

test("polling while reconciliation is pending does not repeat the warning", async () => {
  const remote = configPayload({active_revision: 5});
  const {controller, fetch} = await reconnectController({
    draft: "local draft",
    remoteConfig: remote,
    remoteStatus: statusPayload({active_revision: 5}),
  });
  const warning = controller.elements.reconciliationMessage.textContent;
  fetch.calls.length = 0;
  fetch.push(response(statusPayload({active_revision: 5})));
  await controller.refreshStatus();
  assert.equal(controller.state.connection.reconciliationState, "pending");
  assert.equal(warning, controller.elements.reconciliationMessage.textContent);
  assert.deepEqual(fetch.calls.map((call) => call.url), ["/api/v1/status"]);
});

test("concurrent reconciliation triggers share one remote fetch", async () => {
  const {controller, fetch} = controllerFixture([
    response(statusPayload()),
    response(configPayload()),
  ]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();

  await Promise.all([
    controller.beginReconciliation("status-change"),
    controller.beginReconciliation("reconnect"),
  ]);
  assert.deepEqual(
    fetch.calls.map((call) => call.url),
    ["/api/v1/status", "/api/v1/config"],
  );
});

test("only one sequential automatic reconciliation runs per connection epoch", async () => {
  const {controller, fetch} = controllerFixture([
    response(statusPayload()),
    response(configPayload()),
  ]);
  await controller.beginReconciliation("status-change");
  fetch.push(response(statusPayload()), response(configPayload()));

  await controller.beginReconciliation("status-change");
  assert.deepEqual(
    fetch.calls.map((call) => call.url),
    ["/api/v1/status", "/api/v1/config"],
  );
  assert.equal(fetch.remaining(), 2);
  assert.equal(
    controller.state.connection.lastAutomaticReconciliationEpoch,
    controller.state.connection.epoch,
  );
});

test("resolving pending state does not re-arm automatic reconciliation", async () => {
  const {controller, fetch} = controllerFixture([
    response(statusPayload({active_revision: 5})),
    response(configPayload({active_revision: 5})),
  ]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();
  await controller.beginReconciliation("status-change");
  controller.keepLocalDraft();
  fetch.push(
    response(statusPayload({active_revision: 6})),
    response(configPayload({active_revision: 6})),
  );

  await controller.beginReconciliation("status-change");
  assert.equal(controller.state.connection.reconciliationState, "idle");
  assert.equal(fetch.remaining(), 2);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    1,
  );
});

test("a new connection epoch permits a new automatic reconciliation", async () => {
  const {controller, fetch} = controllerFixture([
    response(statusPayload()),
    response(configPayload()),
  ]);
  await controller.beginReconciliation("status-change");
  const previousEpoch = controller.state.connection.epoch;
  fetch.push(
    new TypeError("network unavailable"),
    response(statusPayload()),
    response(statusPayload()),
    response(configPayload()),
  );

  await assert.rejects(
    controller.request("/status"),
    frontend.DaemonUnavailableError,
  );
  await controller.request("/status");
  await controller.beginReconciliation("reconnect");

  assert.equal(controller.state.connection.epoch, previousEpoch + 1);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    2,
  );
  assert.equal(fetch.remaining(), 0);
});

test("identical remote fingerprints do not repeat reconciliation warnings", async () => {
  const remote = configPayload({active_revision: 5});
  const {controller, fetch} = controllerFixture([
    response(statusPayload({active_revision: 5})),
    response(remote),
  ]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();
  await controller.beginReconciliation("status-change");
  const warningWrites = controller.elements.reconciliationMessage.textContentWrites;
  const warnedFingerprint = controller.state.connection.warnedRemoteFingerprint;
  fetch.push(
    response(statusPayload({active_revision: 5})),
    response(remote),
  );

  await controller.beginReconciliation("revision-conflict");
  assert.equal(
    controller.elements.reconciliationMessage.textContentWrites,
    warningWrites,
  );
  assert.equal(
    controller.state.connection.warnedRemoteFingerprint,
    warnedFingerprint,
  );
});

test("a different remote fingerprint updates pending reconciliation", async () => {
  const {controller, fetch} = controllerFixture([
    response(statusPayload({active_revision: 5})),
    response(configPayload({active_revision: 5})),
  ]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();
  await controller.beginReconciliation("status-change");
  const firstFingerprint = controller.state.connection.warnedRemoteFingerprint;
  const warningWrites = controller.elements.reconciliationMessage.textContentWrites;
  fetch.push(
    response(statusPayload({active_revision: 6, dirty: true})),
    response(configPayload({active_revision: 6, dirty: true})),
  );

  await controller.beginReconciliation("revision-conflict");
  assert.notEqual(
    controller.state.connection.warnedRemoteFingerprint,
    firstFingerprint,
  );
  assert.equal(
    controller.state.connection.pendingRemoteSnapshot.config.active_revision,
    6,
  );
  assert.ok(
    controller.elements.reconciliationMessage.textContentWrites > warningWrites,
  );
});

test("status polling during Apply defers reconciliation until success settles", async () => {
  const mutation = deferred();
  const {controller, fetch} = controllerFixture([mutation.promise]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();
  const action = controller.runAction(controller.applyProfile);
  fetch.push(response(statusPayload({active_revision: 2, dirty: true})));

  await controller.refreshStatus();
  assert.equal(controller.state.ui.submitting, true);
  assert.equal(controller.state.connection.deferredReconciliation, true);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    0,
  );

  mutation.resolve(response({
    profile_name: "default",
    profile: profile({scrolling_text: "LOCAL DRAFT"}),
    active_revision: 2,
    dirty: true,
  }));
  assert.equal(await action, true);
  assert.equal(controller.state.runtime.activeRevision, 2);
  assert.equal(controller.state.connection.deferredReconciliation, false);
  assert.equal(controller.state.connection.reconciliationState, "idle");
  assert.equal(controller.elements.reconciliationPanel.hidden, true);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    0,
  );
});

test("a newer external state is reconciled only after Apply settles", async () => {
  const mutation = deferred();
  const externalStatus = statusPayload({
    active_revision: 3,
    saved_revision: 1,
    dirty: true,
  });
  const {controller, fetch} = controllerFixture([mutation.promise]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();
  const action = controller.runAction(controller.applyProfile);
  fetch.push(response(externalStatus));

  await controller.refreshStatus();
  assert.equal(controller.state.connection.deferredReconciliation, true);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    0,
  );
  fetch.push(
    response(externalStatus),
    response(configPayload({
      active_revision: 3,
      saved_revision: 1,
      dirty: true,
    })),
  );
  mutation.resolve(response({
    profile_name: "default",
    profile: profile({scrolling_text: "LOCAL DRAFT"}),
    active_revision: 2,
    dirty: true,
  }));

  assert.equal(await action, true);
  assert.equal(controller.state.runtime.activeRevision, 3);
  assert.equal(controller.state.connection.reconciliationState, "idle");
  assert.equal(controller.elements.reconciliationPanel.hidden, true);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    1,
  );
});

test("status polling during Save cannot overlap the configuration request", async () => {
  const mutation = deferred();
  const {controller, fetch} = controllerFixture([mutation.promise]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();
  const action = controller.runAction(controller.saveConfiguration);
  fetch.push(response(statusPayload({
    active_revision: 2,
    saved_revision: 2,
  })));

  await controller.refreshStatus();
  assert.equal(controller.state.ui.submitting, true);
  assert.equal(controller.state.connection.deferredReconciliation, true);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    1,
  );

  mutation.resolve(response({
    configuration: configuration({
      profile: profile({scrolling_text: "LOCAL DRAFT"}),
    }),
    active_revision: 2,
    saved_revision: 2,
    dirty: false,
    warning: null,
  }));
  assert.equal(await action, true);
  assert.equal(controller.state.runtime.activeRevision, 2);
  assert.equal(controller.state.runtime.savedRevision, 2);
  assert.equal(controller.state.connection.reconciliationState, "idle");
  assert.equal(controller.elements.reconciliationPanel.hidden, true);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    1,
  );
});

test("failed mutation preserves state then performs deferred reconciliation", async () => {
  const mutation = deferred();
  const remoteStatus = statusPayload({
    active_revision: 9,
    saved_revision: 7,
    dirty: true,
  });
  const remoteConfig = configPayload({
    active_revision: 9,
    saved_revision: 7,
    dirty: true,
  });
  const {controller, fetch} = controllerFixture([mutation.promise]);
  controller.controls.scrolling_text.value = "local draft";
  controller.updateDraftFromForm();
  const action = controller.runAction(controller.applyProfile);
  fetch.push(response(remoteStatus));

  await controller.refreshStatus();
  assert.equal(controller.state.connection.deferredReconciliation, true);
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    0,
  );
  fetch.push(response(remoteStatus), response(remoteConfig));
  mutation.resolve(response(
    {
      code: "validation_failed",
      message: "The request contains invalid fields.",
      field_errors: null,
      request_id: "request-deferred",
    },
    {status: 422},
  ));

  assert.equal(await action, false);
  assert.equal(controller.controls.scrolling_text.value, "local draft");
  assert.equal(controller.state.runtime.activeRevision, 1);
  assert.equal(controller.state.runtime.savedRevision, 1);
  assert.equal(controller.state.connection.reconciliationState, "pending");
  assert.equal(
    fetch.calls.filter((call) => call.url.endsWith("/config")).length,
    1,
  );
});

test("HTTP 409 enters reconciliation without retrying the mutation", async () => {
  const remote = configPayload({active_revision: 9});
  const {controller, fetch} = controllerFixture([
    response(
      {
        code: "revision_conflict",
        message: "The configuration changed.",
        field_errors: null,
        request_id: "request-2",
      },
      {status: 409},
    ),
    response(statusPayload({active_revision: 9})),
    response(remote),
  ]);
  controller.controls.scrolling_text.value = "local";
  controller.updateDraftFromForm();

  assert.equal(await controller.runAction(controller.applyProfile), false);
  assert.equal(controller.state.connection.reconciliationState, "pending");
  assert.equal(controller.controls.scrolling_text.value, "local");
  assert.equal(fetch.calls.filter((call) => call.url.endsWith("/apply")).length, 1);
});

test("viewer placement reverses regions without changing logical coordinates", () => {
  const original = frontend.FALLBACK_REGIONS.map((region) => ({...region}));
  const viewerRegions = frontend.placeRegionsForViewer(original, 90);

  assert.deepEqual(viewerRegions.map((region) => region.name), [
    "Rear clock",
    "Scrolling text",
    "Gap",
    "Front clock",
  ]);
  assert.deepEqual(viewerRegions.map((region) => region.viewer_start_pixel), [
    0,
    31,
    62,
    66,
  ]);
  assert.deepEqual(original, frontend.FALLBACK_REGIONS);
  assert.deepEqual(
    viewerRegions.map((region) => region.start_pixel),
    [59, 28, 24, 0],
  );
});

test("viewer region labels match transformed positions and render names safely", () => {
  const document = fakeDocument();
  const list = document.elements.get("#region-labels");
  const regions = [
    {name: "<Front>", start_pixel: 0, width: 24, type: "clock"},
    {name: "Gap", start_pixel: 24, width: 66, type: "gap"},
  ];
  const normalized = frontend.normalizeRegions(regions, 90);
  assert.equal(normalized.valid, true);
  const viewerRegions = frontend.placeRegionsForViewer(normalized.regions, 90);
  frontend.renderRegions(document, list, viewerRegions);
  assert.equal(list.children[0].children[0].textContent, "Gap");
  assert.equal(list.children[0].styleValues["--region-viewer-start"], "1");
  assert.equal(list.children[1].children[0].textContent, "<Front>");
  assert.equal(list.children[1].styleValues["--region-width"], "24");
  assert.equal(list.children[1].styleValues["--region-viewer-start"], "67");
  assert.equal(list.children[1].getAttribute("data-logical-start-pixel"), "0");
  assert.equal(list.children[1].getAttribute("data-viewer-start-pixel"), "66");
});

test("viewer framebuffer moves whole regions without mirroring their pixels", () => {
  const logicalRegions = [
    {name: "Front", start_pixel: 0, width: 3, type: "clock"},
    {name: "Rear", start_pixel: 3, width: 3, type: "clock"},
  ];
  const viewerRegions = frontend.placeRegionsForViewer(logicalRegions, 6);
  const image = {data: new Uint8ClampedArray(6 * 4)};
  const binary = String.fromCharCode(
    10, 0, 0,
    20, 0, 0,
    30, 0, 0,
    40, 0, 0,
    50, 0, 0,
    60, 0, 0,
  );

  frontend.copyFramebufferForViewer(binary, image, 6, 1, viewerRegions);

  assert.deepEqual(
    [0, 1, 2, 3, 4, 5].map((x) => image.data[x * 4]),
    [40, 50, 60, 10, 20, 30],
  );
});

test("viewer framebuffer replacement clears relocated pixels from older frames", () => {
  const firstRegions = frontend.placeRegionsForViewer([
    {name: "Clock", start_pixel: 0, width: 2, height: 1, type: "clock"},
  ], 6);
  const secondRegions = frontend.placeRegionsForViewer([
    {name: "Metric", start_pixel: 0, width: 1, height: 1, type: "system-metric"},
  ], 6);
  const image = {data: new Uint8ClampedArray(6 * 4).fill(127)};
  const purpleFrame = String.fromCharCode(
    128, 0, 255, 128, 0, 255,
    1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3,
  );
  const whiteFrame = String.fromCharCode(
    255, 255, 255,
    1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3,
  );

  frontend.copyFramebufferForViewer(purpleFrame, image, 6, 1, firstRegions);
  assert.deepEqual(
    [0, 1, 2, 3, 4, 5].map((x) => Array.from(image.data.slice(x * 4, x * 4 + 3))),
    [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [128, 0, 255], [128, 0, 255]],
  );

  frontend.copyFramebufferForViewer(whiteFrame, image, 6, 1, secondRegions);
  const fresh = {data: new Uint8ClampedArray(6 * 4)};
  frontend.copyFramebufferForViewer(whiteFrame, fresh, 6, 1, secondRegions);
  assert.deepEqual(image.data, fresh.data);
  assert.deepEqual(
    [0, 1, 2, 3, 4, 5].map((x) => Array.from(image.data.slice(x * 4, x * 4 + 3))),
    [[1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [1, 2, 3], [255, 255, 255]],
  );
  assert.equal(Array.from(image.data).includes(128), false);
});

test("preview refresh and reconnect preserve viewer perspective", async () => {
  const preview = previewPayload();
  const framebuffer = String.fromCharCode(
    ...Array.from(
      {length: 90},
      (_value, x) => [x, 0, 0],
    ).flat(),
  ).repeat(10);
  const {controller} = controllerFixture([
    response(preview),
    new Error("offline"),
    response(preview),
  ], {decodeBase64: () => framebuffer});

  await controller.refreshPreview();
  assert.deepEqual(
    controller.elements.regionLabels.children.map(
      (item) => item.children[0].textContent,
    ),
    ["Rear clock", "Scrolling text", "Gap", "Front clock"],
  );
  assert.deepEqual(
    [0, 30, 31, 61, 62, 65, 66, 89].map(
      (x) => controller.elements.context.image.data[x * 4],
    ),
    [59, 89, 28, 58, 24, 27, 0, 23],
  );
  await assert.rejects(controller.refreshPreview());
  controller.state.ui.submitting = true;
  await controller.refreshPreview();
  assert.deepEqual(
    controller.elements.regionLabels.children.map(
      (item) => item.children[0].textContent,
    ),
    ["Rear clock", "Scrolling text", "Gap", "Front clock"],
  );
  assert.deepEqual(
    [0, 30, 31, 61, 62, 65, 66, 89].map(
      (x) => controller.elements.context.image.data[x * 4],
    ),
    [59, 89, 28, 58, 24, 27, 0, 23],
  );
});

test("unchanged preview regions are not rebuilt", async () => {
  const preview = previewPayload();
  const {controller} = controllerFixture([
    response(preview),
    response(preview),
  ]);
  await controller.refreshPreview();
  const replacements = controller.elements.regionLabels.replaceCalls;
  await controller.refreshPreview();
  assert.equal(controller.elements.regionLabels.replaceCalls, replacements);
});

test("invalid preview region variants use the Matrix900 fallback", () => {
  const variants = [
    null,
    [
      {name: "A", start_pixel: 0, width: 50, type: "clock"},
      {name: "B", start_pixel: 40, width: 50, type: "clock"},
    ],
    [{name: "Outside", start_pixel: 0, width: 91, type: "clock"}],
    [{name: "", start_pixel: 0, width: 90, type: "clock"}],
  ];
  for (const regions of variants) {
    const result = frontend.normalizeRegions(regions, 90);
    assert.equal(result.valid, false);
    assert.deepEqual(result.regions, frontend.FALLBACK_REGIONS);
  }
});

test("empty and noncontiguous free-layout preview regions are valid", () => {
  assert.deepEqual(frontend.normalizeRegions([], 90, 10), {
    regions: [],
    valid: true,
  });
  const result = frontend.normalizeRegions([
    {
      name: "Only",
      start_pixel: 10,
      y: 5,
      width: 20,
      height: 5,
      type: "static-text",
    },
  ], 90, 10);
  assert.equal(result.valid, true);
  assert.equal(result.regions[0].start_pixel, 10);
  assert.equal(result.regions[0].y, 5);
});

test("duplicate normalized preview-region names use the fallback", () => {
  const variants = [
    [
      {name: "Clock", start_pixel: 0, width: 45, type: "clock"},
      {name: "Clock", start_pixel: 45, width: 45, type: "clock"},
    ],
    [
      {name: "Front", start_pixel: 0, width: 45, type: "clock"},
      {name: " front ", start_pixel: 45, width: 45, type: "clock"},
    ],
    [
      {name: "\u212b", start_pixel: 0, width: 45, type: "clock"},
      {name: "A\u030a", start_pixel: 45, width: 45, type: "clock"},
    ],
  ];
  for (const regions of variants) {
    const result = frontend.normalizeRegions(regions, 90);
    assert.equal(result.valid, false);
    assert.deepEqual(result.regions, frontend.FALLBACK_REGIONS);
  }
});

test("non-printing preview-region names use the fallback", () => {
  for (const name of [
    "Front\u0000clock",
    "Front\nclock",
    "Front\u200bclock",
    "Front\u2028clock",
    "\u0301",
    "\ufe0f",
  ]) {
    const result = frontend.normalizeRegions([
      {name, start_pixel: 0, width: 90, type: "clock"},
    ], 90);
    assert.equal(result.valid, false, JSON.stringify(name));
    assert.deepEqual(result.regions, frontend.FALLBACK_REGIONS);
  }
});

test("distinct printable Unicode preview-region names remain valid", () => {
  const regions = [
    {name: "Horloge avant", start_pixel: 0, width: 45, type: "clock"},
    {name: "Texto traseiro \u2605", start_pixel: 45, width: 45, type: "text"},
  ];
  const result = frontend.normalizeRegions(regions, 90);
  assert.equal(result.valid, true);
  assert.deepEqual(result.regions.map((region) => region.name), [
    "Horloge avant",
    "Texto traseiro \u2605",
  ]);
});

test("multiple-device warning appears and clears", () => {
  const {controller} = controllerFixture();
  controller.renderStatus(statusPayload({
    multiple_device_warning: "Multiple Matrix900 devices detected.",
  }));
  assert.equal(
    controller.elements.multipleDeviceWarning.textContent,
    "Multiple Matrix900 devices detected.",
  );
  controller.renderStatus(statusPayload({multiple_device_warning: null}));
  assert.equal(controller.elements.multipleDeviceWarning.textContent, "");
});

test("status exposes existing Matrix900 and configuration information", () => {
  const {controller} = controllerFixture();
  controller.renderStatus(statusPayload({
    device_available: true,
    model_name: "Phanteks Matrix900",
    device_path: "/dev/hidraw-test",
    frame_rate: 4,
    last_display_error: "Display unavailable.",
    configuration_warning: "Configuration migration warning.",
  }));
  assert.equal(controller.elements.modelName.textContent, "Phanteks Matrix900");
  assert.equal(controller.elements.vidPid.textContent, "2e3c:6500");
  assert.equal(
    controller.elements.deviceStatus.textContent,
    "Phanteks Matrix900 connected",
  );
  assert.equal(controller.elements.frameRate.textContent, "4 FPS");
  assert.match(controller.elements.lastError.textContent, /Display unavailable/);
  assert.match(
    controller.elements.lastError.textContent,
    /Configuration migration warning/,
  );
});

test("last upload uses daemon-relative preview timing", () => {
  assert.equal(frontend.formatLastUpload(null, 100), "Not yet");
  assert.equal(frontend.formatLastUpload(99, 100), "Just now");
  assert.equal(frontend.formatLastUpload(88, 100), "12 seconds ago");
  assert.equal(frontend.formatLastUpload(0, 180), "3 minutes ago");
  assert.equal(
    frontend.formatLastUpload(88, null),
    "Recorded; awaiting preview timing",
  );
});

test("successful and failed non-JSON responses are sanitized", async () => {
  const success = controllerFixture([response(null, {raw: "<private>", status: 200})]);
  await assert.rejects(
    success.controller.request("/status"),
    /The daemon returned an invalid response/,
  );
  const failure = controllerFixture([response(null, {raw: "<private>", status: 500})]);
  await assert.rejects(
    failure.controller.request("/status"),
    (error) => (
      error.message === "The daemon rejected the request (HTTP 500)."
      && !error.message.includes("private")
    ),
  );
});

test("structured HTTP errors preserve safe protocol metadata", async () => {
  const {controller} = controllerFixture([
    response(
      {
        code: "validation_failed",
        message: "The request contains invalid fields.",
        field_errors: [{field: "profile.brightness", message: "Invalid value"}],
        request_id: "request-safe",
      },
      {status: 422},
    ),
  ]);
  await assert.rejects(controller.request("/apply"), (error) => (
    error instanceof frontend.ApiRequestError
    && error.status === 422
    && error.code === "validation_failed"
    && error.requestId === "request-safe"
    && error.fieldErrors.length === 1
  ));
});

test("structured field errors map recognized suffixes and retain unknown paths", () => {
  const result = frontend.mapStructuredFieldErrors([
    {
      field: "configuration.profiles.default.scroll_speed",
      message: "Invalid speed",
    },
    {field: "profile.scrolling_text", message: "Invalid text"},
    {field: "configuration.schema_version", message: "Invalid schema"},
    {field: "untrusted.path.scroll_speed", message: "Untrusted path"},
  ]);
  assert.deepEqual(result.mapped.scroll_speed, ["Invalid speed"]);
  assert.deepEqual(result.mapped.scrolling_text, ["Invalid text"]);
  assert.deepEqual(result.unknown, ["Invalid schema", "Untrusted path"]);
  for (const field of frontend.EDITABLE_FIELDS) {
    const mapped = frontend.mapStructuredFieldErrors([
      {
        field: `configuration.profiles.default.${field}`,
        message: `Invalid ${field}`,
      },
    ]);
    assert.deepEqual(mapped.mapped[field], [`Invalid ${field}`]);
  }
});

test("background polling messages do not overwrite action errors", () => {
  const {controller} = controllerFixture();
  controller.showActionMessage("Apply failed.", "error");
  controller.showBackgroundMessage("Status could not be refreshed.");
  assert.equal(controller.elements.message.textContent, "Apply failed.");
  assert.equal(
    controller.elements.backgroundMessage.textContent,
    "Status could not be refreshed.",
  );
});

test("network failure uses the friendly warning and reconnects safely", async () => {
  const {controller, fetch} = controllerFixture([
    new TypeError("Failed to fetch"),
    response(statusPayload()),
    response(statusPayload()),
    response(configPayload()),
  ]);
  await assert.rejects(
    controller.request("/status"),
    frontend.DaemonUnavailableError,
  );
  assert.equal(
    controller.elements.connectionMessage.textContent,
    frontend.UNAVAILABLE_MESSAGE,
  );
  assert.equal(controller.elements.applyButton.disabled, true);
  assert.equal(controller.elements.saveButton.disabled, true);

  await controller.request("/status");
  await controller.beginReconciliation();
  assert.equal(controller.elements.connectionMessage.textContent, "");
  assert.equal(controller.elements.applyButton.disabled, false);
  assert.equal(controller.elements.saveButton.disabled, false);
  assert.equal(
    JSON.stringify(controller.state).includes("Failed to fetch"),
    false,
  );
  assert.equal(fetch.remaining(), 0);
});

test("free-layout coordinates round-trip in viewer perspective", () => {
  assert.equal(freeLayout.logicalToViewerX(0, 24), 66);
  assert.equal(freeLayout.logicalToViewerX(59, 31), 0);
  assert.equal(freeLayout.viewerToLogicalX(66, 24), 0);
  for (const geometry of [
    {x: 0, y: 0, width: 90, height: 10},
    {x: 89, y: 9, width: 1, height: 1},
    {x: 17, y: 3, width: 23, height: 4},
  ]) {
    const viewerX = freeLayout.logicalToViewerX(
      geometry.x, geometry.width
    );
    assert.equal(
      freeLayout.viewerToLogicalX(viewerX, geometry.width),
      geometry.x,
    );
    assert.deepEqual(
      freeLayout.geometryFromViewer(
        viewerX, geometry.y, geometry.width, geometry.height
      ),
      geometry,
    );
  }
});

test("free-layout drag and resize snap and clamp in viewer coordinates", () => {
  const original = {x: 0, y: 1, width: 24, height: 7};
  assert.deepEqual(
    freeLayout.moveGeometryInViewer(original, -10, 9),
    {x: 10, y: 3, width: 24, height: 7},
  );
  assert.deepEqual(
    freeLayout.moveGeometryInViewer(original, 100, -100),
    {x: 0, y: 0, width: 24, height: 7},
  );
  assert.deepEqual(
    freeLayout.resizeGeometryInViewer(original, "nw", -5, -2),
    {x: 0, y: 0, width: 29, height: 8},
  );
  assert.deepEqual(
    freeLayout.resizeGeometryInViewer(original, "se", -5, 4),
    {x: 5, y: 1, width: 19, height: 9},
  );
  assert.deepEqual(
    freeLayout.clampGeometry({x: -8.2, y: 20, width: 95, height: 0}),
    {x: 0, y: 9, width: 90, height: 1},
  );
});

test("free-layout region operations preserve order and stable identities", () => {
  const defaults = freeLayout.widgetDefaultMap(widgetMetadata);
  const first = freeLayout.addRegion(freeProfile(), defaults, widgetMetadata);
  assert.equal(first.regionId, "region-1");
  assert.equal(first.profile.regions.length, 1);
  assert.deepEqual(first.profile.regions[0].widget, {
    type: "blank", settings: {},
  });

  const second = freeLayout.addRegion(
    first.profile, defaults, widgetMetadata
  );
  assert.equal(second.regionId, "region-2");
  assert.equal(
    freeLayout.rectanglesOverlap(
      second.profile.regions[0], second.profile.regions[1]
    ),
    false,
  );
  const duplicate = freeLayout.duplicateRegion(
    second.profile, "region-1"
  );
  assert.equal(duplicate.regionId, "region-3");
  assert.equal(duplicate.profile.regions[2].widget.type, "blank");
  assert.notEqual(duplicate.profile.regions[2].id, "region-1");

  const earlier = freeLayout.reorderRegion(
    duplicate.profile, "region-3", -1
  );
  assert.deepEqual(
    earlier.regions.map((region) => region.id),
    ["region-1", "region-3", "region-2"],
  );
  const disabled = freeLayout.toggleRegion(earlier, "region-3");
  assert.equal(disabled.regions[1].enabled, false);
  const removed = freeLayout.removeRegion(disabled, "region-3");
  assert.deepEqual(
    removed.regions.map((region) => region.id),
    ["region-1", "region-2"],
  );
  assert.equal(
    freeLayout.shouldConfirmRegionDelete(freeRegion()),
    false,
  );
  assert.equal(
    freeLayout.shouldConfirmRegionDelete(freeRegion({
      widget: {
        type: "static_text",
        settings: widgetMetadata.widget_definitions[2].default_settings,
      },
    })),
    true,
  );
});

test("local validation marks overlaps, bounds, dimensions, and duplicate IDs", () => {
  const overlapping = freeConfiguration(freeProfile([
    freeRegion(),
    freeRegion({id: "region-2", x: 5}),
  ]));
  const overlap = freeLayout.validateLocalCandidate(
    overlapping, widgetMetadata
  );
  assert.equal(overlap.valid, false);
  assert.equal(overlap.invalidRegionIds.has("region-1"), true);
  assert.equal(overlap.invalidRegionIds.has("region-2"), true);
  assert.equal(
    overlap.errors.some((error) => error.category === "overlap"),
    true,
  );

  for (const change of [
    {x: 90},
    {width: 0},
    {x: 0, id: "region-1"},
  ]) {
    const regions = change.id
      ? [freeRegion(), freeRegion({...change, x: 20})]
      : [freeRegion(change)];
    assert.equal(
      freeLayout.validateLocalCandidate(
        freeConfiguration(freeProfile(regions)), widgetMetadata
      ).valid,
      false,
    );
  }
});

test("region validation explanations expose exact safe geometry guidance", () => {
  assert.equal(freeLayout.validationExplanation({
    category: "content_too_wide",
    actual_width: 34,
    actual_height: 10,
    minimum_width: 35,
    minimum_height: 5,
  }), "Too narrow · minimum width 35 px");
  assert.equal(freeLayout.validationExplanation({
    category: "content_too_tall",
    actual_width: 38,
    actual_height: 6,
    minimum_width: 39,
    minimum_height: 7,
  }), "Minimum size 39 × 7 px");
  assert.equal(freeLayout.validationExplanation({
    category: "content_too_tall",
    actual_width: 90,
    actual_height: 6,
    minimum_width: 39,
    minimum_height: 7,
  }), "Too short · minimum height 7 px");
  assert.equal(freeLayout.validationExplanation({
    category: "unsupported_glyph",
    message: "PRIVATE USER CONTENT",
  }), "Unsupported character");
  assert.equal(freeLayout.validationExplanation({
    category: "time_zone_unavailable",
    message: "PRIVATE/TIMEZONE",
  }), "Invalid time zone");
});

test("region validation priority and overlap explanations are deterministic", () => {
  const profile = freeProfile([
    freeRegion({name: "CPU Metric"}),
    freeRegion({id: "region-2", name: "Clock", x: 5}),
  ]);
  const local = freeLayout.validateLocalCandidate(
    freeConfiguration(profile), widgetMetadata,
  );
  const firstOverlap = local.errors.find(
    (error) => error.region_id === "region-1"
      && error.category === "overlap"
  );
  assert.equal(
    freeLayout.validationExplanation(firstOverlap, profile),
    "Overlaps Clock",
  );
  const ordered = freeLayout.regionValidationErrors([
    {category: "content_too_wide", region_id: "region-1"},
    {category: "overlap", region_id: "region-1"},
    {category: "out_of_bounds", region_id: "region-1"},
    {category: "content_too_tall", region_id: "region-1"},
  ], [], "region-1");
  assert.deepEqual(
    ordered.map((error) => error.category),
    ["out_of_bounds", "overlap", "content_too_tall", "content_too_wide"],
  );
});

test("inline region error selects the region and inspector shows every error", async () => {
  const settings = widgetMetadata.widget_definitions.find(
    (definition) => definition.type === "system_metric"
  ).default_settings;
  const fixture = freeEditorFixture({
    profiles: {default: freeProfile([freeRegion({
      width: 34,
      height: 10,
      widget: {type: "system_metric", settings: {...settings}},
    })])},
  });
  await fixture.editor.initialize();
  const lastValidPreview = fixture.editor.state.lastValidPreview;
  const previewImage = fixture.elements.get("#draft-preview").context.image;
  fixture.editor.state.serverValidation = "invalid";
  fixture.editor.state.serverErrors = [
    {
      category: "content_too_wide", field: "content",
      region_id: "region-1", actual_width: 34, actual_height: 10,
      minimum_width: 35, minimum_height: 5,
    },
    {
      category: "unsupported_glyph", field: "content",
      region_id: "region-1", message: "<script>private()</script>",
    },
  ];
  fixture.editor.render();

  const overlay = fixture.elements.get("#fl-region-layer").children[0];
  const badge = overlay.children.find(
    (child) => child.className === "region-validation-badge"
  );
  assert.equal(badge.textContent, "Error · Too narrow · minimum width 35 px");
  assert.equal(badge.textContent.includes("script"), false);
  badge.dispatch("click");
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");

  const details = fixture.elements.get("#fl-region-validation-details");
  assert.equal(details.hidden, false);
  assert.deepEqual(details.children.map((item) => item.textContent), [
    "Error · Too narrow · minimum width 35 px",
    "Also · Unsupported character",
  ]);
  assert.equal(fixture.elements.get("#fl-width").getAttribute("aria-invalid"), "true");
  assert.equal(fixture.elements.get("#fl-apply").disabled, true);
  assert.equal(fixture.elements.get("#fl-save").disabled, true);
  assert.equal(fixture.editor.state.lastValidPreview, lastValidPreview);
  assert.equal(
    fixture.elements.get("#draft-preview").context.image,
    previewImage,
  );

  fixture.editor.state.serverErrors = [];
  fixture.editor.mutate((candidate) => {
    candidate.profiles.default.regions[0].width = 35;
  });
  const repaired = fixture.elements.get("#fl-region-layer").children[0];
  assert.equal(repaired.children.some(
    (child) => child.className === "region-validation-badge"
  ), false);
  assert.equal(details.hidden, true);
});

test("small invalid regions use an accessible message below the canvas", async () => {
  const fixture = freeEditorFixture({
    profiles: {default: freeProfile([freeRegion({x: 85, width: 10})])},
  });
  await fixture.editor.initialize();
  const summary = fixture.elements.get("#fl-region-validation-summary");
  assert.equal(summary.children.length, 1);
  assert.equal(
    summary.children[0].textContent,
    "Error · Region 1: Outside canvas",
  );
  summary.children[0].dispatch("click");
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");
});

test("widget selection uses exactly seven canonical defaults", () => {
  assert.deepEqual(freeLayout.WIDGET_TYPES, [
    "blank", "clock", "static_text", "scrolling_text", "date",
    "system_metric", "metric_bar",
  ]);
  const defaults = freeLayout.widgetDefaultMap(widgetMetadata);
  const source = freeRegion({
    x: 11, y: 5, width: 20, height: 5,
    background_color: "#123456",
  });
  for (const type of freeLayout.WIDGET_TYPES) {
    const changed = freeLayout.setRegionWidget(source, type, defaults);
    assert.equal(changed.widget.type, type);
    assert.deepEqual(changed.widget.settings, defaults[type]);
    assert.deepEqual(
      {
        x: changed.x, y: changed.y,
        width: changed.width, height: changed.height,
        background_color: changed.background_color,
      },
      {
        x: 11, y: 5, width: 20, height: 5,
        background_color: "#123456",
      },
    );
  }
  assert.deepEqual(
    Object.keys(freeLayout.setRegionWidget(
      source, "blank", defaults
    ).widget.settings),
    [],
  );
});

test("widget settings validate exact models and German date formats", () => {
  const defaults = freeLayout.widgetDefaultMap(widgetMetadata);
  for (const type of freeLayout.WIDGET_TYPES) {
    assert.equal(
      freeLayout.validateWidgetSettings({
        type, settings: defaults[type],
      }, widgetMetadata),
      null,
    );
  }
  for (const format of [
    "day_month_full_year",
    "day_month_short_year",
    "day_month",
    "weekday_day_month",
  ]) {
    assert.equal(
      freeLayout.validateWidgetSettings({
        type: "date",
        settings: {...defaults.date, format},
      }, widgetMetadata),
      null,
    );
  }
  assert.equal(
    freeLayout.validateWidgetSettings({
      type: "clock",
      settings: {...defaults.clock, unknown: true},
    }, widgetMetadata),
    "settings",
  );
  assert.equal(
    freeLayout.validateWidgetSettings({
      type: "scrolling_text",
      settings: {...defaults.scrolling_text, trailing_gap: 1.5},
    }, widgetMetadata),
    "settings",
  );
  assert.equal(
    freeLayout.validateWidgetSettings({
      type: "static_text",
      settings: {...defaults.static_text, content: ""},
    }, widgetMetadata),
    "content",
  );
  assert.equal(
    freeLayout.validateWidgetSettings({
      type: "date",
      settings: {...defaults.date, format: "MM/DD/YYYY"},
    }, widgetMetadata),
    "settings",
  );
});

test("metric widget validation rejects unknown metrics and invalid bar ranges", () => {
  const defaults = freeLayout.widgetDefaultMap(widgetMetadata);
  assert.equal(freeLayout.validateWidgetSettings({
    type: "system_metric",
    settings: {...defaults.system_metric, metric: "fan_usage"},
  }, widgetMetadata), "metric");
  assert.equal(freeLayout.validateWidgetSettings({
    type: "metric_bar",
    settings: {...defaults.metric_bar, metric: "uptime"},
  }, widgetMetadata), "metric");
  assert.equal(freeLayout.validateWidgetSettings({
    type: "metric_bar",
    settings: {...defaults.metric_bar, minimum: 10, maximum: 10},
  }, widgetMetadata), "maximum");
});

test("profile actions keep canonical layouts and new profiles empty", () => {
  const original = freeConfiguration(freeProfile([freeRegion()]));
  const created = freeLayout.profileAction(
    original, "create", "default", "empty"
  );
  assert.equal(created.active_profile, "empty");
  assert.deepEqual(created.profiles.empty, freeLayout.createEmptyProfile());
  const duplicated = freeLayout.profileAction(
    original, "duplicate", "default", "copy"
  );
  assert.deepEqual(duplicated.profiles.copy, original.profiles.default);
  assert.notEqual(duplicated.profiles.copy, original.profiles.default);
  const renamed = freeLayout.profileAction(
    duplicated, "rename", "copy", "renamed"
  );
  assert.equal(renamed.active_profile, "renamed");
  assert.equal("copy" in renamed.profiles, false);
  const deleted = freeLayout.profileAction(
    renamed, "delete", "renamed", ""
  );
  assert.equal(deleted.active_profile, "default");
  assert.equal("renamed" in deleted.profiles, false);
  assert.equal(
    freeLayout.profileAction(original, "delete", "default", ""),
    null,
  );
});

test("Phase 6 profile drafts, presets, and navigation protection are immutable", () => {
  const original = freeConfiguration(freeProfile([freeRegion()]));
  const imported = freeLayout.addImportedProfile(original, {
    profile_name: "imported",
    profile: freeProfile([]),
  });
  assert.equal(imported.active_profile, "imported");
  assert.equal(original.active_profile, "default");
  assert.notEqual(imported.profiles.imported, original.profiles.default);
  assert.equal(freeLayout.addImportedProfile(original, {
    profile_name: "default", profile: freeProfile([]),
  }), null);
  const fromPreset = freeLayout.createProfileFromPreset(original, {
    profile: freeProfile([]),
  }, "preset_profile");
  assert.equal(fromPreset.active_profile, "preset_profile");
  assert.equal(original.profiles.preset_profile, undefined);
  assert.equal(freeLayout.hasMeaningfulDraft({
    applied: original, draftDirty: true,
  }), true);
  assert.equal(freeLayout.hasMeaningfulDraft({
    applied: original, draftDirty: false,
  }), false);
});

test("Phase 6 browser file encoding preserves exact bytes", () => {
  const bytes = Uint8Array.from([0, 1, 127, 128, 255]);
  const encoded = freeLayout.arrayBufferToBase64(
    bytes.buffer,
    (binary) => Buffer.from(binary, "binary").toString("base64"),
  );
  assert.deepEqual(Buffer.from(encoded, "base64"), Buffer.from(bytes));
});

test("reusable color state canonicalizes complete values without repairing invalid drafts", () => {
  assert.equal(freeLayout.normalizeColorDraft("#a1b2c3"), "#A1B2C3");
  assert.equal(freeLayout.normalizeColorDraft("#A1B2"), "#A1B2");
  assert.equal(freeLayout.normalizeColorDraft("not-a-color"), "not-a-color");
  assert.deepEqual(
    freeLayout.colorControlState("#abcdef", "#123456"),
    {valid: true, draft: "#ABCDEF", picker: "#ABCDEF"},
  );
  assert.deepEqual(
    freeLayout.colorControlState("#ABC", "#123456"),
    {valid: false, draft: "#ABC", picker: "#123456"},
  );
});

test("metric availability diagnostics render backend labels and sanitized states", async () => {
  const fixture = freeEditorFixture();
  await fixture.editor.initialize();

  assert.equal(fixture.metricAvailability.children.length, 12);
  const rows = Object.fromEntries(fixture.metricAvailability.children.map(
    (row) => [row.children[0].textContent, row.children[1].textContent]
  ));
  assert.equal(rows.CPU, "Available");
  assert.equal(rows["NET RX"], "Awaiting sample");
  assert.equal(rows["GPU TEMP"], "Unavailable");
  assert.equal(JSON.stringify(rows).includes("/sys"), false);
  assert.equal(JSON.stringify(rows).includes("/proc"), false);
  assert.equal(fixture.metricAvailability.replaceCalls, 1);
  fixture.editor.render();
  assert.equal(fixture.metricAvailability.replaceCalls, 1);
});

test("physical preview atomically replaces stale clock and metric pixels", async () => {
  const first = Buffer.alloc(90 * 10 * 3);
  const second = Buffer.alloc(90 * 10 * 3);
  for (let y = 0; y < 10; y += 1) {
    for (let x = 0; x < 10; x += 1) {
      first.set([128, 0, 255], ((y * 90) + x) * 3);
    }
  }
  second.set([255, 255, 255], 0);
  second.set([20, 40, 60], 3);
  const fixture = freeEditorFixture({
    profiles: {
      default: freeProfile([freeRegion({
        x: 0, y: 0, width: 10, height: 10,
        widget: {type: "system_metric", settings: {
          ...widgetMetadata.widget_definitions.find(
            (definition) => definition.type === "system_metric"
          ).default_settings,
        }},
      })]),
    },
    validationFrames: [first.toString("base64"), second.toString("base64")],
  });
  await fixture.editor.initialize();
  const preview = fixture.elements.get("#draft-preview");
  const firstImage = preview.context.image;
  assert.deepEqual(
    Array.from(firstImage.data.slice(80 * 4, 80 * 4 + 3)),
    [128, 0, 255],
  );

  await fixture.editor.validateDraft();
  const secondImage = preview.context.image;
  assert.notEqual(secondImage, firstImage);
  assert.deepEqual(
    Array.from(secondImage.data.slice(80 * 4, 80 * 4 + 8)),
    [255, 255, 255, 255, 20, 40, 60, 255],
  );
  assert.equal(Array.from(secondImage.data).includes(128), false);
  const rgbPixels = Array.from({length: 90 * 10}, (_value, pixel) => (
    Array.from(secondImage.data.slice(pixel * 4, pixel * 4 + 3)).join(",")
  ));
  assert.deepEqual(
    new Set(rgbPixels),
    new Set(["0,0,0", "255,255,255", "20,40,60"]),
  );
});

test("color picker and hex draft stay synchronized and invalid color blocks Apply", async () => {
  const fixture = freeEditorFixture();
  await fixture.editor.initialize();
  const {control, picker, hex} = fixture.canvasColor;

  assert.equal(picker.value, "#000000");
  assert.equal(hex.value, "#000000");
  picker.value = "#12abef";
  picker.dispatch("input");
  assert.equal(hex.value, "#12ABEF");
  assert.equal(
    fixture.editor.state.draft.profiles.default.canvas.background_color,
    "#12ABEF",
  );

  hex.value = "#a0b1c2";
  hex.dispatch("input");
  assert.equal(hex.value, "#A0B1C2");
  assert.equal(picker.value, "#A0B1C2");
  assert.equal(control.dataset.invalid, "false");

  const lastPreview = fixture.editor.state.lastValidPreview;
  hex.value = "#A0B";
  hex.dispatch("input");
  assert.equal(hex.value, "#A0B");
  assert.equal(picker.value, "#A0B1C2");
  assert.equal(control.dataset.invalid, "true");
  assert.equal(hex.getAttribute("aria-invalid"), "true");
  assert.equal(fixture.elements.get("#fl-apply").disabled, true);
  assert.equal(fixture.elements.get("#fl-save").disabled, true);
  assert.equal(fixture.editor.state.lastValidPreview, lastPreview);
});

test("region background toggle preserves its last selected color", async () => {
  const fixture = freeEditorFixture();
  await fixture.editor.initialize();
  fixture.editor.state.selectedRegionId = "region-1";
  fixture.editor.render();
  const toggle = fixture.elements.get("#fl-use-region-background");

  toggle.checked = true;
  toggle.dispatch("change");
  fixture.regionColor.hex.value = "#1a2b3c";
  fixture.regionColor.hex.dispatch("input");
  assert.equal(
    fixture.editor.state.draft.profiles.default.regions[0].background_color,
    "#1A2B3C",
  );
  toggle.checked = false;
  toggle.dispatch("change");
  assert.equal(
    fixture.editor.state.draft.profiles.default.regions[0].background_color,
    null,
  );
  toggle.checked = true;
  toggle.dispatch("change");
  assert.equal(
    fixture.editor.state.draft.profiles.default.regions[0].background_color,
    "#1A2B3C",
  );
});

test("editor state distinguishes saved, draft, invalid, conflict, and reconnect", () => {
  const baseState = {
    applied: freeConfiguration(),
    conflict: false,
    disconnected: false,
    localValidation: {valid: true},
    serverValidation: "valid",
    draftDirty: false,
    appliedDirty: false,
  };
  assert.equal(freeLayout.deriveEditorState(baseState).label, "Saved");
  assert.equal(
    freeLayout.deriveEditorState({...baseState, draftDirty: true}).label,
    "Local draft",
  );
  assert.equal(
    freeLayout.deriveEditorState({
      ...baseState, localValidation: {valid: false},
    }).label,
    "Invalid draft",
  );
  assert.equal(
    freeLayout.deriveEditorState({...baseState, conflict: true}).label,
    "Revision conflict",
  );
  assert.equal(
    freeLayout.deriveEditorState({...baseState, disconnected: true}).label,
    "Disconnected · reconnecting",
  );
  assert.equal(
    freeLayout.deriveEditorState({...baseState, appliedDirty: true}).label,
    "Applied but unsaved",
  );
  assert.equal(
    freeLayout.deriveEditorState({
      ...baseState, migrationPending: true,
    }).label,
    "Migration pending · save required",
  );
});

test("Delete shortcuts are ignored while editing form content", () => {
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    assert.equal(freeLayout.isTextEntryTarget({tagName}), true);
  }
  assert.equal(
    freeLayout.isTextEntryTarget({
      tagName: "DIV", isContentEditable: true,
    }),
    true,
  );
  assert.equal(freeLayout.isTextEntryTarget({tagName: "BUTTON"}), false);
});

test("migration-pending canonical state saves without Apply", async () => {
  const fixture = freeEditorFixture({migrationPending: true});
  await fixture.editor.initialize();

  assert.equal(fixture.editor.state.draftDirty, false);
  assert.equal(fixture.editor.state.appliedDirty, false);
  assert.equal(fixture.editor.state.migrationPending, true);
  assert.equal(fixture.elements.get("#fl-save").disabled, false);
  assert.equal(
    fixture.elements.get("#fl-editor-state").textContent,
    "Migration pending · save required",
  );

  assert.equal(await fixture.editor.saveApplied(), true);
  assert.equal(fixture.editor.state.migrationPending, false);
  assert.equal(fixture.editor.state.activeRevision, 1);
  assert.equal(fixture.editor.state.savedRevision, 2);
  assert.equal(fixture.elements.get("#fl-save").disabled, true);
  assert.equal(
    fixture.elements.get("#fl-editor-state").textContent,
    "Saved",
  );
  const writes = fixture.calls.filter(
    (call) => call.options.method === "PUT"
  );
  assert.equal(writes.length, 1);
  assert.equal(JSON.parse(writes[0].options.body).apply, false);
  assert.equal(
    fixture.calls.some((call) => call.url.endsWith("/apply")),
    false,
  );
});

test("failed migration save preserves pending and applied state", async () => {
  const fixture = freeEditorFixture({
    migrationPending: true,
    saveStatus: 500,
  });
  await fixture.editor.initialize();
  const applied = JSON.stringify(fixture.editor.state.applied);

  assert.equal(await fixture.editor.saveApplied(), false);
  assert.equal(fixture.editor.state.migrationPending, true);
  assert.equal(fixture.editor.state.activeRevision, 1);
  assert.equal(fixture.editor.state.savedRevision, 1);
  assert.equal(JSON.stringify(fixture.editor.state.applied), applied);
  assert.equal(fixture.elements.get("#fl-save").disabled, false);
});

test("already-v2 saved state does not enable migration save", async () => {
  const fixture = freeEditorFixture({migrationPending: false});
  await fixture.editor.initialize();

  assert.equal(fixture.editor.state.migrationPending, false);
  assert.equal(fixture.elements.get("#fl-save").disabled, true);
  assert.equal(
    fixture.elements.get("#fl-editor-state").textContent,
    "Saved",
  );
});

test("normal region clicks toggle and different regions switch selection", async () => {
  const fixture = freeEditorFixture({
    profiles: {
      default: freeProfile([
        freeRegion(),
        freeRegion({id: "region-2", name: "Region 2", x: 20}),
      ]),
    },
  });
  await fixture.editor.initialize();
  const layer = fixture.elements.get("#fl-region-layer");
  const clickRegion = (index, pointerId) => {
    const overlay = layer.children[index];
    overlay.dispatch("pointerdown", {
      button: 0,
      pointerId,
      clientX: 50 + index * 200,
      clientY: 20,
    });
    fixture.windowListeners.pointerup({pointerId});
    overlay.dispatch("click", {detail: 1});
  };

  clickRegion(0, 1);
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");
  assert.equal(fixture.elements.get("#fl-selected-region").hidden, false);

  clickRegion(0, 2);
  assert.equal(fixture.editor.state.selectedRegionId, null);
  assert.equal(fixture.elements.get("#fl-selected-region").hidden, true);

  clickRegion(0, 3);
  clickRegion(1, 4);
  assert.equal(fixture.editor.state.selectedRegionId, "region-2");
  assert.equal(fixture.elements.get("#fl-selected-region").hidden, false);
});

test("region selection survives click release, inspector, drag, resize, and preview", async () => {
  const fixture = freeEditorFixture();
  await fixture.editor.initialize();
  const layer = fixture.elements.get("#fl-region-layer");

  let overlay = layer.children[0];
  overlay.dispatch("pointerdown", {
    button: 0,
    pointerId: 7,
    clientX: 50,
    clientY: 20,
  });
  fixture.windowListeners.pointerup({pointerId: 7});
  layer.dispatch("click", {target: layer});
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");
  assert.equal(fixture.elements.get("#fl-selected-region").hidden, false);

  const name = fixture.elements.get("#fl-region-name");
  name.value = "Selected clock";
  fixture.elements.get("#fl-region-inspector").dispatch("input", {
    target: name,
  });
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");

  overlay = layer.children[0];
  overlay.dispatch("pointerdown", {
    button: 0,
    pointerId: 8,
    clientX: 50,
    clientY: 20,
  });
  fixture.windowListeners.pointermove({
    pointerId: 8,
    clientX: 150,
    clientY: 30,
  });
  fixture.windowListeners.pointerup({pointerId: 8});
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");

  overlay = layer.children[0];
  const resizeHandle = overlay.children[1];
  overlay.dispatch("pointerdown", {
    target: resizeHandle,
    button: 0,
    pointerId: 9,
    clientX: 150,
    clientY: 30,
  });
  fixture.windowListeners.pointermove({
    pointerId: 9,
    clientX: 140,
    clientY: 20,
  });
  fixture.windowListeners.pointerup({pointerId: 9});
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");

  await fixture.editor.validateDraft();
  fixture.editor.render();
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");
  assert.equal(fixture.elements.get("#fl-selected-region").hidden, false);
});

test("keyboard activation toggles selected regions", async () => {
  const fixture = freeEditorFixture();
  await fixture.editor.initialize();
  const layer = fixture.elements.get("#fl-region-layer");

  layer.children[0].dispatch("keydown", {key: "Enter"});
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");
  layer.children[0].dispatch("keydown", {key: " "});
  assert.equal(fixture.editor.state.selectedRegionId, null);
});

test("selection clears only for unused canvas, deletion, or missing profile region", async () => {
  const profiles = {
    default: freeProfile([freeRegion()]),
    matching: freeProfile([freeRegion({name: "Matching"})]),
    different: freeProfile([freeRegion({
      id: "region-2", name: "Different",
    })]),
  };
  const fixture = freeEditorFixture({profiles});
  await fixture.editor.initialize();
  const layer = fixture.elements.get("#fl-region-layer");

  layer.children[0].dispatch("pointerdown", {
    button: 0,
    pointerId: 1,
    clientX: 20,
    clientY: 20,
  });
  fixture.windowListeners.pointerup({pointerId: 1});
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");

  const profile = fixture.elements.get("#fl-profile");
  profile.value = "matching";
  profile.dispatch("change");
  assert.equal(fixture.editor.state.selectedRegionId, "region-1");

  profile.value = "different";
  profile.dispatch("change");
  assert.equal(fixture.editor.state.selectedRegionId, null);

  layer.children[0].dispatch("pointerdown", {
    button: 0,
    pointerId: 2,
    clientX: 20,
    clientY: 20,
  });
  fixture.windowListeners.pointerup({pointerId: 2});
  assert.equal(fixture.editor.state.selectedRegionId, "region-2");
  layer.dispatch("pointerdown", {target: layer});
  assert.equal(fixture.editor.state.selectedRegionId, null);

  layer.children[0].dispatch("pointerdown", {
    button: 0,
    pointerId: 3,
    clientX: 20,
    clientY: 20,
  });
  fixture.windowListeners.pointerup({pointerId: 3});
  fixture.elements.get("#fl-delete-region").dispatch("click");
  assert.equal(fixture.editor.state.selectedRegionId, null);
});
