# Changelog

## Unreleased

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
