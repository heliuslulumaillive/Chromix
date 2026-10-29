# Chromium 155 surface semantics candidate

This candidate starts at the accepted `75599a0755ebeb1f57129d2a118c0d1f9ed2e636`
source. It preserves the 231 selected Windows patches and 11 restoration overlays,
including native passkey discovery, the Google AppID restoration, native Windows
voice compatibility, physical GPU binding and the existing WebGL V2 fixes. It does
not activate a browser, change a profile binding, migrate saved seeds or add UI
controls.

## Included changes

`0011-pixel-semantic-preservation.patch` adds a coordinate-anchored flat-cell
guard to the selected ShardX Canvas/WebGL paths. Crop reads obtain a one-pixel
halo from the same image or framebuffer. Export has full-image context; WebGL
row orientation, premultiplication, float paths, PACK state and PBO ownership
remain explicit. The guard ignores only bits the noise can assign, so repeated
readback and export do not change eligibility through earlier assignments.

The production ShardX Canvas path uses a separate seeded coordinate stream.
This avoids drawing all candidates from a large blank background that the new
guard correctly excludes. Historical reference/golden behavior remains available
to its existing callers. Noise is sparse, limited to an RGB low bit, and does not
change alpha. Density is not identical to the previous fixed 32-candidate rule:
the independent text and sparse corpus changed fewer pixels, while a larger
gradient changed more pixels (still below 0.22% in the recorded sample). This is
an implementation tradeoff, not evidence of a better detector score.

The extra halo has a runtime memory/readback cost that must be measured on the
candidate. If its allocation or completed read fails, the current design keeps
the native readback rather than fabricating neighboring pixels. That failure
path can expose unperturbed pixels; it is not a fail-closed privacy guarantee.
Context loss has a separate lifetime guard: the DrawingBuffer/transport stays
owned through state restoration, and a lost context never receives PBO writeback.

The scope is **flat-region preservation**, not general script-write provenance.
Complex `putImageData` arrays are still eligible. The original Shard 156 build 3
also modified such arrays in controlled reference observations. No URL, detector
name, image hash or special canvas-size exception is used.

`0014-headless-screen-synchronization.patch` translates a complete explicit
virtual display plus DPR into Chromium 155's native `--screen-info` geometry.
It runs in the browser process, after Native/global-off normalization, only for
headless launches. Explicit native screen-info or force-device-scale-factor
switches retain precedence; headed launches remain unchanged. Chromium 155's
native screen-info parser accepts DPR 0.5–8 in this integration; the previous
virtual-only 0.25 behavior is not broadened. Fractional scales retain normal
physical-pixel rounding. An existing 155 binary was used to confirm that the
native screen-info path produces actual DPR 2/1.25 rastering before writing this
overlay.

## Deferred changes

Audio source preservation is not included. A first producer-boundary trial passed
its local method tests but changed a transparent BufferSource render. A stricter
45-condition probe passed on original Shard 156 and failed 8 conditions on the
existing 155 ShardX mode. Correct source tracking must account for channels,
ranges, copy/mix paths, render quantum reuse and script/Worklet outputs. The trial
was removed from the candidate rather than treated as an accepted repair.

Text/Rects changes are not included. Rounding only a scalar getter or guarding
one CSS width does not cover Range, ResizeObserver, transforms, wrapped fragments,
TextMetrics ink boxes, selection rectangles, clusters and caret queries. These
need a shared metric source and a coherent test matrix. Existing Native/legacy,
ShardX and other selected behavior is retained for this build.

## Acceptance boundaries

The candidate retains existing native passkey and voice execution gates. New
pixel native properties must execute with a nonempty independently captured
corpus. The corpus is hashed into the build recipe. Browser acceptance additionally
requires flat regions, rendered positive perturbation, repetition/crop/export
consistency and headless raster scale observations. Missing data or a skipped
path is not a pass. A changed DLL cannot inherit an older runtime acceptance.

The baseline's WebGL fixes already cover stable coordinates, repeated reads,
cross-API transport and canonical color endpoints. This candidate extends that
work; it is not a claim that those fixes were absent or that every aspect of the
closed Shard engine is reproduced.

## Detector score interpretation

A website score is a separate observation. It may aggregate browser, network,
automation, reputation and prior-visit signals, and some numbers describe
identification confidence rather than authenticity. Semantic improvements do not
promise a higher displayed score. Equal scores do not establish equal behavior.

The useful comparison holds engine version, actual GPU/driver, public identity,
proxy/exit, locale, screen, profile history and automation attachment conditions
constant. Record completion failures, individual signals, repeated cold samples,
same-profile restarts, per-seed differences and runtime cost. Do not selectively
retain favorable runs or optimize code for a named test site.

The current source/helper checks support proceeding to compilation, not claiming
GPU/runtime or public-site acceptance. Those require the compiled candidate and
new hash-bound records before any production activation.

Relevant public references:

- HTML Canvas bitmap and pixel manipulation semantics:
  <https://html.spec.whatwg.org/multipage/canvas.html#pixel-manipulation>
- Fingerprint's description of its multiple Smart Signals and aggregate score:
  <https://fingerprint.com/products/smart-signals/>
- Web Audio data access and copying contracts:
  <https://www.w3.org/TR/webaudio-1.1/>
