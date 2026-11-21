# Windows product builds

## Chromium 158.0.8127.36 for Windows x64

This branch ports the Lite 157 slim V2 features from `a60db89` onto the original Chromium source archive. The [download pin](build/chromium158-source.ini) binds the complete release archive. The [base policy](build/chromium158-base.json) selects the required ungoogled core and Windows patch sections, and keeps the three native binary resources plus four preferences source files during pruning. The [158 product manifest](build/lite-windows158.json) lists the remaining product changes and their verified source identities.

Use the project's hosted Windows workflow with `operation=fresh` for the first 158 build. The existing dispatch entry `security-win156.yml` calls the 158 worker on this branch. Use `resume` with a matching native 158 checkpoint to continue a previous build. The native browser, Cookie, session, security, graphics, font and Windows Passkey suites still run against the produced artifact before DLL admission.

## Historical 157.0.8103.78 slim source candidate

The installed source baseline is `230b7f6` (run `44470833537`, stage 1). The slim target removes the standalone Chromix and Cloak surface modes while retaining the physical GPU and hardware catalog contracts. The Windows product uses its normal proxy broker for authenticated SOCKS5. It uses the native component path for DRM.

Version 2 uses the signed DirectWrite catalog or the native Windows collection for font lookups. It retires font-name masks and unsigned resource directories. Windows speech uses its native voice names and backends. Chromium chooses the default site quota and explicit overrides are supported. Multiline bounds share a rectangle policy, including correct zero-size results for detached nodes.

The 157 worktree used `operation=upgrade`, `source_run=44470833537`, and `source_stage=1`. Its migration bound both source trees and each source replacement.

## 157.0.8103.78 / signed source-controlled catalog v3 (candidate)

This candidate combines the external catalog loader with the source boundary
verified by the .38 backport (`ca743abb`, actual DLL `cb9dee64300a6eb379cbe4e64d0942a448f427bebe74b2510d638e889228c586`).
The .78 implementation compiles only the public trust anchor and bounded
protocol. Country maps, common native source pins, supported registered shared
package variants, regional native sources and open-font additions stay in
external signed data. Editing that data does not trigger a kernel build.

The current reviewed data has 250 country/territory entries and 25 groups.
The 143 common Windows sources and supported registered Terminal sources are
retained; only CN selects the eight Fonts.Hans sources. Windows-provided
SimSun/YaHei and other verified shared Asian foundations remain common. Other
host app/custom sources are excluded from automatic mode; explicit native mode
continues to use the complete original collection. Windows fonts are read from
legitimate installed sources and are not redistributed or installed by this tool.
The present source profile is the reviewed Windows 11 25H2 x64 26200.9974 set;
other OS/source versions need reviewed data variants and actual acceptance.

Publish a data-only update with the existing current-user non-exportable CNG
signer (historical key name `ShardX Lite Font Catalog v2`, key identity unchanged):

```
python -m tools.font_catalog_v3 --manifest build/controlled-external-font-resources.json --store <absolute-font-root> --version <release-id> --public-key <absolute-font-catalog-key.json> --powershell <absolute-powershell.exe> --activate
```

Every selected native source and resource is independently authenticated by the
kernel; real named faces, axes, styles and read-only lifetime are preserved.
Unknown countries use the valid catalog's US group. Unknown raw groups retain only its authenticated controlled native sources, without optional additions. Missing or corrupt optional
additions retain only the selected controlled native view. A missing/invalid
catalog or required native source mismatch rejects controlled
startup, never exposes the complete host collection. The launcher preflight
provides compatibility errors before normal browser launch. Each running
browser keeps its own immutable signed generation; publication affects later
launches only. Key rotation, protocol or native loader changes still require a
kernel build, whereas country/font/source data changes within this protocol do
not. Old signed generations support explicit rollback.

The original .78 run `43775379956` / `d9727f1` retained a resume-safe stage-4
checkpoint after an HTTP 429 during metagen bindings provisioning; it did not
deliver an accepted DLL. Completed work was 28,402 of 52,406 actions. The new
candidate will use an exact two-source reviewed migration from that checkpoint;
no .38 objects or relaxed checks are used. Verified LLVM Python bindings now
persist inside the source/toolchain checkpoint, and transient HTTP responses
have bounded retries. The first v3 stage completed 5,975 tasks and retained 18,032 remaining. Its next stage exposed a cache-placement bug: two pinned Python files inside the LLVM tree changed the full tool fingerprint, which invalidated that runner copy. Recovery is pinned to intact run `43953428441`, stage 1, never its stage-2 tree. The audited relocation verifies complete compiler fingerprints before and after moving only those files outside toolchain roots. Future cache writes leave compiler trees unchanged; GN bootstrap follows environment completion, and incremental-only recovery refuses toolchain-wide invalidation. No compiler-fingerprint check is weakened.

Actual rebuilt .78 DLL/browser acceptance and enrollment
remain pending. Local v3 CNG/parser, all 25 controlled DirectWrite groups,
required-source rejection and optional-addition fallback have passed.

## Prior .38 work and evidence

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
