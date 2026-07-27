"use strict";

const api = "/api/v1";
const form = document.querySelector("#profile-form");
const message = document.querySelector("#message");
const buttons = [...document.querySelectorAll("button")];
const canvas = document.querySelector("#preview");
const context = canvas.getContext("2d");
let configuration = null;
let defaults = null;
let activeRevision = null;
let savedRevision = null;
let submitting = false;
let polling = true;
let statusTimer = null;
let previewTimer = null;

function showMessage(text, kind = "") {
  message.textContent = text;
  message.className = `message ${kind}`.trim();
}

function setBusy(value) {
  submitting = value;
  buttons.forEach((button) => { button.disabled = value; });
}

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, options);
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.message || "The request failed.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function fillForm(profile) {
  document.querySelector("#scrolling-text").value = profile.scrolling_text;
  document.querySelector("#text-color").value = profile.text_color;
  document.querySelector("#clock-color").value = profile.clock_color;
  document.querySelector("#scroll-speed").value = profile.scroll_speed;
  document.querySelector("#brightness").value =
    profile.brightness === null ? "" : String(profile.brightness);
}

function profileFromForm(base) {
  return {
    ...base,
    scrolling_text: document.querySelector("#scrolling-text").value,
    text_color: document.querySelector("#text-color").value,
    clock_color: document.querySelector("#clock-color").value,
    scroll_speed: Number(document.querySelector("#scroll-speed").value),
    brightness: document.querySelector("#brightness").value === ""
      ? null : Number(document.querySelector("#brightness").value),
  };
}

async function loadConfiguration() {
  const payload = await request("/config");
  configuration = payload.configuration;
  defaults = payload.built_in_default;
  activeRevision = payload.active_revision;
  savedRevision = payload.saved_revision;
  fillForm(configuration.profiles[configuration.active_profile]);
}

async function refreshStatus() {
  try {
    const status = await request("/status");
    document.querySelector("#daemon-state").textContent = status.daemon_state;
    document.querySelector("#device-status").textContent =
      status.device_available ? "Detected" : "Unavailable";
    document.querySelector("#device-path").textContent = status.device_path || "—";
    document.querySelector("#active-profile").textContent = status.active_profile;
    document.querySelector("#rendering-state").textContent = status.rendering_state;
    document.querySelector("#last-upload").textContent =
      status.last_successful_upload_time === null
        ? "Not yet" : status.last_successful_upload_time.toFixed(2);
    document.querySelector("#last-error").textContent =
      status.last_display_error || status.configuration_warning || "None";
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function refreshPreview() {
  try {
    const preview = await request("/preview");
    const binary = atob(preview.framebuffer_base64);
    const image = context.createImageData(preview.width, preview.height);
    for (let source = 0, target = 0; source < binary.length; source += 3) {
      image.data[target++] = binary.charCodeAt(source);
      image.data[target++] = binary.charCodeAt(source + 1);
      image.data[target++] = binary.charCodeAt(source + 2);
      image.data[target++] = 255;
    }
    context.putImageData(image, 0, 0);
    document.querySelector("#preview-revision").textContent =
      `Revision ${preview.active_revision}`;
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function pollStatus() {
  if (!polling) return;
  try {
    await refreshStatus();
  } finally {
    if (polling) statusTimer = setTimeout(pollStatus, 2000);
  }
}

async function pollPreview() {
  if (!polling) return;
  try {
    await refreshPreview();
  } finally {
    if (polling) previewTimer = setTimeout(pollPreview, 1000);
  }
}

async function applyProfile() {
  const name = configuration.active_profile;
  const profile = profileFromForm(configuration.profiles[name]);
  const result = await request("/apply", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      profile_name: name,
      profile,
      expected_revision: activeRevision,
    }),
  });
  configuration.profiles[name] = result.profile;
  activeRevision = result.active_revision;
  fillForm(result.profile);
  showMessage("Changes applied for this session.", "success");
  await refreshPreview();
}

async function saveConfiguration() {
  const name = configuration.active_profile;
  const candidate = JSON.parse(JSON.stringify(configuration));
  candidate.profiles[name] = profileFromForm(configuration.profiles[name]);
  const result = await request("/config", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      configuration: candidate,
      expected_active_revision: activeRevision,
      expected_saved_revision: savedRevision,
      apply: true,
    }),
  });
  configuration = result.configuration;
  activeRevision = result.active_revision;
  savedRevision = result.saved_revision;
  fillForm(configuration.profiles[configuration.active_profile]);
  showMessage(result.warning || "Configuration saved and applied.", "success");
}

async function runAction(action) {
  if (submitting) return;
  setBusy(true);
  showMessage("");
  try {
    await action();
  } catch (error) {
    const suffix = error.status === 409 ? " Reload the page and try again." : "";
    showMessage(`${error.message}${suffix}`, "error");
  } finally {
    setBusy(false);
  }
}

async function revertRuntimeConfiguration() {
  const payload = await request("/config");
  configuration = payload.configuration;
  defaults = payload.built_in_default;
  activeRevision = payload.active_revision;
  savedRevision = payload.saved_revision;
  fillForm(configuration.profiles[configuration.active_profile]);
  showMessage("Current runtime settings loaded.", "success");
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  runAction(applyProfile);
});
document.querySelector("[data-action='save']").addEventListener(
  "click", () => runAction(saveConfiguration)
);
document.querySelector("[data-action='defaults']").addEventListener("click", () => {
  fillForm(defaults.profiles[defaults.active_profile]);
  showMessage("Built-in defaults loaded into the form. Apply or Save to use them.");
});
document.querySelector("[data-action='revert']").addEventListener(
  "click", () => runAction(revertRuntimeConfiguration)
);

loadConfiguration()
  .then(() => {
    pollStatus();
    pollPreview();
  })
  .catch((error) => showMessage(error.message, "error"));

window.addEventListener("beforeunload", () => {
  polling = false;
  clearTimeout(statusTimer);
  clearTimeout(previewTimer);
});
