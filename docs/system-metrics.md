# Live Linux system metrics

NexTuxLinq samples read-only Linux system data for schema-v2 System Metric and
Metric Bar widgets. It remains specific to the Phanteks Matrix900 and never
uses telemetry to alter Creator, HID, protocol, transport, DeviceWorker, or
physical orientation behavior.

The stable metrics are:

- `cpu_usage`, `memory_usage`, `cpu_temperature`, and `uptime`;
- `load_average_1m` and fixed-root `storage_usage`;
- `network_receive_rate` and `network_transmit_rate`;
- `gpu_usage`, `gpu_temperature`, and `gpu_memory_usage`;
- `storage_temperature`.

The provider reads fixed Linux procfs interfaces, root-filesystem statistics,
and generic DRM, thermal, and hwmon sysfs interfaces. It runs no shell command,
subprocess, vendor utility, network request, D-Bus call, or dynamic import.
Configuration and API input cannot supply paths. GPU and storage sensor support
depends on what the running kernel driver exposes; GPU, VRAM, CPU temperature,
GPU temperature, and storage temperature are not guaranteed to be available.
NexTuxLinq does not use `nvidia-smi`, `sensors`, or other vendor tools and does
not claim vendor-specific GPU support.

CPU usage is calculated from consecutive aggregate-counter samples. Network
receive and transmit rates aggregate non-loopback interfaces shared by two
consecutive samples and use their monotonic time delta. New interfaces begin
contributing after their own baseline exists; disappeared interfaces are
discarded. The first CPU and network samples are awaiting a delta. Counter
resets, non-positive time deltas, malformed data, and impossible values become
unavailable without sleeping or affecting another metric.

Memory and storage usage use available-capacity semantics and report a clamped
percentage. Load average is the finite, non-negative one-minute value. Uptime
is `MM:SS` below one hour, `HH:MM` below one day, and `<days>D <hours>H` from
one day onward. Network rates automatically use uppercase `KB/S`, `MB/S`, or
`GB/S`. Temperatures use the font-safe ASCII suffix `C`.

System Metric font selection is snapshot-independent. An explicit font stays
authoritative, while Auto checks the widest configured percentage (`100%`),
the complete `-20C` to `150C` temperature range, the unavailable text, and the
effective label before selecting one concrete font for that region. Precision
is included in every envelope. The deterministic envelopes for intrinsically
unbounded text are `9999.00` for load average, `9999.00GB/S` for network rates,
and `999999D 23H` for uptime (shown here at two-decimal numeric precision).
Values beyond those display ceilings retain the ceiling representation. This
prevents glyph size changes as samples change; alignment may still move with
the current text width. With `overflow=error`, the full envelope must fit,
while Clip and Truncate retain their existing drawing behavior.

Generic GPU selection first prefers one DRM device marked as the primary VGA
device, then one uniquely active device, then the sole discovered card. An
otherwise ambiguous multi-GPU system reports sanitized unavailability. GPU
temperature deterministically prefers associated `edge`, `gpu`, `junction`,
then `hotspot` labels. VRAM usage is exposed only when reliable used and total
counters are both present. Storage temperature deterministically prefers a
valid composite reading from storage-associated hwmon data.

The collapsed Technical details section shows Metric availability as
Available, Awaiting sample, or Unavailable. The read-only diagnostics API uses
only safe reason categories such as `source_not_present`,
`awaiting_initial_sample`, `malformed_source`, `ambiguous_source`,
`unsupported_source`, and `temporary_read_failure`. It never exposes procfs or
sysfs paths, raw labels, PCI identifiers, serials, file contents, or exception
details.

The runtime samples at most once for each scheduled render containing metric
widgets. Every metric region in that frame receives the same immutable
snapshot through `RenderContext`; profiles without metric widgets do not
sample. Both metric widgets retain the existing `second_boundary` scheduler
policy. There is no second scheduler, per-metric task, unbounded history, or
queue. Provider failure leaves the scheduler and configuration intact.

Draft validation, preview, presets, imports, exports, and backup restore never
persist configuration or upload a physical frame. Viewer transformation
remains frontend-only: Rear is left and Front is right, while bars, text,
glyphs, and stored logical coordinates are never mirrored. Fan telemetry and
fan control, including OpenLinkHub control, remain outside this metrics model.
