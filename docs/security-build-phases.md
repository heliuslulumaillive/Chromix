# Windows 156 continuation phases

The worker uses one disposable GitHub-hosted Windows runner for all phases.
Splitting the visible Actions steps does not transfer the source tree or
`out/Default` between jobs. The running historical job is immutable; these
changes apply only to a new run at this source commit.

A handled `SECURITY_BUDGET_STOP` explicitly clears the surviving PowerShell
native exit status; real compilation failures still throw. The named original
donor misreported this planned pause as failure. It may be admitted only with a
separately reviewed evidence artifact and exact file hashes, consistent safe
checkpoint/build inputs, a cleanly terminated timeout and no compiler errors.
The exception is opt-in for that evidence, not permission to resume failed jobs
generally. No large transfer occurs during the review.

| Phase | Work | Reuse boundary |
| --- | --- | --- |
| Acquire | Validate the small checkpoint manifest and pinned donor first, then download its exact volumes; or fetch the pinned upstream snapshot | No SDK installation or compilation before donor admission. A preflight record pins the selected artifact IDs. Credentials are forwarded only to acquisition helpers. |
| Extract | Validate archive paths, extract once, verify source/args/receipt, and admit an explicit same-version migration | Original donor provenance is retained before only the exact owned transfer directory is released. No fallback to a 155 donor. |
| Prepare | Install the native SDK once; establish VS inputs; prepare or migrate source and validate it | Resume/upgrade never reruns base domain substitution. The preparation helper preserves timestamps of sources whose final bytes did not change. |
| Configure | Re-establish the same VS environment; merge effective GN, prepare host tools, regenerate the graph and record pending Ninja work | Existing GN is rewritten only when content differs. PGO preservation and toolchain invalidation rules remain active. |
| Compile | Run Ninja against the restored source, objects and dependency state | No deletion of `out/Default`, `.ninja_log` or `.ninja_deps`; only genuinely invalidated work is rebuilt. |
| Bundle | Stage the runtime and freeze its file inventory, without making a ZIP | The later archive verifies this inventory instead of rebaselining test-modified files. |
| Verify | Execute and validate native passkey, native smoke, security, Google-service and fingerprint gates | Source-only or inherited reports do not replace current exact-bundle verification. |
| Archive | Create the deliverable ZIP once and retain matching outer receipts | This phase is unreachable until Verify completes successfully. |

`fresh` remains an explicit cold-bootstrap option. Its base-preparation script
already owns its pinned download/unpack layer markers; these are not claimed to
be independent cached jobs. The current continuation path is `upgrade` from an
explicit registered Windows 156 checkpoint, followed by ordinary `resume` of
the new recipe if more time is needed.

For checkpoint continuation, the tiny donor preflight succeeds before the
fixed hosted-runner tool cleanup allowlist runs. Download admission then budgets
all compressed parts, the uncompressed tree size when present in the manifest,
and a 24 GiB build reserve. Old manifests do not have a guessed tree size: after
download, one path-validated `7z l` header read records the actual uncompressed
size, and extraction requires that size plus the same reserve. There is no
duplicate `7z t` full decompression. New checkpoints record the tree size already
measured for compression planning, so future runs can reject insufficient space
before a large transfer.

The original job start time controls a 300-minute work deadline across every
phase, leaving 55 minutes of the 355-minute job budget for a complete checkpoint
and its upload. Every bounded owned command uses the remaining total budget.
Phase state is saved atomically with the run, checkout, stage and donor identity;
skipped, repeated or reordered phases are rejected. Command numbers and per-phase
timings survive separate PowerShell invocations on the runner.

A budget stop skips later phases. Once source preparation and required final GN
values have been verified, failures or budget stops can retain the complete work
tree. Interrupted acquisition, extraction or migration is not labelled resumable;
the small diagnostics and original donor remain available instead of compressing
an unusable partial tree. Automatic continuation requires a budget stop plus a
successfully published, resume-safe checkpoint manifest. Compile or acceptance
errors are not automatically retried as successful progress.

`retain_checkpoint=true` keeps one complete source/object checkpoint even after
candidate delivery. The manifest is uploaded only after all content-bound parts.
The worker reports `checkpoint_ready` separately from `finished`. If retention
was requested but the candidate upload succeeds while checkpoint creation or
publication fails, the final result fails explicitly; the already delivered
candidate remains available, but reusable-cache completion is not claimed.
Setting retention false preserves the historical option to skip the giant
archive after successful candidate delivery.

These controls improve visibility and avoid repeated large transfers within a
runner. They do not guarantee a cache-hit percentage: changed Chromium files,
GN flags or toolchains must still invalidate affected objects. Current online
account and threat-service acceptance limitations remain unchanged.
