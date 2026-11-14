# Windows product builds

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

The isolated application-package candidate on
`codex/font-pack-cohorts-win157-20261114` preserves the complete published Windows
base/FOD inventory and known family, full-name, PostScript and truncated GDI
aliases. It varies Office, Noto Sans CJK, Noto Serif CJK and Liberation as whole
installed packages; unknown fonts remain native. Twelve seed-stable profiles
describe installation choices, never proxy-country inventories. Missing packages
can make profiles identical on a particular host. Source/compiled host tests are
separate from the pending hosted DLL build and subsequent local/Rare acceptance;
no formal kernel binding or DLL admission is changed by this branch.
