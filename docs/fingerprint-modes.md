# Windows 155 independent fingerprint modes

This candidate adds opt-in `compatible-v1` strategies derived from the recorded ShardX and CloakBrowser studies. It is based on the Google-services/native-passkey candidate at `90e6d29bf81408820c7bba89caf8bad7dec8430c`. Source reconstruction, helper tests, a compiled candidate, and hardware/runtime acceptance are separate evidence levels.

The deliverable is one kernel containing independently selectable strategies. The user explicitly deferred the Passkey latency prototype at `a493096`; it is not part of this candidate.

The machine-readable contract is [`build/fingerprint-modes-capabilities.json`](../build/fingerprint-modes-capabilities.json). A delivered candidate must also contain hash-bound `modes-native.json`, `modes-runtime.json`, and `fingerprint-modes-validation.json` reports. A capability declaration alone does not mean a browser was built or a physical GPU was tested.

## Launcher and manager boundary

The environment manager UI is not changed here. Existing environment IDs, seeds, cookies, profile directories, proxies, and kernel bindings are not migrated. Selecting a field's native mode does not globally disable other fields. Omitting a new mode retains the previous Chromix branch, defaults, old switches, and seed precedence.

The browser entry point normalizes the following public switches to immutable `uxr-*` configuration before child processes receive the existing configuration snapshot. The old `--fingerprint=off` remains the global opt-out. Use a persisted positive `--fingerprint=<seed>` for a reproducible environment; bare `--fingerprint` retains its existing random-seed behavior.

| Surface | New public switch | Supported choices |
|---|---|---|
| Canvas pixels/export | `--fingerprint-canvas-mode` | `native`, `chromix`, `shardx`, `cloak` |
| WebGL pixel readback | `--fingerprint-webgl-mode` | `native`, `chromix`, `shardx`, `cloak` |
| Font family selection | `--fingerprint-fonts-mode` | `native`, `chromix`, `shardx`, `cloak` |
| Audio readback/selected processing | `--fingerprint-audio-mode` | `native`, `chromix`, `shardx`, `cloak` |
| Text metrics | `--fingerprint-text-mode` | `native`, `chromix`, `shardx`, `cloak` |
| DOM rectangles | `--fingerprint-rects-mode` | `native`, `chromix`, `shardx` |
| Offset dimensions | `--fingerprint-offset-mode` | `native`, `chromix`, `cloak` |
| Acceleration readings | `--fingerprint-sensors-mode` | `native`, `chromix`, `shardx` |
| Media-device enumeration | `--fingerprint-media-mode` | `native`, `chromix`, `shardx` |
| Speech voice inventory | `--fingerprint-voices-mode` | `native`, `chromix`, `cloak` |

Each surface accepts `--fingerprint-<surface>-mode-seed=<uint64>`. New explicit ShardX seeds must be `1..4294967295`; inherited larger legacy seeds use the documented existing fold, with zero mapped to one. The distinct `mode-seed` name avoids reinterpreting the old `canvas-seed`/`audio-seed` flags. Explicit `chromix` selection retains the old per-surface seed precedence. Unsupported modes and orphaned mode-specific options are rejected.

Amounts are supported for `rects`/`text` (`0..16`, default `1`), `sensors` (`0..100`, default `0.1`), and `audio` (`0..1`, default `0`, selecting the measured default precision). Other surface amounts must be zero. These are algorithm parameters, not a universal noise percentage. `false`/`raw` behavior is recorded per surface in the contract; font/profile selection remains independent of pixel noise.

Per-field `--fingerprint-identity-mode`, `--fingerprint-cpu-mode`, `--fingerprint-memory-mode`, and `--fingerprint-screen-mode` accept `native` or `custom`. CPU custom values use the existing `--fingerprint-hardware-concurrency` (`1..128`). Memory uses `--fingerprint-device-memory` (`0.25..32` GiB), rounded consistently to the supported buckets for JavaScript and both Device-Memory Client Hint names. This changes reporting, not the V8 heap or available RAM.

The native identity mode removes that field's UA/UA-CH overrides, while retaining the shared platform setting used by independently selected fonts and GPU profiles. Screen native mode removes screen/work-area/DPR overrides; window placement and viewport settings remain separate.

