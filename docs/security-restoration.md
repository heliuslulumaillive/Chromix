# Desktop security restoration

The security branch targets Chromium **156.0.8078.79**, ungoogled core
`28a211ab4ab6b83d919966a4f58a6df23cf4fb29`, and the matching Windows layer
`8492884eb30fdd9417ea7816aa92def805c97c74`. The historical configuration filename
`build/google-services-win155.json` remains the preparation interface; its
version fields and the generated aggregate must identify the actual base.

The restoration removes the layers that disable desktop Safe Browsing,
client-side phishing requests, danger classification, and download quarantine.
It also restores the mixed preference-removal hunks, password leak checks,
service factories, network/database startup, resources, and settings bindings.
The native Safety Hub entry is restored with its original visibility condition;
the separate Privacy Guide promotion remains outside this restoration.
The JavaScript optimizer preference moves back to its upstream Safe Browsing
definition to avoid duplicate declarations and registrations.
The component update-check entry point is restored so signed file-type policy
and other registered security component updates can run. This is separate from
the browser auto-updater; its disabled configurator is not restored here. The
mode-1 resource uses the generated, nonempty `download_file_types.asciipb` policy,
not the empty fallback protobuf.

Upstream preference defaults are retained: Standard Protection enabled,
Enhanced Protection disabled, and optional reporting disabled. Preparation
does not write browser profiles or override an existing user's preference.
Google authentication, Trusted Vault, sync encryption, and WebAuthn validation
are not replaced with permissive fallbacks.

The empty file-type-policy protobuf is a binary source resource. Its pinned
payload lives in `build/security-resources/`, with size and SHA-256 in the
restoration configuration. `restored_source_resources.py` verifies and restores
it separately from the GNU text patch. The generator checks it against the
fixed Chromium source; preparation rejects unexpected existing bytes. Both the
payload and `build/args.security.gn` are bound into the preparation recipe.

The security build must merge **`build/args.security.gn` last** and verify the
effective `safe_browsing_mode = 1` before GN generation and compilation. The
generic Windows argument file remains separate. Preserve the actual final GN
arguments and complete source/build receipts with the artifact.

Source replay, aggregate forward/reverse checks, and the source gates establish
preparation consistency only. They do not establish a successful native build,
live Safe Browsing service, password leak result, account eligibility, or
fingerprint equivalence. Those require acceptance of the newly compiled exact
DLL. Keep the original kernel, production route, and saved environment bindings
until that acceptance is complete.
