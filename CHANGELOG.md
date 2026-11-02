# Changelog

## Unreleased — Native browser ownership (2026-11-02)

- Restore the pinned Google search definition and Chromium search-suggestion and translation defaults. Retain native regional, user and policy preference precedence; leave session and history behavior upstream.
- Use a validated managed-environment UUID for native Windows taskbar grouping and profile avatars, including a single regular profile. Disable unmanaged profile shortcuts and relaunch properties inside Chromium.
- Enforce environment camera/microphone restrictions in native permission queries and capture dispatch, blocked ports in native effective-port checks, and DNT through the native command-line preference layer. Forward port policy to renderer and network processes; no manager extension or per-profile JSON rewrite is required.
- Bind generated restoration patches to their generator, configuration, overlays and selected baseline inputs. Reuse source caches only with matching pinned hashes, and preserve unchanged source timestamps and compiled objects during a reviewed same-156 checkpoint upgrade.
- Build four focused native integration cases using the existing test executable, and require separate account-free runtime probes. Source verification alone does not establish compiled or Windows UI acceptance.

## Unreleased — Windows 156 Lite continuation

Probe the default internal-WebUI access gate before testing the Safe Browsing
diagnostic page in a separate disposable profile with only the documented
diagnostic Local State opt-in. Keep security defaults, sandbox and network denial
unchanged; require both contexts and current verifier-bound schema-2 receipts.

Link the existing Windows C++ signature-payload encoder into its web-package
library. The upstream adaptation disconnected its former source set while test
support still used the function. Keep the encoder bytes, signature checks and
native Passkey test suites unchanged, and reuse the latest complete checkpoint.

Keep enterprise profile ID collection disabled when restoring the adjacent
Safe Browsing and password-protection signals. Restore the exact pinned Windows
ETW message resources removed by binary pruning; retain the original RC source
and resource target. Reuse the complete stage3 checkpoint through the reviewed
source migration instead of rebuilding unrelated objects.

Restore the Safe Browsing reporting getter declarations and native preference
write as one interface, fixing the observed Windows compiler mismatch without
changing protection defaults. Preserve useful partial compilation when an edge
fails, but do not let a later stage timeout hide the failure or automatically
retry the unchanged source.

Correct PowerShell's residual native exit status after a handled stage-budget
pause. A failed donor badge is still rejected unless explicitly pinned,
reviewed timeout evidence and a complete matching checkpoint establish this
specific misclassification; actual compiler failures are not admitted.

The follow-up functional review removes a leftover matrix copy whose Bridge
consumer had been retired, preserving native GL call order and arguments.
Candidate acceptance now binds the main harness, oracle and complete collector
set to current source bytes, and requires the packaged retirement contract and
capability declaration to match their prepared-source inputs.

Remove unused external Canvas Bridge, Runtime suppression, FakeShadowRoot and
synthetic backend implementations from the final Chromium source. Restrict
fonts, audio and media modes to Native/ShardX while preserving current GPU,
pixel export, native voice, text/offset and passkey dependencies. Rejected
retired inputs require actual native negative-test evidence before acceptance.

Split Windows acquisition, extraction, source preparation, GN configuration,
incremental compilation, staging, verification and archiving into visible
steps on one runner. Add an explicit reviewed same-156 source transition,
verified transfer reuse, disk budgets, source timestamp preservation and
complete checkpoint retention. A named completed donor job may hand off to
one uniquely identified continuation; ordinary resume remains strict.

Source replay, source/fixture tests and bounded C++ helper tests are separate
from a complete Chromium build and exact-DLL runtime acceptance. The existing
running job and installed production route are not changed by this commit.

## Earlier Google-services work

- Default pushes verify an existing source-run candidate without installing the
  SDK or restoring/rebuilding Chromium. Explicit resume requires a completed
  checkpoint and has no cold/upstream fallback.
- Future resume jobs calculate their compile window after preparation, reserve
  50 minutes for finalization, and record cached outputs and pending Ninja work.
- Preserve Google WebAuthn AppID origin checks independently of regenerated
  domain-substitution lists, without rewriting unchanged cached sources.
- Gate Google-service candidates on local HTTPS/CTAP2 registration, rejection
  and restart checks; keep account access and cloud synchronization unverified
  until tested with the owner's configured account.
- Include source-recipe, Actions run, cache donor and complete bundle hashes in
  Google-service builds. Continue using the existing staged incremental build.

Affected files: `tools/prepare_google_services.py`,
`tools/verify_google_services_source.py`, `tools/verify_google_services_runtime.py`,
`tools/write_google_services_build_record.py`, `build/windows/package-win.ps1`,
`build/windows/build-google-services.ps1` and the Google-service workflows.
