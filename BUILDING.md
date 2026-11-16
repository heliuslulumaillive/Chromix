# Windows product builds

## .38 source-controlled country font view candidate

The `codex/native-font-view-win157-20261116` backport keeps explicit native mode
unchanged. Country auto mode retains the reviewed 143 system sources and
registered supported Terminal shared-font variants; only the CN-specific group
includes the eight Fonts.Hans sources. Other host application fonts are excluded
from this process-private view. No Windows fonts are installed or removed.
Optional resource verification or DirectWrite loading failures retry only the
verified selected native view. Required-source mismatches reject controlled
startup; the launcher preflight reports compatibility errors before launch.

The exact upgrade donor is accepted .38 run `43767199807`, stage 1 / `96173fd`.
Only the private font helper and its common DirectWrite entry point change in
compiled source. The reviewed transition and target identities are in
`build/native-font-view-upgrade-plan.json`. Local native/source tests do not
enroll a DLL. Actual hosted output, ordinary-start and font-interface acceptance,
signed packaging and temporary deployment remain pending. The independent .78
build is unchanged.

The country-coverage expansion on `codex/country-font-coverage-win157-20261115`
adds 250 CLDR-derived country/territory entries sharing 25 application supplement
combinations. All retain the native-complete font view. Pinned open fonts are
shared by content hash; the complete store is about 183 MiB. Local compiled
DirectWrite checks passed for all 25 combinations, including native retention,
real local files and read-only handles. The registered hosted upgrade changes
only `base/uxr_private_font_packages_win.h` from run `43675068974` stage 1.
Actual rebuilt-DLL browser acceptance and DLL admission remain pending. These
are language coverage choices, not captured Windows country installations.

The additive country-resource candidate is built by `43675068974` from
`3324ab1c76831cabbebd08172c40d1318dc35d19`; DLL SHA-256 is
`147c98d2c0d03293239677f2a46ace142f13d635ac6ec3f0f5b75bf9973a1941`.
It merges the original native collection's own font set with only one pinned
regional package, rather than importing compatibility/remote-backed faces from
GetSystemFontSet. Broker checks remain unchanged. Actual Font Access bytes,
Window/iframe/Worker aliases and metrics, content-bound regular/bold CSS,
repeated choice, missing/unknown-group fallback and explicit seed variation
passed. The manager candidate defaults to auto supplement selection per launch;
US is the native-complete base with no additional assets. This is additive
resource loading, not admission of complete country installation profiles.
The 12 public headed-CDP observations had Rare=false (one explicit perturbation
was p99-p99.5), but Bot/DevTools were detected and demo raw attributes did not
reflect the added fonts. Formal kernel installation/bindings remain unchanged.
Local receipts: `G:/Shardxlite/tests/country-font-browser-20261114/report.html`.

The `font-installation-profiles-win157-20261114` branch supersedes the engineered
package-combination default proposal. JP/KR/US are selection entries, not font
inventories. `windows-font-installations-v3.json` has no verified defaults until
complete versioned installation captures, actual-DLL interface evidence,
save/reopen stability and Rare observations are admitted. Catalogue unions,
downloaded resource existence and earlier engineering-mask scores are not
installation evidence. Shared language fonts remain present when installed.
The former private-font loader remains an opt-in engine experiment; additional
assets are not loaded or bundled merely to increase configuration count.

Default Windows font visibility is native-complete even if an old saved caller
requests ShardX font mode. Seeded package masks require the explicit legacy
`uxr-fonts-experimental-packages=1` switch and remain default-ineligible. Potential
system Noto CJK HK/JP/KR/SC/TC families and their actual aliases are preserved,
without asserting their presence on a given installation. Office catalogue
membership is not used to admit an Office-present/absent country profile.

The v3 selector is a launcher-side candidate module, not a change to the shipped
desktop manager. `tools/font_profile_selection_cli.mjs --action list --country
JP --registry <catalogue> --host <normalized-host-facts> --evidence <evidence-root>
--dll <actual-dll-sha256>` lists compatible admitted installations; `--action
select --directory <isolated-profile> --seed <stable-u64>` saves one immutable
choice. KR/US use the same explicit country entry. Empty pools fail closed.
Selection currently supports complete native-visible installations with exact
host inventory compatibility; it does not provision another Windows image or
invent application-present/absent variants. A future non-native configuration
requires real installation evidence and a separately implemented, validated
visibility plan before admission.

