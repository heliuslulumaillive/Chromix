# Runtime positive oracles

`positive-oracles.json` contains bounded observations from the authorized original
browser study. It does not compute expected values using the new production
helpers. Each retained source file has a SHA-256 provenance record.

- Shard WebGL: 52 captured XOR-1 byte positions for seed 314159, sequence zero,
  opaque 64×64 RGBA8. Each crop read restarts the sequence; PNG export remains native.
- Cloak offset: observed Arial width +40 / height −28 for seed 12345, with the
  original greater-than-50 threshold and unchanged native layout controls.
- Cloak text: eleven Windows reference rows. A positive requires the actual glyph
  family, five native metrics, and a distinguishable change in exposed fields.
  Internal fields absent from JavaScript cannot establish a positive.
- Cloak voices: captured ordered en-US and zh-CN lists. The harness selects one
  of these existing captures using the actual native voice inventory, then tests
  the native intersection and local-voice exclusion. No voice backend is created.
- Cloak audio: three complete native/reference PCM SHA-256 pairs. A different
  native waveform is an ineligible input, not permission to relax the guard.

Font allowlist expectations and source hashes are in
`tools/fingerprint_font_acceptance.mjs`; the separately licensed, original test
font is in `font-allowlist-v1`. Candidate artifacts retain exact browser hashes,
collector hashes, runtime checks and per-strategy positive coverage.
