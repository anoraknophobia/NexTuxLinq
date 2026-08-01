# Live Linux system metrics

NexTuxLinq samples read-only Linux system data for schema-v2 System Metric and
Metric Bar widgets. It remains specific to the Phanteks Matrix900 and never
uses these values to alter Creator, HID, protocol, transport, DeviceWorker, or
physical orientation behavior.

The stable metric identifiers are `cpu_usage`, `memory_usage`,
`cpu_temperature`, and `uptime`. CPU and memory usage default to 0–100%; CPU
temperature defaults to 0–100C and may be unavailable on systems without a
suitable generic thermal source. GPU metrics are intentionally not supported.

The Linux provider reads only the fixed procfs CPU, memory, and uptime
interfaces and generic thermal/hwmon discovery roots. It accepts no paths,
commands, expressions, format strings, imports, network services, or shell
execution from configuration or API input. Source failures become sanitized
availability states and never expose source paths, sensor names, file content,
or exceptions.

CPU usage is calculated from consecutive aggregate counter deltas. The first
sample therefore reports `awaiting_initial_cpu_delta`; no sleep is introduced
to manufacture a delta. Invalid, reset, zero, or impossible deltas are
unavailable. Memory uses total and available-memory semantics. Uptime is
formatted as `MM:SS` under an hour, `HH:MM` under a day, and
`<days>D <hours>H` at one day or more, with a two-digit hour. Temperature
accepts plausible Celsius or millidegree-Celsius values
and deterministically prefers generic CPU, package, core, Tctl, Tdie, or SoC
labels while ignoring clearly unrelated sensors.

The runtime samples at most once for a scheduled render of a profile containing
metric widgets. Every metric region in that frame receives the same immutable
snapshot through `RenderContext`. Profiles without metric widgets do not
sample. Both widgets use the existing `second_boundary` scheduler policy; no
second scheduler, per-region task, history, or queue is created. Provider
failure leaves the configuration and scheduler alive and renders documented
unavailable states.

Draft validation, preview, preset selection, import, export, and backup restore
do not persist configuration or upload a physical frame. Viewer transformation
remains frontend-only: Rear is left, Front is right, while bar fill, text,
glyphs, and stored logical coordinates are never mirrored.