`capture_windows_font_environment.py --output <new-folder>` captures raw OS,
update, language, protected-file and actual Office-file facts without installing
fonts. `capture_browser_font_installation.mjs` records the actual browser-visible
inventory and interface observations, explicitly marking automated collection.
These raw observations are not automatically promoted to complete installation
evidence: unresolved component ownership, missing application state, unsupported
interfaces and alias-loading failures remain gaps. Country defaults require
reviewed, pinned installation, native-interface, persistence and matching Rare
receipts. Web receipts must match the profile, full inventory and configuration;
a result for another configuration cannot authorize admission. Tests use
synthetic receipts only and never populate the shipped registry.

Actions `43630999220` compiled source `8bcffed` into DLL
`79bda4bc78722481f4be2e14dd2b09ffa8f95954ced20313a1dc5bc4a241b4b5`.
All 48 runtime scenes / 1,704 executed checks passed. The final receipt reader
rejected the 682,365-byte formatted summary at its unchanged 512 KiB limit.
The summary writer now emits compact JSON and rejects oversized UTF-8 output
before writing; no fields, unavailable conditions or acceptance checks are
removed. The same actual DLL and unchanged native/runtime/capability receipts
passed strict local receipt validation after lossless summary compaction
(516,849 bytes). Two regression tests cover full-data roundtrip and rejection
of UTF-8 output above the original bound. This is a report-format repair, not
a C++ change, a new DLL admission or a retrospective CI success claim; the
original hosted result and diagnostic artifact remain unchanged.

Replay `43643931998` / `42bc115` subsequently completed successfully and delivered
the accepted CI runtime. All 686 declared runtime files, including the DLL,
executable, ICU, paks and contracts, are byte-identical to the measured runtime;
the existing host observations therefore remain bound to the delivered bytes.
The six headed-automation Rare controls (two common screen/DPR configurations,
native and explicit font seeds, plus a repeated baseline) all returned
`false / <p95`. Font-only comparisons kept the same exit IP, complete visible
inventory and SDK raw device attributes. These are six observations of the same
complete host font inventory, not six validated random installations. JP/KR/US
default pools remain empty; the formal r69 kernel, saved bindings and DLL
admission identities remain unchanged.

The actual DLL also passed 120 host-side aggregate comparisons over six
captures (native / seeds 9918 and 14666, each reopened), covering complete
visible records and font bytes, system aliases, Window/Worker/iframe, CSS and
rendered output against the independently captured r69 native baseline.
This Chinese-host result is not JP/KR/US configuration admission. Verified
Microsoft 26H2 media snapshots are separate experimental source evidence,
not post-OOBE installations. Full local evidence and subsequent Rare controls:
`G:/Shardxlite/tests/font-installation-profiles-20261114/report.html`.

The current Windows x64 product targets Chromium **157.0.8103.38**, ungoogled core
`de09e0c70c6bc1ac83d4ecdfd0e82c7e250c9a3c`, and Windows packaging
`f85bc55429c37b1c748b676b18fe79b38eee3207`. The 26 reviewed topics and exact source
identities are in `build/lite-windows157.json`; port details are in
`build/windows157-port.json`. Historical 156 inputs remain available for review.

Use the GitHub-hosted `security-win157.yml` workflow for complete kernel builds.
Its `upstream` operation admits the pinned, completed x64 run `42046998774` and
artifact `13560057174` before downloading. Source and object files are restored
once; unchanged preparation preserves timestamps. A `resume` donor must belong
to the same exact 157 identity and recipe. A 156 checkpoint cannot authorize a
157 build. The previously registered `security-win156.yml` dispatch entry remains
available on this branch and calls the 157 worker; only the 157 entry builds on
main pushes.

Run `42488966846` compiled the ICU locale repair and retained a complete stage-1
checkpoint after three pruned updater type libraries failed GUID parsing. The
current explicit `upgrade` transition uses this source stage `1`, restores the
omitted native security-sync metadata and requires its compiled regression. The
exact-157 MIDL wrapper generates empty TLBs with native MIDL and invalidates only
declared generated MIDL outputs once. Its two reviewed original forms normalize
one documentation URL so product domain restoration and warm verification agree.
Other sources and object state retain
their timestamps; ordinary resume still refuses changed recipes. No runtime or
DLL acceptance is inherited.

