#!/usr/bin/env sh
# Install dsh-multimodel-coordination into a separate verification profile.
# Usage: ./install.sh [profile] [spec]
set -eu
PROFILE="${1:-coordination-verify}"
SPEC="${2:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}"
if ! command -v dsh >/dev/null 2>&1; then
  echo "dsh CLI not found on PATH. Use the desktop plugin manager, or install the CLI first." >&2
  exit 1
fi
echo "install: dsh plugin --profile $PROFILE add $SPEC"
dsh plugin --profile "$PROFILE" add "$SPEC"
echo
echo "Installed into profile '$PROFILE' (the running profile was not touched)."
echo "Next: dsh --profile $PROFILE  -> Settings -> 多模型协作 -> 验证此版本 -> 确认启用"
