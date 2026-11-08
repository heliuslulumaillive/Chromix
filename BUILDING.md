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

Run `42416735586` retained a complete stage-3 checkpoint after the removed ICU
locale API failed to compile. Its explicit `upgrade` transition (source stage
`3`) authenticates every product source and rewrites only the two registered
locale files. Unchanged sources and object state retain their timestamps;
ordinary resume still refuses changed recipes. This transition grants no
runtime or DLL acceptance.

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