The Windows SDK directory version is **10.0.28000.0**, using Microsoft's servicing
release **10.0.28000.2705**. `build/windows/ensure-windows-sdk.ps1` verifies the
required headers, libraries and tools before large transfers. Installer identity
and download hashes remain pinned. Metagen checks the exact V8 revision
`440c1c463e2e528c309af5067952e7b11631507f` and the LLVM profile of the actual Ninja
action. The workflow also pins Python, Node and Go and checks the private desktop
before acquiring the source cache.

An accepted candidate requires compiled native tests, runtime checks, matching
source/build receipts and an unchanged archive inventory. DLL admission and
installation follow acceptance. The sign-in changes recover explicit failures
and apply the requested native history/tab-sync default after account and policy
checks; Google authorization remains a service-side requirement.

The document WebRTC candidate keeps the native non-proxied-UDP restriction and
uses a per-ExecutionContext memory transport for internal peers. The hosted
verification plan requires `webrtc-document-runtime.json`: data and PCM content,
video, candidate pooling, relay/all transitions, document/profile isolation,
navigation revocation, literal/hostname TURN/TCP, failed-proxy and reachable
UDP-canary controls. WebRTC names are not pre-resolved locally; TURN/TCP uses
the native proxy-capable hostname fallback, with unresolved names excluded from
the virtual port namespace. The fixture retains managed DNS/proxy flags and
allows only its own HTTP port to bypass the proxy. Local packet
acceptance with the configured proxy remains separate from this bounded CI
fixture. No DLL admission or installation is implied by a source change.

The Windows font-cohort candidate retains the documented Windows base fonts and
selects four stable installed-font cohorts from the existing seed: optional
language packs and application fonts are each kept or hidden as a class. It
never invents absent fonts or changes inventory with the proxy country. The
registered transition from run `43149750998` changes only the shared policy,
its compiled regression, and the Windows font-cache call site. The expanded
browser acceptance requires core retention, actual optional-font masking across
the cohorts, consistent Window/Worker/CSS/enumeration results, and unchanged
downloaded fonts. Source tests do not imply a rebuilt or admitted DLL.

Candidate `da5de28`, built by Actions `43459314622`, produced DLL SHA-256
`3bdd3a063f6359b8594feefec9161a49ac1b38d5364ea266788150cba683ebec`.
On 2026-11-14 its 34 local runtime scenes passed 2,966 checks, including 200
positive font checks; the desktop manual path passed 431 and the packaged
bridge passed 1,251. Four font cohorts remained observable and all returned
`rare=false` in the public demo, with one still in `p99.5-p99.9`. The same old
font vector changed from `true/not_seen` on Nov 13 to `false/p95-p99` on Nov 14;
these results do not establish permanent or production-model rarity behavior.
The desktop source admits only this exact DLL and the previously verified r69
identity. The isolated accepted package has not replaced the formal install.
Local evidence: `G:/Shardxlite/tests/font-cohorts-20261113/report.html`.

The superseded isolated application-package experiment on
`codex/font-pack-cohorts-win157-20261114` preserves the complete published Windows
base/FOD name protection and known family, full-name, PostScript and truncated GDI
aliases. Its twelve seed-stable slots are engineered masks of Office catalogue
entries, Noto and Liberation, not twelve captured installations. Unknown fonts
remain visible, so its old `system-only` label did not mean a clean Windows image.
Missing packages can make slots identical on a particular host. These slots are
default-ineligible in v3. Source/compiled host tests are
separate from the pending hosted DLL build and subsequent local/Rare acceptance;
no formal kernel binding or DLL admission is changed by this branch.

That experimental branch treats the audited OS-owned Noto SC variable fonts and
Nirmala TTC as system fonts. Additional Noto JP and Liberation files are pinned
by `build/private-font-packages.json`, carry their SIL licenses, and are loaded
only into an opt-in DirectWrite collection with
`--uxr-fonts-resources-dir=<bundle>/private-fonts`. No Windows font registration
is performed. The native host test confirms real regular/bold faces, unchanged
global system collection counts and rejection of altered resources. Browser
acceptance additionally requires the private families in Window, Worker,
Font Access and actual CSS glyph selection; missing assets cannot be skipped
into acceptance. The current DLL build and Rare sampling remain separate steps.