`--fingerprint-hardware-profile=cloak` selects the recovered metadata catalog using the persisted seed. The catalog has 18 Intel, 41 NVIDIA, and 20 Apple logical entries, with 67 distinct model-name strings. Optional `--fingerprint-hardware-vendor` and `--fingerprint-hardware-gpu-index` choose a specific valid entry. Windows and macOS personas are supported; an unsupported platform/vendor combination fails. Explicit CPU/RAM/WebGL values and per-field native choices win over generated defaults. This catalog is not a physical GPU emulator.

GPU identity and capabilities are separate from pixel noise:

| Control | Supported choices / meaning |
|---|---|
| `--fingerprint-gpu-mode` | `native`, `custom`, `cloak`; default for both API identity fields |
| `--fingerprint-webgl-identity-mode` | Independent identity override |
| `--fingerprint-webgpu-identity-mode` | Independent identity override |
| `--fingerprint-webgl-capabilities-mode` | `native`, `custom` |
| `--fingerprint-webgpu-features-mode` | `native`, `custom`, `cloak` |
| `--fingerprint-webgpu-limits-mode` | `native`, `cloak` |
| `--fingerprint-hardware-platform-version` | Independent GPU profile version, unaffected by native UA selection |

Custom identity requires complete WebGL vendor/renderer and/or WebGPU vendor/architecture pairs for the selected API. WebGL custom mode supports only the five validated `uxr-webgl-max-*` controls listed in the contract: texture size, cube-map texture size, renderbuffer size, 3D texture size, and array texture layers. It clamps the actual validation cache downward. WebGL extensions and WebGPU features are intersections with real support; an empty custom feature list enables no optional features. Cloak WebGPU limits conservatively constrain 36 public limits while real device creation and execution remain authoritative.

ShardX media caps are `--fingerprint-media-audio-inputs`, `--fingerprint-media-audio-outputs`, and `--fingerprint-media-video-inputs`, each `0..32`. Omission leaves that category unrestricted. These cap real enumerated entries, including native aliases; they do not install virtual devices or disable capture through a previously known native ID.

The additional `fingerprint-audio-default-sample-rate` control changes the actual realtime context rendering rate only when the script omits `sampleRate`; explicit AudioContext/OfflineAudioContext/AudioBuffer options retain precedence. `fingerprint-audio-channel-limit` clamps the real destination capacity and setter. `fingerprint-audio-default-channel-count` creates that actual initial output bus or raises `NotSupportedError` when the backend cannot support it. Omission preserves native defaults.

`fingerprint-fonts-allowlist` restricts resolved installed families; `fingerprint-fonts-local-allowlist` further restricts only `local()` and Local Font Access. Both intersect with the chosen font strategy. The UTF-8 CSV contract distinguishes omission from an explicitly empty list, permits up to 256 entries/16 KiB total, and does not install fonts or fabricate aliases. Downloaded web fonts remain separate.

Paired `fingerprint-webgl-masked-vendor/renderer` values are supported with a complete custom WebGL identity. Actual software/fallback contexts keep the standard masked values as well as their real unmasked identity.

## Kernel implementation and deliberate corrections

The original selected Chromix patch series remains intact. Six new overlays are composed after the existing Google/native-passkey restoration:

1. `0001-independent-mode-contract.patch`: immutable configuration, aliases, field-level native behavior, common memory reporting, hardware catalog, and native test target.
2. `0002-graphics.patch`: Canvas/export/readPixels hooks and graphics helper functions.
3. `0003-surfaces.patch`: fonts, text/geometry, audio, acceleration, media enumeration, and voice selection.
4. `0004-gpu-compatible-modes-v1.patch`: actual WebGL capability constraints and WebGPU identity/feature/limit policies.
5. `0005-installed-font-allowlists.patch`: separate general/local installed-font filters, applied incrementally after the surface overlay.
6. `0006-audio-defaults.patch`: real default sample rate, output-channel policy, and construction/sink-change validation.

The shared WebGL file is owned by the graphics overlay; the GPU overlay excludes it to avoid replaying overlapping hunks twice.

