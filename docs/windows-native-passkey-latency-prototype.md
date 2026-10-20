# Windows native passkey metadata-scan prototype

This is a default-disabled prototype based on commit
`90e6d29bf81408820c7bba89caf8bad7dec8430c` and Chromium `155.0.8059.39`.
It omits the browser's preliminary Windows credential metadata query when the
request can go directly to the native Windows picker. It does not change the
assertion's allow-list, transports, hints, attachment, verification, private-mode
flag, cancellation ID, extensions, timeout, or provider selection.

The runtime feature is `WebAuthnWinNativeModalSkipCredentialEnumeration`.
There is no application setting or persistent profile migration.

## Eligibility and implementation

The opt-in passes through three existing layers:

1. `ChromeAuthenticatorRequestDelegate::ConfigureDiscoveries` resets the
   factory intent and enables it only for an ordinary modal WebAuthentication
   get request. The feature must be enabled; no GPM controller, virtual
   environment, test discovery factory, or forced Chrome hybrid discovery may
   be present. Checking the actual GPM controller is deliberately conservative,
   including a controller whose credential list is empty.
2. `AuthenticatorCommonImpl::ConfigureWindowsNativeCredentialEnumeration`
   recomputes the request flag from the real factory and extra-platform-discovery
   vector. It independently rejects non-modal mediation, non-WebAuthentication
   sources, test factories, extra platform discoveries, and empty allow-lists.
   Retries assign false as well as true.
3. `WinWebAuthnApiAuthenticator::GetPlatformCredentialInfoForRequest` first
   retains its existing transport filter. The bypass additionally requires a
   nonempty allow-list, API version 9 or later, and the feature enabled. It posts
   an asynchronous, weak-pointer-bound empty/Unknown result without invoking
   `WebAuthNGetPlatformCredentialList`. Unknown leaves Windows free to offer
   Hello, native passkey managers, security keys, and hybrid under the original
   request. Destruction cancels the metadata callback.

Conditional, ambient, immediate, payment/internal, GPM, extra-provider, virtual,
old-API and empty-allow-list paths retain their existing behavior. Version 9
already includes Windows hybrid support. No RP-specific or hint-based shortcut
is introduced. `webauthn_api.cc` is unchanged.

The new device EVENT message is exactly:

```text
Skipping Windows metadata for eligible modal request
```

It contains no RP, account, credential ID, challenge, or response.

## Patch and source preparation

The authored change is
`patches/google-services/0003-windows-native-passkey-latency.patch`, applied after
the unchanged `0002-windows-native-passkey-discovery.patch`.
`tools/build_google_services_patch.py` regenerates the composite `0001` patch
and source manifest. Overlay-created files are excluded from upstream downloads;
the focused test file is one such new file. The preparation receipt binds the
new overlay and generator. The previous composite patch is retained in history
for checkpoint upgrades.

The prototype changes eleven Chromium files: the Chrome delegate, one new focused
test file, `chrome/test/BUILD.gn`, common implementation/header, assertion-options
header, discovery-factory header, feature implementation/header, and Windows
authenticator implementation/tests. See
`build/windows/native-passkey-latency-source-checks.json` for exact paths and
source evidence.

## Verification and test dependency scope

Local verification:

- 36 source/pipeline/checkpoint Python tests passed.
- Both restoration overlays replayed with `--fuzz=0`.
- Preparing an isolated fixture with the old recipe, upgrading to the new
  recipe, and preparing it again passed.
- Exactly the eleven intended Chromium files changed. The other 153 fixture
  files and timestamps remained unchanged; the second prepare was idempotent.
- All 40 source gates passed.
- `git diff --check` passed.

Eight native tests cover disabled-by-default behavior, explicit intent, actual
metadata bypass, empty allow-list and API-version exclusions, transport
preservation, cancellation before callback, and the unchanged native assertion
boundary. Eight focused Chrome/common tests cover actual delegate configuration,
GPM-controller presence, factory overrides, additional discoveries, request
modes/sources, request preservation and retry reset. The old eight required
regressions remain required; the candidate verifier now requires all 24 named
regressions, independently of the total number of passed tests.

The launcher includes all four relevant test suites. The verification receipt
keeps every passed matching test in `native_passkey_regression_tests` and records
the exact 24-case policy separately in `required_native_passkey_regression_tests`.

The native test target retains its `assert_no_deps` exclusions for aggregate
Chrome test support and Settings/WebUI test data. Pinned GN-source inspection
shows that identity-manager and sync test support were already dependencies of
the existing `testing_profile_impl` target. The one new support target,
`//components/webauthn/core/browser:test_support`, has four Windows `.cc` sources:
fake CMTG provider, mock internal authenticator, WebAuthn test utilities, and
test passkey model. Its direct dependencies are component libraries, base,
content/public/browser, and gmock; it does not add a Chrome test or WebUI aggregate.

These are source checks. The modified C++ tests have not been compiled or run,
and a generated GN dependency graph has not been validated.

## Required performance follow-up

Use the same rebuilt candidate with the feature disabled and enabled, using
separate owned disposable test contexts and a localhost modal get with one
synthetic allow-list ID. The accepted baseline has already shown a native picker
for this no-account case. Empty-allow-list timing is not evidence for this
prototype's gain.

Enable with:

```text
--enable-features=WebAuthnWinNativeModalSkipCredentialEnumeration
```

Disable explicitly with the matching `--disable-features` argument. Keep all
other request and native API options identical. Confirm the EVENT is present
only in the enabled eligible case, and measure JS call to native API, native API
to visible picker, and total time separately. Never report fast rejection as a
faster picker.

Incognito still calls the metadata API and changes `bBrowserInPrivateMode` for
both enumeration and assertion, so it is not an isolated bypass comparison.
Wait for the previous native request to finish, rather than treating the
immediate return from cancellation as completion. Static Microsoft DLL analysis
found a conditional 1500 ms sleep associated with a prior pending command;
runtime execution of that branch remains unproven.

No native build, performance improvement, provider selection result, or renewed
account acceptance is claimed by this source prototype. Existing accepted
browser files, profiles, bindings and account data are unchanged.
