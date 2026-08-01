"use strict";

(function exposeFrontend(root, factory) {
  const frontend = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = frontend;
  }
  if (root) {
    root.NexTuxLinqFrontend = frontend;
  }
  if (root && root.document) {
    frontend.bootstrap({
      document: root.document,
      fetch: root.fetch.bind(root),
      setTimeout: root.setTimeout.bind(root),
      clearTimeout: root.clearTimeout.bind(root),
      decodeBase64: root.atob.bind(root),
      window: root,
      previewPollingEnabled: !root.document.querySelector(
        "#free-layout-root"
      ),
    });
  }
})(typeof globalThis === "object" ? globalThis : this, function frontendFactory() {
  const API_PREFIX = "/api/v1";
  const UNAVAILABLE_MESSAGE = "Daemon unavailable. Reconnecting…";
  const EDITABLE_FIELDS = Object.freeze([
    "scrolling_text",
    "text_color",
    "clock_color",
    "scroll_speed",
    "brightness",
  ]);
  const FIELD_IDS = Object.freeze({
    scrolling_text: ["#scrolling-text", "#scrolling-text-error"],
    text_color: ["#text-color", "#text-color-error"],
    clock_color: ["#clock-color", "#clock-color-error"],
    scroll_speed: ["#scroll-speed", "#scroll-speed-error"],
    brightness: ["#brightness", "#brightness-error"],
  });
  const FALLBACK_REGIONS = Object.freeze([
    Object.freeze({name: "Front clock", start_pixel: 0, width: 24, type: "clock"}),
    Object.freeze({name: "Gap", start_pixel: 24, width: 4, type: "gap"}),
    Object.freeze({
      name: "Scrolling text",
      start_pixel: 28,
      width: 31,
      type: "scrolling-text",
    }),
    Object.freeze({name: "Rear clock", start_pixel: 59, width: 31, type: "clock"}),
  ]);

  class DaemonUnavailableError extends Error {
    constructor() {
      super(UNAVAILABLE_MESSAGE);
      this.name = "DaemonUnavailableError";
    }
  }

  class ApiRequestError extends Error {
    constructor(message, details = {}) {
      super(message);
      this.name = "ApiRequestError";
      this.status = details.status ?? null;
      this.code = details.code ?? null;
      this.fieldErrors = Array.isArray(details.fieldErrors)
        ? details.fieldErrors : [];
      this.requestId = typeof details.requestId === "string"
        ? details.requestId : null;
    }
  }

  function createInitialState() {
    return {
      runtime: {
        configuration: null,
        activeRevision: null,
        savedRevision: null,
        dirty: false,
        acceptedFingerprint: null,
        activeProfileName: null,
        legacyEditorSupported: true,
      },
      draft: {
        values: null,
        baseline: null,
        dirty: false,
      },
      connection: {
        reachable: null,
        epoch: 0,
        lastAutomaticReconciliationEpoch: null,
        reconciliationState: "idle",
        pendingRemoteSnapshot: null,
        warnedRemoteFingerprint: null,
        deferredReconciliation: false,
        deferredReconciliationReason: null,
      },
      ui: {
        submitting: false,
        actionError: null,
        backgroundError: null,
        fieldErrors: {},
      },
    };
  }

  function deepClone(value) {
    return value === null || value === undefined
      ? value : JSON.parse(JSON.stringify(value));
  }

  function stableStringify(value) {
    if (value === null || typeof value !== "object") {
      return JSON.stringify(value);
    }
    if (Array.isArray(value)) {
      return `[${value.map(stableStringify).join(",")}]`;
    }
    return `{${Object.keys(value).sort().map(
      (key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`
    ).join(",")}}`;
  }

  function configurationFingerprint(configuration) {
    return stableStringify(configuration);
  }

  function canonicalEditableValues(values) {
    const speed = Number(values.scroll_speed);
    const brightness = values.brightness === "" || values.brightness === null
      || values.brightness === undefined
      ? null : Number(values.brightness);
    return {
      scrolling_text: String(values.scrolling_text ?? ""),
      text_color: String(values.text_color ?? "").toUpperCase(),
      clock_color: String(values.clock_color ?? "").toUpperCase(),
      scroll_speed: speed,
      brightness,
    };
  }

  function editableProfileProjection(profile) {
    return canonicalEditableValues(profile);
  }

  function editableValuesEqual(left, right) {
    if (left === null || right === null) return left === right;
    const normalizedLeft = canonicalEditableValues(left);
    const normalizedRight = canonicalEditableValues(right);
    return EDITABLE_FIELDS.every((field) => (
      Object.is(normalizedLeft[field], normalizedRight[field])
    ));
  }

  function deriveConfigurationState(state) {
    if (!state.runtime.legacyEditorSupported) {
      return {
        primary: "Free-layout profile is read-only",
        detail: "The current editor cannot modify this profile. Preview and status remain available.",
        className: "state-read-only",
      };
    }
    if (state.runtime.configuration === null) {
      return {
        primary: "Loading settings…",
        detail: "The applied and saved profile state is loading.",
        className: "",
      };
    }
    if (state.draft.dirty) {
      return {
        primary: state.runtime.dirty
          ? "Draft changes not applied; applied settings are also not saved."
          : "Draft changes not applied",
        detail: state.runtime.dirty
          ? "Local draft changes are not applied, and the currently applied settings differ from the saved configuration."
          : "Local draft changes have not been applied to the runtime profile.",
        className: "state-draft",
      };
    }
    if (state.runtime.dirty) {
      return {
        primary: "Applied changes not saved",
        detail: "The applied runtime profile differs from the saved configuration.",
        className: "state-unsaved",
      };
    }
    return {
      primary: "Saved",
      detail: "The applied runtime profile and saved configuration are synchronized.",
      className: "state-synchronized",
    };
  }

  function remoteFingerprint(snapshot) {
    const canonical = snapshot.config.canonical_configuration
      || snapshot.config.configuration;
    return stableStringify({
      configuration: canonical,
      active_revision: snapshot.config.active_revision,
      saved_revision: snapshot.config.saved_revision,
      dirty: snapshot.config.dirty,
      active_profile: canonical.active_profile,
    });
  }

  function remoteMatchesAccepted(state, snapshot) {
    return (
      state.runtime.activeRevision === snapshot.config.active_revision
      && state.runtime.savedRevision === snapshot.config.saved_revision
      && state.runtime.dirty === snapshot.config.dirty
      && state.runtime.acceptedFingerprint
        === configurationFingerprint(
          snapshot.config.canonical_configuration
            || snapshot.config.configuration
        )
      && state.runtime.activeProfileName
        === (snapshot.config.canonical_configuration
          || snapshot.config.configuration).active_profile
    );
  }

  function applySuccessfulResponse(state, result) {
    const configuration = deepClone(state.runtime.configuration);
    configuration.profiles[result.profile_name] = deepClone(result.profile);
    configuration.active_profile = result.profile_name;
    state.runtime.configuration = configuration;
    state.runtime.activeProfileName = result.profile_name;
    state.runtime.legacyEditorSupported = true;
    state.runtime.activeRevision = result.active_revision;
    state.runtime.dirty = Boolean(result.dirty);
    state.runtime.acceptedFingerprint = configurationFingerprint(configuration);
    state.draft.values = editableProfileProjection(result.profile);
    state.draft.baseline = editableProfileProjection(result.profile);
    state.draft.dirty = false;
  }

  function saveSuccessfulResponse(state, result) {
    state.runtime.configuration = deepClone(result.configuration);
    state.runtime.activeProfileName = result.configuration.active_profile;
    state.runtime.legacyEditorSupported = true;
    state.runtime.activeRevision = result.active_revision;
    state.runtime.savedRevision = result.saved_revision;
    state.runtime.dirty = Boolean(result.dirty);
    state.runtime.acceptedFingerprint = configurationFingerprint(
      result.configuration
    );
    const profile = result.configuration.profiles[
      result.configuration.active_profile
    ];
    state.draft.values = editableProfileProjection(profile);
    state.draft.baseline = editableProfileProjection(profile);
    state.draft.dirty = false;
  }

  function normalizeRegions(regions, previewWidth = 90, previewHeight = 10) {
    if (!Number.isInteger(previewWidth) || previewWidth <= 0
        || !Number.isInteger(previewHeight) || previewHeight <= 0
        || !Array.isArray(regions)) {
      return {regions: FALLBACK_REGIONS, valid: false};
    }
    const normalized = [];
    const normalizedNames = new Set();
    for (const region of regions) {
      const displayName = region && typeof region.name === "string"
        ? region.name.trim() : "";
      const normalizedName = displayName.normalize("NFKC").toLowerCase();
      if (!region || typeof region !== "object"
          || typeof region.name !== "string"
          || displayName === ""
          || displayName.length > 80
          || /[\p{C}\p{Zl}\p{Zp}]/u.test(region.name)
          || !/[\p{L}\p{N}\p{P}\p{S}]/u.test(displayName)
          || normalizedNames.has(normalizedName)
          || !Number.isInteger(
            region.start_pixel === undefined ? region.x : region.start_pixel
          )
          || !Number.isInteger(region.width)
          || region.width <= 0
          || (region.start_pixel ?? region.x) < 0
          || (region.start_pixel ?? region.x) + region.width > previewWidth
          || !Number.isInteger(region.y ?? 0)
          || !Number.isInteger(region.height ?? previewHeight)
          || (region.y ?? 0) < 0
          || (region.height ?? previewHeight) <= 0
          || (region.y ?? 0) + (region.height ?? previewHeight) > previewHeight
          || typeof region.type !== "string"
          || !/^[a-z0-9-]+$/.test(region.type)) {
        return {regions: FALLBACK_REGIONS, valid: false};
      }
      normalizedNames.add(normalizedName);
      normalized.push({
        name: displayName,
        start_pixel: region.start_pixel ?? region.x,
        y: region.y ?? 0,
        width: region.width,
        height: region.height ?? previewHeight,
        enabled: region.enabled !== false,
        type: region.type,
      });
    }
    for (let index = 0; index < normalized.length; index += 1) {
      const left = normalized[index];
      for (const right of normalized.slice(index + 1)) {
        const overlaps = (
          left.start_pixel < right.start_pixel + right.width
          && right.start_pixel < left.start_pixel + left.width
          && left.y < right.y + right.height
          && right.y < left.y + left.height
        );
        if (overlaps) return {regions: FALLBACK_REGIONS, valid: false};
      }
    }
    return {regions: normalized, valid: true};
  }

  function regionFingerprint(regions) {
    return stableStringify(regions);
  }

  function viewerX(logicalX, regionWidth, canvasWidth) {
    return canvasWidth - logicalX - regionWidth;
  }

  function placeRegionsForViewer(regions, canvasWidth) {
    return regions.map((region) => ({
      ...region,
      viewer_start_pixel: viewerX(
        region.start_pixel,
        region.width,
        canvasWidth,
      ),
    })).sort(
      (left, right) => left.viewer_start_pixel - right.viewer_start_pixel,
    );
  }

  function copyFramebufferForViewer(
    binary,
    image,
    canvasWidth,
    canvasHeight,
    regions,
  ) {
    image.data.fill(0);
    const covered = new Uint8Array(canvasWidth * canvasHeight);
    for (const region of regions) {
      if (region.enabled === false) continue;
      const regionY = region.y ?? 0;
      const regionHeight = region.height ?? canvasHeight;
      for (let y = regionY; y < regionY + regionHeight; y += 1) {
        for (let localX = 0; localX < region.width; localX += 1) {
          covered[(y * canvasWidth) + region.start_pixel + localX] = 1;
        }
      }
    }
    const backgroundPixel = covered.findIndex((value) => value === 0);
    const backgroundOffset = backgroundPixel < 0 ? -1 : backgroundPixel * 3;
    const background = backgroundOffset < 0
      ? [0, 0, 0]
      : [
        binary.charCodeAt(backgroundOffset),
        binary.charCodeAt(backgroundOffset + 1),
        binary.charCodeAt(backgroundOffset + 2),
      ];
    for (let y = 0; y < canvasHeight; y += 1) {
      for (let x = 0; x < canvasWidth; x += 1) {
        const targetPixel = ((y * canvasWidth) + x) * 4;
        image.data[targetPixel] = background[0];
        image.data[targetPixel + 1] = background[1];
        image.data[targetPixel + 2] = background[2];
        image.data[targetPixel + 3] = 255;
      }
    }
    for (const region of regions) {
      if (region.enabled === false) continue;
      const regionY = region.y ?? 0;
      const regionHeight = region.height ?? canvasHeight;
      for (let y = regionY; y < regionY + regionHeight; y += 1) {
        for (let localX = 0; localX < region.width; localX += 1) {
          const sourcePixel = (
            (y * canvasWidth) + region.start_pixel + localX
          ) * 3;
          const targetPixel = (
            (y * canvasWidth) + region.viewer_start_pixel + localX
          ) * 4;
          image.data[targetPixel] = binary.charCodeAt(sourcePixel);
          image.data[targetPixel + 1] = binary.charCodeAt(sourcePixel + 1);
          image.data[targetPixel + 2] = binary.charCodeAt(sourcePixel + 2);
          image.data[targetPixel + 3] = 255;
        }
      }
    }
  }

  function renderRegions(documentObject, list, regions) {
    const children = regions.map((region) => {
      const item = documentObject.createElement("li");
      item.style.setProperty("--region-width", String(region.width));
      item.style.setProperty(
        "--region-viewer-start",
        String(region.viewer_start_pixel + 1),
      );
      item.setAttribute("data-region-type", region.type);
      item.setAttribute(
        "data-logical-start-pixel",
        String(region.start_pixel),
      );
      item.setAttribute(
        "data-viewer-start-pixel",
        String(region.viewer_start_pixel),
      );
      const name = documentObject.createElement("span");
      name.textContent = region.name;
      const width = documentObject.createElement("strong");
      width.textContent = `${region.width} px`;
      item.append(name, width);
      return item;
    });
    list.replaceChildren(...children);
  }

  function formatLastUpload(uploadTimestamp, referenceTimestamp) {
    if (uploadTimestamp === null || uploadTimestamp === undefined) {
      return "Not yet";
    }
    if (referenceTimestamp === null || referenceTimestamp === undefined
        || !Number.isFinite(Number(uploadTimestamp))
        || !Number.isFinite(Number(referenceTimestamp))) {
      return "Recorded; awaiting preview timing";
    }
    const elapsed = Math.max(
      0,
      Number(referenceTimestamp) - Number(uploadTimestamp),
    );
    if (elapsed < 5) return "Just now";
    if (elapsed < 60) {
      const seconds = Math.round(elapsed);
      return `${seconds} second${seconds === 1 ? "" : "s"} ago`;
    }
    const minutes = Math.round(elapsed / 60);
    return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }

  function mapStructuredFieldErrors(fieldErrors) {
    const mapped = {};
    const unknown = [];
    for (const item of Array.isArray(fieldErrors) ? fieldErrors : []) {
      const path = typeof item.field === "string" ? item.field : "";
      const message = typeof item.message === "string"
        ? item.message : "This field is invalid.";
      const parts = path.split(".");
      const field = (
        parts.length === 2
        && parts[0] === "profile"
        && EDITABLE_FIELDS.includes(parts[1])
      ) ? parts[1] : (
        parts.length === 4
        && parts[0] === "configuration"
        && parts[1] === "profiles"
        && parts[2] !== ""
        && EDITABLE_FIELDS.includes(parts[3])
      ) ? parts[3] : null;
      if (field) {
        if (!mapped[field]) mapped[field] = [];
        mapped[field].push(message);
      } else {
        unknown.push(message);
      }
    }
    return {mapped, unknown};
  }

  function createController(dependencies) {
    const documentObject = dependencies.document;
    const fetchFunction = dependencies.fetch;
    const setTimeoutFunction = dependencies.setTimeout;
    const clearTimeoutFunction = dependencies.clearTimeout;
    const previewPollingEnabled = dependencies.previewPollingEnabled !== false;
    const decodeBase64 = dependencies.decodeBase64;
    const windowObject = dependencies.window ?? null;
    const state = createInitialState();
    const elements = {
      form: documentObject.querySelector("#profile-form"),
      message: documentObject.querySelector("#message"),
      backgroundMessage: documentObject.querySelector("#background-message"),
      connectionMessage: documentObject.querySelector("#connection-message"),
      reconciliationPanel: documentObject.querySelector("#reconciliation-panel"),
      reconciliationMessage: documentObject.querySelector("#reconciliation-message"),
      configurationState: documentObject.querySelector("#configuration-state"),
      configurationSummary: documentObject.querySelector(
        "#configuration-state-summary"
      ),
      configurationDetail: documentObject.querySelector(
        "#configuration-state-detail"
      ),
      regionLabels: documentObject.querySelector("#region-labels"),
      canvas: documentObject.querySelector("#preview"),
      daemonState: documentObject.querySelector("#daemon-state"),
      deviceStatus: documentObject.querySelector("#device-status"),
      modelName: documentObject.querySelector("#model-name"),
      vidPid: documentObject.querySelector("#vid-pid"),
      devicePath: documentObject.querySelector("#device-path"),
      activeProfile: documentObject.querySelector("#active-profile"),
      configurationRevision: documentObject.querySelector(
        "#configuration-revision"
      ),
      statusSavedRevision: documentObject.querySelector("#status-saved-revision"),
      renderingState: documentObject.querySelector("#rendering-state"),
      runtimeMode: documentObject.querySelector("#runtime-mode"),
      serviceAutostart: documentObject.querySelector("#service-autostart"),
      serviceActive: documentObject.querySelector("#service-active"),
      openlinkhubRecovery: documentObject.querySelector(
        "#openlinkhub-recovery"
      ),
      frameRate: documentObject.querySelector("#frame-rate"),
      lastUpload: documentObject.querySelector("#last-upload"),
      lastError: documentObject.querySelector("#last-error"),
      multipleDeviceWarning: documentObject.querySelector(
        "#multiple-device-warning"
      ),
      previewRevision: documentObject.querySelector("#preview-revision"),
      applyButton: documentObject.querySelector("[data-action='apply']"),
      saveButton: documentObject.querySelector("[data-action='save']"),
      defaultsButton: documentObject.querySelector("[data-action='defaults']"),
      reloadButton: documentObject.querySelector(
        "[data-action='reload-applied']"
      ),
      keepDraftButton: documentObject.querySelector(
        "[data-action='keep-local-draft']"
      ),
      reloadReconciliationButton: documentObject.querySelector(
        "[data-action='reload-reconciliation']"
      ),
    };
    elements.context = elements.canvas.getContext("2d");
    const controls = {};
    const errorElements = {};
    for (const [field, [controlSelector, errorSelector]] of Object.entries(
      FIELD_IDS
    )) {
      controls[field] = documentObject.querySelector(controlSelector);
      errorElements[field] = documentObject.querySelector(errorSelector);
    }

    let defaults = null;
    let polling = true;
    let statusTimer = null;
    let previewTimer = null;
    let reconciliationPromise = null;
    let latestStatus = null;
    let latestPreviewTimestamp = null;
    let renderedRegionFingerprint = null;

    function activeProfile(configuration = state.runtime.configuration) {
      if (configuration === null) return null;
      return configuration.profiles[configuration.active_profile];
    }

    function readDraftValues() {
      return canonicalEditableValues({
        scrolling_text: controls.scrolling_text.value,
        text_color: controls.text_color.value,
        clock_color: controls.clock_color.value,
        scroll_speed: controls.scroll_speed.value,
        brightness: controls.brightness.value,
      });
    }

    function fillForm(profile) {
      controls.scrolling_text.value = profile.scrolling_text;
      controls.text_color.value = profile.text_color;
      controls.clock_color.value = profile.clock_color;
      controls.scroll_speed.value = String(profile.scroll_speed);
      controls.brightness.value = profile.brightness === null
        ? "" : String(profile.brightness);
    }

    function setDraftBaseline(profile, {fill = true, values = null} = {}) {
      if (fill) fillForm(profile);
      state.draft.baseline = editableProfileProjection(profile);
      state.draft.values = values === null ? readDraftValues() : values;
      state.draft.dirty = !editableValuesEqual(
        state.draft.values,
        state.draft.baseline,
      );
      renderConfigurationState();
    }

    function updateDraftFromForm() {
      if (state.runtime.configuration === null) return;
      state.draft.values = readDraftValues();
      state.draft.dirty = !editableValuesEqual(
        state.draft.values,
        state.draft.baseline,
      );
      renderConfigurationState();
    }

    function renderConfigurationState() {
      const presentation = deriveConfigurationState(state);
      elements.configurationSummary.textContent = presentation.primary;
      elements.configurationDetail.textContent = presentation.detail;
      elements.configurationState.className = (
        `configuration-state ${presentation.className}`
      ).trim();
    }

    function renderStatus(status = latestStatus) {
      if (!status) return;
      elements.daemonState.textContent = status.daemon_state;
      elements.deviceStatus.textContent = status.device_available
        ? `${status.model_name || "Matrix900"} connected`
        : "Matrix900 unavailable";
      elements.modelName.textContent = status.model_name || "Not detected";
      elements.vidPid.textContent = `${status.usb_vid}:${status.usb_pid}`;
      elements.devicePath.textContent = status.device_path || "—";
      elements.activeProfile.textContent = state.runtime.configuration
        ? state.runtime.configuration.active_profile
        : state.runtime.activeProfileName || status.active_profile;
      elements.configurationRevision.textContent =
        state.runtime.activeRevision === null
          ? String(status.active_revision)
          : String(state.runtime.activeRevision);
      const displayedSavedRevision = state.runtime.configuration
        ? state.runtime.savedRevision : status.saved_revision;
      elements.statusSavedRevision.textContent =
        displayedSavedRevision === null
          ? "Not saved yet" : String(displayedSavedRevision);
      elements.renderingState.textContent = status.rendering_state;
      elements.runtimeMode.textContent = ({
        systemd_user_service: "systemd user service",
        manual: "Manual",
        unknown: "Unavailable / unknown",
      })[status.runtime_mode] || "Unavailable / unknown";
      elements.serviceAutostart.textContent = status.service_enabled === true
        ? "Enabled"
        : status.service_enabled === false ? "Disabled" : "Unknown";
      elements.serviceActive.textContent = status.service_active === true
        ? "Active"
        : status.service_active === false ? "Inactive" : "Unknown";
      elements.openlinkhubRecovery.textContent = (
        status.openlinkhub_recovery_enabled === true
          ? "Enabled"
          : status.openlinkhub_recovery_enabled === false ? "Disabled" : "Unknown"
      );
      const frameRate = Number(status.frame_rate);
      elements.frameRate.textContent = Number.isFinite(frameRate)
        ? `${frameRate} FPS` : "—";
      elements.lastUpload.textContent = formatLastUpload(
        status.last_successful_upload_time,
        latestPreviewTimestamp,
      );
      const reportedErrors = [
        status.last_display_error,
        status.configuration_warning,
      ].filter((message) => typeof message === "string" && message !== "");
      elements.lastError.textContent = reportedErrors.length
        ? reportedErrors.join(" — ") : "None";
      elements.multipleDeviceWarning.textContent =
        status.multiple_device_warning || "";
    }

    function renderReconciliation() {
      const pending = state.connection.pendingRemoteSnapshot;
      const show = (
        state.connection.reconciliationState === "pending" && pending !== null
      );
      elements.reconciliationPanel.hidden = !show;
      if (!show) {
        elements.reconciliationMessage.textContent = "";
        return;
      }
      const previousProfile = state.runtime.configuration
        ? state.runtime.configuration.active_profile : null;
      const remoteConfiguration = pending.config.canonical_configuration
        || pending.config.configuration;
      const remoteProfile = remoteConfiguration.active_profile;
      const profileChange = previousProfile !== null
        && previousProfile !== remoteProfile
        ? ` The applied profile changed from ${previousProfile} to ${remoteProfile}.`
        : "";
      elements.reconciliationMessage.textContent = (
        "The applied configuration changed while local draft edits were "
        + "preserved. Choose how to reconcile them."
        + profileChange
      );
    }

    function updateControls() {
      const unavailable = (
        state.connection.reachable !== true
        || state.runtime.configuration === null
        || !state.runtime.legacyEditorSupported
      );
      const reconciling = ["fetching", "pending"].includes(
        state.connection.reconciliationState
      );
      const mutationDisabled = state.ui.submitting || unavailable || reconciling;
      elements.applyButton.disabled = mutationDisabled;
      elements.saveButton.disabled = mutationDisabled;
      elements.reloadButton.disabled = mutationDisabled;
      elements.defaultsButton.disabled = mutationDisabled || defaults === null;
      for (const control of Object.values(controls)) {
        control.disabled = mutationDisabled;
      }
      const pending = state.connection.pendingRemoteSnapshot;
      const pendingSupportsLegacyEditor = (
        pending !== null
        && pending.config.legacy_editor_supported !== false
        && pending.config.configuration !== null
      );
      elements.keepDraftButton.disabled = (
        state.ui.submitting
        || state.connection.reconciliationState !== "pending"
        || !pendingSupportsLegacyEditor
      );
      elements.reloadReconciliationButton.disabled = (
        state.ui.submitting
        || state.connection.reconciliationState !== "pending"
      );
    }

    function showActionMessage(text, kind = "") {
      elements.message.textContent = text;
      elements.message.className = `message ${kind}`.trim();
      state.ui.actionError = kind === "error" ? text : null;
    }

    function showBackgroundMessage(text) {
      state.ui.backgroundError = text || null;
      elements.backgroundMessage.textContent = text;
    }

    function deferReconciliation(reason) {
      state.connection.deferredReconciliation = true;
      if (
        state.connection.deferredReconciliationReason !== "reconnect"
        || reason === "reconnect"
      ) {
        state.connection.deferredReconciliationReason = reason;
      }
    }

    function markDaemonUnavailable() {
      state.connection.reachable = false;
      state.connection.pendingRemoteSnapshot = null;
      state.connection.reconciliationState = "idle";
      state.connection.deferredReconciliation = false;
      state.connection.deferredReconciliationReason = null;
      elements.connectionMessage.textContent = UNAVAILABLE_MESSAGE;
      renderReconciliation();
      updateControls();
    }

    function markDaemonReachable() {
      const reconnected = state.connection.reachable === false;
      state.connection.reachable = true;
      elements.connectionMessage.textContent = "";
      updateControls();
      if (reconnected) {
        state.connection.epoch += 1;
        state.connection.warnedRemoteFingerprint = null;
        state.connection.deferredReconciliation = false;
        state.connection.deferredReconciliationReason = null;
        if (state.ui.submitting) {
          deferReconciliation("reconnect");
        } else {
          void beginReconciliation("reconnect");
        }
      }
    }

    async function request(path, options = {}) {
      let response;
      try {
        response = await fetchFunction(`${API_PREFIX}${path}`, options);
      } catch (_error) {
        markDaemonUnavailable();
        throw new DaemonUnavailableError();
      }
      let rawBody;
      try {
        rawBody = await response.text();
      } catch (_error) {
        markDaemonReachable();
        throw new ApiRequestError("The daemon returned an invalid response.", {
          status: response.status,
        });
      }
      let payload = null;
      if (rawBody.trim() !== "") {
        try {
          payload = JSON.parse(rawBody);
        } catch (_error) {
          markDaemonReachable();
          throw new ApiRequestError(
            response.ok
              ? "The daemon returned an invalid response."
              : `The daemon rejected the request (HTTP ${response.status}).`,
            {status: response.status},
          );
        }
      }
      markDaemonReachable();
      if (!response.ok) {
        const structured = payload && typeof payload === "object" ? payload : {};
        throw new ApiRequestError(
          typeof structured.message === "string"
            ? structured.message
            : `The daemon rejected the request (HTTP ${response.status}).`,
          {
            status: response.status,
            code: structured.code,
            fieldErrors: structured.field_errors,
            requestId: structured.request_id,
          },
        );
      }
      if (payload === null || typeof payload !== "object") {
        throw new ApiRequestError("The daemon returned an invalid response.", {
          status: response.status,
        });
      }
      return payload;
    }

    function clearFieldErrors(field = null) {
      const fields = field === null ? EDITABLE_FIELDS : [field];
      for (const name of fields) {
        delete state.ui.fieldErrors[name];
        errorElements[name].textContent = "";
        controls[name].removeAttribute("aria-invalid");
      }
    }

    function displayStructuredFieldErrors(error) {
      const {mapped, unknown} = mapStructuredFieldErrors(error.fieldErrors);
      state.ui.fieldErrors = mapped;
      for (const [field, messages] of Object.entries(mapped)) {
        errorElements[field].textContent = messages.join(" ");
        controls[field].setAttribute("aria-invalid", "true");
      }
      return unknown;
    }

    function validateForm() {
      clearFieldErrors();
      if (elements.form.checkValidity()) return true;
      elements.form.reportValidity();
      const invalid = elements.form.querySelector(":invalid");
      if (invalid && typeof invalid.focus === "function") invalid.focus();
      return false;
    }

    function profileFromDraft(baseProfile, values = readDraftValues()) {
      return {
        ...deepClone(baseProfile),
        scrolling_text: values.scrolling_text,
        text_color: values.text_color,
        clock_color: values.clock_color,
        scroll_speed: Number(values.scroll_speed),
        brightness: values.brightness,
      };
    }

    function acceptRemoteSnapshot(
      snapshot,
      {fill = true, preservedDraft = null} = {},
    ) {
      const canonical = snapshot.config.canonical_configuration
        || snapshot.config.configuration;
      const supported = snapshot.config.legacy_editor_supported !== false
        && snapshot.config.configuration !== null;
      state.runtime.configuration = supported
        ? deepClone(snapshot.config.configuration) : null;
      state.runtime.legacyEditorSupported = supported;
      state.runtime.activeProfileName = canonical.active_profile;
      state.runtime.activeRevision = snapshot.config.active_revision;
      state.runtime.savedRevision = snapshot.config.saved_revision;
      state.runtime.dirty = Boolean(snapshot.config.dirty);
      state.runtime.acceptedFingerprint = configurationFingerprint(
        canonical
      );
      defaults = deepClone(snapshot.config.built_in_default);
      latestStatus = snapshot.status || latestStatus;
      if (supported) {
        const profile = activeProfile();
        setDraftBaseline(profile, {
          fill,
          values: preservedDraft,
        });
      } else {
        state.draft.values = null;
        state.draft.baseline = null;
        state.draft.dirty = false;
        showActionMessage(
          snapshot.config.legacy_editor_message
            || "This free-layout profile is read-only in the current editor.",
          "warning",
        );
        renderConfigurationState();
      }
      state.connection.pendingRemoteSnapshot = null;
      state.connection.reconciliationState = "idle";
      state.connection.warnedRemoteFingerprint = null;
      state.connection.deferredReconciliation = false;
      state.connection.deferredReconciliationReason = null;
      renderReconciliation();
      renderStatus();
      updateControls();
    }

    async function fetchRemoteSnapshot() {
      const [status, config] = await Promise.all([
        request("/status"),
        request("/config"),
      ]);
      return {status, config};
    }

    async function beginReconciliation(reason = "remote-change") {
      if (reconciliationPromise) return reconciliationPromise;
      const explicitConflict = reason === "revision-conflict";
      if (!explicitConflict && state.ui.submitting) {
        deferReconciliation(reason);
        return false;
      }
      if (
        !explicitConflict
        && state.connection.lastAutomaticReconciliationEpoch
          === state.connection.epoch
      ) {
        return false;
      }
      if (!explicitConflict) {
        state.connection.lastAutomaticReconciliationEpoch =
          state.connection.epoch;
      }
      const draftValues = state.runtime.configuration === null
        ? null : readDraftValues();
      const hadDraft = draftValues !== null && !editableValuesEqual(
        draftValues,
        state.draft.baseline,
      );
      if (draftValues !== null) {
        state.draft.values = draftValues;
        state.draft.dirty = hadDraft;
        renderConfigurationState();
      }
      state.connection.reconciliationState = "fetching";
      updateControls();
      reconciliationPromise = (async () => {
        try {
          const remote = await fetchRemoteSnapshot();
          const changed = !remoteMatchesAccepted(state, remote);
          if (!hadDraft || state.runtime.configuration === null) {
            acceptRemoteSnapshot(remote, {fill: true});
            return;
          }
          if (!changed) {
            acceptRemoteSnapshot(remote, {
              fill: false,
              preservedDraft: draftValues,
            });
            return;
          }
          const fingerprint = remoteFingerprint(remote);
          const warningAlreadyShown = (
            state.connection.warnedRemoteFingerprint === fingerprint
          );
          state.connection.pendingRemoteSnapshot = remote;
          state.connection.reconciliationState = "pending";
          latestStatus = remote.status;
          renderStatus(remote.status);
          if (!warningAlreadyShown) {
            state.connection.warnedRemoteFingerprint = fingerprint;
            renderReconciliation();
          }
          updateControls();
        } catch (error) {
          if (!(error instanceof DaemonUnavailableError)) {
            showBackgroundMessage("Configuration reconciliation failed.");
          }
          if (state.connection.reachable === true) {
            state.connection.reconciliationState = "idle";
          }
          updateControls();
        } finally {
          reconciliationPromise = null;
        }
      })();
      return reconciliationPromise;
    }

    function statusNeedsReconciliation(status = latestStatus) {
      if (!status || state.runtime.activeRevision === null) return true;
      return (
        status.active_revision !== state.runtime.activeRevision
        || status.saved_revision !== state.runtime.savedRevision
        || Boolean(status.dirty) !== state.runtime.dirty
        || status.active_profile !== state.runtime.activeProfileName
      );
    }

    function statusHasNewerRemoteState(status = latestStatus) {
      if (!statusNeedsReconciliation(status) || !status) return false;
      const compareRevision = (remote, accepted) => {
        if (remote === accepted) return 0;
        if (accepted === null) return remote === null ? 0 : 1;
        if (remote === null) return -1;
        return remote > accepted ? 1 : -1;
      };
      const comparisons = [
        compareRevision(status.active_revision, state.runtime.activeRevision),
        compareRevision(status.saved_revision, state.runtime.savedRevision),
      ];
      if (comparisons.includes(1)) return true;
      if (comparisons.includes(-1)) return false;
      return true;
    }

    async function processDeferredReconciliation(mutationSucceeded) {
      if (!state.connection.deferredReconciliation) return;
      const reason = state.connection.deferredReconciliationReason;
      state.connection.deferredReconciliation = false;
      state.connection.deferredReconciliationReason = null;
      if (
        state.connection.reachable !== true
        || state.connection.reconciliationState !== "idle"
      ) {
        return;
      }
      if (reason === "reconnect") {
        await beginReconciliation("reconnect");
        return;
      }
      if (
        mutationSucceeded
          ? statusHasNewerRemoteState()
          : statusNeedsReconciliation()
      ) {
        await beginReconciliation("deferred-status-change");
      }
    }

    function keepLocalDraft() {
      const pending = state.connection.pendingRemoteSnapshot;
      if (
        !pending
        || pending.config.legacy_editor_supported === false
        || pending.config.configuration === null
      ) return;
      const preservedDraft = readDraftValues();
      const previousProfile = state.runtime.configuration.active_profile;
      const targetProfile = pending.config.configuration.active_profile;
      acceptRemoteSnapshot(pending, {
        fill: false,
        preservedDraft,
      });
      const suffix = previousProfile !== targetProfile
        ? ` The draft now targets profile ${targetProfile}.`
        : "";
      showActionMessage(`Local draft kept.${suffix}`, "success");
    }

    function reloadPendingAppliedSettings() {
      const pending = state.connection.pendingRemoteSnapshot;
      if (!pending) return;
      acceptRemoteSnapshot(pending, {fill: true});
      showActionMessage(
        state.runtime.dirty
          ? "Applied settings reloaded; they still differ from the saved configuration."
          : "Applied settings reloaded.",
        "success",
      );
    }

    async function applyProfile() {
      if (!validateForm()) return false;
      const name = state.runtime.configuration.active_profile;
      const profile = profileFromDraft(activeProfile());
      const result = await request("/apply", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          profile_name: name,
          profile,
          expected_revision: state.runtime.activeRevision,
        }),
      });
      applySuccessfulResponse(state, result);
      fillForm(result.profile);
      clearFieldErrors();
      showActionMessage("Changes applied for this session.", "success");
      renderConfigurationState();
      renderStatus();
      return true;
    }

    async function saveConfiguration() {
      if (!validateForm()) return false;
      const name = state.runtime.configuration.active_profile;
      const candidate = deepClone(state.runtime.configuration);
      candidate.profiles[name] = profileFromDraft(activeProfile());
      const result = await request("/config", {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          configuration: candidate,
          expected_active_revision: state.runtime.activeRevision,
          expected_saved_revision: state.runtime.savedRevision,
          apply: true,
        }),
      });
      saveSuccessfulResponse(state, result);
      fillForm(activeProfile());
      clearFieldErrors();
      showActionMessage(
        result.warning || "Configuration saved and applied.",
        result.warning ? "warning" : "success",
      );
      renderConfigurationState();
      renderStatus();
      return true;
    }

    async function reloadAppliedSettings() {
      const config = await request("/config");
      acceptRemoteSnapshot({config, status: latestStatus}, {fill: true});
      showActionMessage(
        config.dirty
          ? "Applied settings reloaded; they still differ from the saved configuration."
          : "Applied settings reloaded.",
        "success",
      );
    }

    async function runAction(action) {
      if (
        state.ui.submitting
        || state.connection.reachable !== true
        || state.runtime.configuration === null
        || state.connection.reconciliationState !== "idle"
      ) return false;
      state.ui.submitting = true;
      state.ui.actionError = null;
      showActionMessage("");
      updateControls();
      let mutationSucceeded = false;
      try {
        const result = await action();
        mutationSucceeded = result === true;
        return result;
      } catch (error) {
        if (error instanceof DaemonUnavailableError) return false;
        if (error instanceof ApiRequestError && error.status === 409) {
          showActionMessage(
            "The applied configuration changed while you were editing.",
            "error",
          );
          state.connection.deferredReconciliation = false;
          state.connection.deferredReconciliationReason = null;
          await beginReconciliation("revision-conflict");
          return false;
        }
        const isApiError = error instanceof ApiRequestError;
        const unknown = isApiError ? displayStructuredFieldErrors(error) : [];
        const suffix = unknown.length ? ` ${unknown.join(" ")}` : "";
        showActionMessage(
          `${isApiError
            ? error.message
            : "The action could not be completed."}${suffix}`,
          "error",
        );
        return false;
      } finally {
        state.ui.submitting = false;
        updateControls();
        await processDeferredReconciliation(mutationSucceeded);
      }
    }

    function renderPreviewRegions(regions) {
      const fingerprint = regionFingerprint(regions);
      if (fingerprint === renderedRegionFingerprint) return;
      renderRegions(documentObject, elements.regionLabels, regions);
      renderedRegionFingerprint = fingerprint;
    }

    async function refreshPreview() {
      const preview = await request("/preview");
      const normalized = normalizeRegions(
        preview.regions,
        preview.width,
        preview.height,
      );
      const viewerRegions = placeRegionsForViewer(
        normalized.regions,
        preview.width,
      );
      const binary = decodeBase64(preview.framebuffer_base64);
      const image = elements.context.createImageData(
        preview.width,
        preview.height,
      );
      copyFramebufferForViewer(
        binary,
        image,
        preview.width,
        preview.height,
        viewerRegions,
      );
      elements.context.putImageData(image, 0, 0);
      elements.previewRevision.textContent = `Revision ${preview.active_revision}`;
      latestPreviewTimestamp = Number.isFinite(preview.generation_timestamp)
        ? preview.generation_timestamp : null;
      renderPreviewRegions(viewerRegions);
      renderStatus();
      return preview;
    }

    async function refreshStatus() {
      const status = await request("/status");
      latestStatus = status;
      renderStatus(status);
      if (
        state.connection.reconciliationState === "idle"
        && statusNeedsReconciliation(status)
      ) {
        if (state.ui.submitting) {
          deferReconciliation(
            state.runtime.configuration === null
              ? "initial-retry" : "status-change"
          );
        } else {
          await beginReconciliation(
            state.runtime.configuration === null
              ? "initial-retry" : "status-change"
          );
        }
      }
      return status;
    }

    async function pollStatus() {
      if (!polling) return;
      try {
        await refreshStatus();
        showBackgroundMessage("");
      } catch (error) {
        if (!(error instanceof DaemonUnavailableError)) {
          showBackgroundMessage("Status could not be refreshed.");
        }
      } finally {
        if (polling) statusTimer = setTimeoutFunction(pollStatus, 2000);
      }
    }

    async function pollPreview() {
      if (!polling) return;
      try {
        await refreshPreview();
      } catch (error) {
        if (!(error instanceof DaemonUnavailableError)) {
          showBackgroundMessage("Preview could not be refreshed.");
        }
      } finally {
        if (polling) previewTimer = setTimeoutFunction(pollPreview, 1000);
      }
    }

    async function initialize() {
      renderConfigurationState();
      const fallbackViewerRegions = placeRegionsForViewer(
        FALLBACK_REGIONS,
        90,
      );
      renderRegions(
        documentObject,
        elements.regionLabels,
        fallbackViewerRegions,
      );
      renderedRegionFingerprint = regionFingerprint(fallbackViewerRegions);
      updateControls();
      try {
        const remote = await fetchRemoteSnapshot();
        acceptRemoteSnapshot(remote, {fill: true});
      } catch (error) {
        if (!(error instanceof DaemonUnavailableError)) {
          showBackgroundMessage("Configuration could not be loaded.");
        }
      } finally {
        void pollStatus();
        if (previewPollingEnabled) void pollPreview();
      }
    }

    function bindEvents() {
      for (const field of EDITABLE_FIELDS) {
        const eventName = field === "brightness" ? "change" : "input";
        controls[field].addEventListener(eventName, () => {
          clearFieldErrors(field);
          updateDraftFromForm();
        });
      }
      elements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        void runAction(applyProfile);
      });
      elements.saveButton.addEventListener(
        "click",
        () => void runAction(saveConfiguration),
      );
      elements.defaultsButton.addEventListener("click", () => {
        const defaultProfile = defaults.profiles[defaults.active_profile];
        fillForm(defaultProfile);
        clearFieldErrors();
        updateDraftFromForm();
        showActionMessage(
          "Built-in defaults loaded into the form. Apply or Save to use them.",
        );
      });
      elements.reloadButton.addEventListener(
        "click",
        () => void runAction(reloadAppliedSettings),
      );
      elements.keepDraftButton.addEventListener("click", keepLocalDraft);
      elements.reloadReconciliationButton.addEventListener(
        "click",
        reloadPendingAppliedSettings,
      );
      if (windowObject) {
        windowObject.addEventListener("beforeunload", stopPolling);
      }
    }

    function stopPolling() {
      polling = false;
      clearTimeoutFunction(statusTimer);
      clearTimeoutFunction(previewTimer);
    }

    bindEvents();
    return {
      state,
      elements,
      controls,
      initialize,
      stopPolling,
      request,
      validateForm,
      updateDraftFromForm,
      applyProfile,
      saveConfiguration,
      reloadAppliedSettings,
      beginReconciliation,
      keepLocalDraft,
      reloadPendingAppliedSettings,
      refreshStatus,
      refreshPreview,
      runAction,
      acceptRemoteSnapshot,
      readDraftValues,
      renderStatus,
      showActionMessage,
      showBackgroundMessage,
      getLatestStatus: () => latestStatus,
    };
  }

  function bootstrap(dependencies) {
    const controller = createController(dependencies);
    void controller.initialize();
    return controller;
  }

  return Object.freeze({
    API_PREFIX,
    UNAVAILABLE_MESSAGE,
    EDITABLE_FIELDS,
    FALLBACK_REGIONS,
    DaemonUnavailableError,
    ApiRequestError,
    createInitialState,
    stableStringify,
    configurationFingerprint,
    canonicalEditableValues,
    editableProfileProjection,
    editableValuesEqual,
    deriveConfigurationState,
    remoteFingerprint,
    remoteMatchesAccepted,
    applySuccessfulResponse,
    saveSuccessfulResponse,
    normalizeRegions,
    regionFingerprint,
    viewerX,
    placeRegionsForViewer,
    copyFramebufferForViewer,
    renderRegions,
    formatLastUpload,
    mapStructuredFieldErrors,
    createController,
    bootstrap,
  });
});