| Area | New compatible behavior and difference from the measured original |
|---|---|
| ShardX Canvas | Retains the recovered xoshiro128** selection and low-bit rule, with full-canvas coordinates, consistent crop projection, and alpha guards. |
| ShardX WebGL | Uses a stable sequence for repeated reads; does not reproduce the observed per-call counter drift. PNG remains native for this separate readPixels strategy. |
| Cloak graphics | Coherent logical RGB treatment across supported readback/export. Opaque, complete readbacks are required. The original stored fake-image substitution is deliberately omitted because it could replace different rendered content with the same reference. |
| WebGL fallback | Unsupported formats, pack/PBO/shared-memory paths, user framebuffers, incomplete reads, and unverified alpha conditions retain native data. The hook does not consume the application's GL error state. |
| GPU features and limits | Claims are bounded by the real adapter. Software/fallback adapters retain their actual identity. No fabricated optional feature set, skipped device validation, or restored fake device limits. |
| ShardX fonts | Filters the resolved native family rather than raw caller spelling, closing case/alias inconsistencies while preserving generic and last-resort fallback. |
| Cloak fonts | Uses the recovered Windows 30/138/149/231/232, macOS 562, and Linux 47 candidate arrays against actually available faces. Candidate counts do not imply installed font counts; no missing font binaries or undocumented aliases are synthesized. |
| Cloak text | Reference correction requires the exact text/font key plus compatible persona, resolved faces, shaped glyph families, width, and glyph bounds. Mismatches stay native. A table entry does not prove font-file identity. |
| ShardX audio | Retains the measured bit formulas. Analyser tails stay untouched; copy operations transform only their valid destination range, including overlapping typed-array cases. Existing Chromix graph isolation stays separate. |
| Cloak audio | Reference replacement is limited to exact bitwise matches of three captured mono 44.1 kHz native PCM buffers under a macOS persona. It is not general macOS DSP emulation. Other graphs, rates, channels, changed samples, and the unconfirmed double-destination branch remain native. |
| Geometry/sensors | Recovered ShardX rect/text formulas and Cloak offset rule are separately selected. New sensor Auto defaults to `0.1 m/s²`; explicit zero remains zero. Only real acceleration-family readings are biased; no sensor is invented. |
| Media | Filters actual entries and preserves IDs, permission labels, and capability associations. Cloak's fake empty-list placeholder is omitted; `media=cloak` is rejected instead of silently doing nothing. |
| Voices | Intersects/reorders the actual native voice inventory against recorded names/languages, preserving real URI/backend/default metadata. No nonexistent Google/macOS engine is advertised as installed. |

Unknown original font coverage/alias branches, full DSP graph behavior, and unobserved sensor/media strategies remain explicitly unsupported. New compatible modes are implementations with documented differences, not a claim that every closed-source path has been recovered.

## Cached build and acceptance

The initial build explicitly imports completed checkpoint run `39396862771` at commit `70ef25a442589c4899a8b9fc8fc046df2b932c96`. Both archive volumes and the final manifest must match the pinned identities. The successful baseline candidate run `39425901213` is a runtime artifact, not a substitute for the compiled object checkpoint.

The separate workflow/artifact/checkpoint namespace is `fingerprint-modes-win155`. The existing registered Google workflow has an explicit variant route on this isolated branch; its default remains the original Google flow. New builds do not cancel other runs.

Each stage restores once, preserves unchanged source timestamps, refreshes Ninja metadata only when necessary, and performs a real target dry-run. A successful tested candidate skips full source/object archiving. An unfinished or failed stage commits all checkpoint volumes before its manifest; later stages may resume only a complete checkpoint from this variant.

The native mode target, existing native Passkey regressions, Google/runtime checks, and bounded raw-CDP mode matrix are required before a candidate is delivered. The matrix uses disposable profiles, loopback pages, fake media, and synthetic sensor input. It includes positive selected-strategy/default/allowlist cases, not just native fallback checks. It does not operate production profiles or exercise real third-party accounts. Missing GPU APIs, fonts, native voices, or exact PCM eligibility remain visible as `not_tested`; none establishes a positive acceptance claim for that branch.

Same-runner telemetry records CPU, memory, disk, owned Ninja process activity, and newly appended log output. The task monitor checks the exact run/commit every ten minutes and reports actionable failure, repair, or completion; it does not cancel an active compile or repeatedly dispatch unchanged source.
