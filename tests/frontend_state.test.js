"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const frontend = require("../src/nextuxlinq/static/app.js");

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
    this.focused = false;
    this.replaceCalls = 0;
    this.styleValues = {};
    this.style = {
      setProperty: (name, value) => {
        this.styleValues[name] = value;
      },
    };
  }

  addEventListener(name, callback) {
    if (!this.listeners[name]) this.listeners[name] = [];
    this.listeners[name].push(callback);
  }

  dispatch(name, event = {}) {
    for (const callback of this.listeners[name] || []) callback(event);
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

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  focus() {
    this.focused = true;
  }

  getContext() {
    return {
      createImageData(width, height) {
        return {data: new Uint8ClampedArray(width * height * 4)};
      },
      putImageData() {},
    };
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
    "#draft-state",
    "#runtime-state",
    "#state-active-revision",
    "#state-saved-revision",
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
    "#status-dirty",
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

function controllerFixture(responses = []) {
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
    decodeBase64() {
      return "\0".repeat(2700);
    },
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
  assert.equal(
    presentation.primary,
    "Applied settings differ from the saved configuration.",
  );
  state.draft.dirty = true;
  presentation = frontend.deriveConfigurationState(state);
  assert.equal(presentation.primary, "Draft changes have not been applied.");
  assert.match(presentation.secondary, /currently applied settings also differ/);
  state.draft.dirty = false;
  state.runtime.dirty = false;
  assert.equal(
    frontend.deriveConfigurationState(state).primary,
    "Applied and saved settings are synchronized.",
  );
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

test("valid preview regions preserve API order and render names safely", () => {
  const document = fakeDocument();
  const list = document.elements.get("#region-labels");
  const regions = [
    {name: "<Front>", start_pixel: 0, width: 24, type: "clock"},
    {name: "Gap", start_pixel: 24, width: 66, type: "gap"},
  ];
  const normalized = frontend.normalizeRegions(regions, 90);
  assert.equal(normalized.valid, true);
  frontend.renderRegions(document, list, normalized.regions);
  assert.equal(list.children[0].children[0].textContent, "<Front>");
  assert.equal(list.children[0].styleValues["--region-width"], "24");
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
    [],
    [{name: "Only", start_pixel: 0, width: 20, type: "clock"}],
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
  assert.equal(controller.elements.deviceStatus.textContent, "Detected");
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
