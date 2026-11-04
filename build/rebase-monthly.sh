#!/usr/bin/env bash
# Read-only compatibility entrypoint; this no longer changes version files or builds.
# Usage: build/rebase-monthly.sh <target-version> [windows|linux|macos]
# New versions fail until their complete pinned recipe has been ported and reviewed.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_VERSION="${1:?usage: rebase-monthly.sh <target-version> [windows|linux|macos]}"
TARGET_PLATFORM="${2:-windows}"
case "$TARGET_PLATFORM" in
  windows) PROFILE=lite ;;
  linux|macos) PROFILE=legacy ;;
  *) echo 'Second argument must name a platform; this read-only check does not use a build directory.' >&2; exit 2 ;;
esac
if [ "$#" -gt 2 ]; then
  echo 'usage: rebase-monthly.sh <target-version> [windows|linux|macos]' >&2
  exit 2
fi
exec "${CHROMIX_PYTHON:-python3}" -B "$REPO/tools/plan_kernel_upgrade.py" \
  --repo "$REPO" --version "$TARGET_VERSION" --platform "$TARGET_PLATFORM" --profile "$PROFILE"
