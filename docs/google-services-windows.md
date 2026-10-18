# Google services on Windows 155

This variant restores the Chromium account and Sync implementations while
retaining Chromix fingerprint patches and Windows storage behavior. The pinned
version is 155.0.8059.39. Google credentials are supplied at runtime and are never
included in the build or its cache.

## Community references

- [ungoogled PR 3914](https://github.com/ungoogled-software/ungoogled-chromium/pull/3914),
  commit `f9cc5514ec21ba73040b838465dfc34d34001f03`, identifies substituted
  Google/gstatic AppID validation constants. This build restores the checker
  implementation, header and test vectors independently of generated lists.
  Origin and AppID-fragment rejection remain enabled.
- [Hibbiki Windows Chromium](https://github.com/Hibbiki/chromium-win64) and
  [Thorium](https://thorium.rocks/faq) are desktop references for retained Google
  sign-in and Sync. Their credential configuration is not automatically inherited.
- [microG ungoogled Chromium](https://github.com/bearinmindcat/microg-ungoogled-chromium)
  is an Android reference for restoring Gaia/GCM and SyncService. Do not port its
  empty Trusted Vault key backend or suppression of actionable sync errors.

## Incremental build and concurrent jobs

`build-google-services-win155.yml` keeps `cancel-in-progress: false`. A push to
the same feature branch queues behind the active run, preserving its current
compile and both stages. A stage only resumes from uploaded checkpoint volumes;
an in-progress runner's object files are not available to another job.

`build-2` depends on `build-1` because it consumes that stage's completed
checkpoint. These stages must remain sequential. Independent builds can use
separate hosted runners, but they cannot share unuploaded compilation progress.
Use an independent branch/concurrency group only when a separate build is needed.

Source preparation writes only changed bytes. Upgrading an already prepared
checkpoint with this validation change leaves unchanged C++ inputs and object
files untouched. The script continues to run Ninja against `out/Default`.

## Candidate checks

The worker packages a complete browser, runs the Windows bundle smoke and then
runs `tools/verify_google_services_runtime.py`. The latter uses local HTTPS pages
and an ephemeral certificate pinned only for that child browser. It checks:

- ordinary WebAuthn registration and authentication;
- Google registration with both gstatic `appidExclude` values and legacy support;
- rejection of unrelated RP IDs, unrelated AppIDs and AppIDs with fragments;
- browser-profile storage across restart and authentication after rehydrating
  the synthetic CTAP2 authenticator.

The synthetic authenticator's private key is kept in memory and never written to
the report. Rehydrating it is a protocol test, not proof of Windows Hello key
persistence. No real Google site, account or cloud vault is used by this probe.

To run the same check against an explicitly selected complete browser:

```powershell
python -m pip install -r tools/google-services-requirements.txt
python -B tools/verify_google_services_runtime.py --browser '<complete-browser>/chrome.exe' --report '<new-report.json>'
```

`google-services-build.json` inside the bundle binds the source recipe, Actions
commit/run, checkpoint donor and file inventory. `google-services-runtime.json`
in the artifact binds the tested EXE/DLL hashes. Account and cloud access remain
`not_tested` in these reports.

## Account setup after the candidate is ready

Use the owner's Google API/OAuth client and the account eligibility described in
[Chromium's API-key documentation](https://www.chromium.org/developers/how-tos/api-keys/).
The local credentials file is `%USERPROFILE%/.config/chromix-build/google-api-credentials.json`.
It is consumed through `build/windows/start-google-account-probe.ps1` or the
matching Lite bridge; do not commit it or put its values on the command line.

The real-account check is a single user flow: sign in, observe the browser
account, exchange a test bookmark with another signed-in browser, restart and
verify persistence, then complete a passkey sign-in. Password synchronization
and Google Password Manager cloud passkeys require their own successful data
and unlock observations; a visible avatar or hidden error banner is not evidence.

Import the complete accepted kernel as a separate immutable version in Lite.
Existing environments retain their kernel bindings until explicitly changed.
