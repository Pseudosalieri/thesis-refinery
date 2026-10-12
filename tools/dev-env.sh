#!/usr/bin/env sh
# Activate a development shell for thesis-refinery.
#
# Companion to tools/dev-env.ps1; see that file for why PYTHONPATH is used
# instead of relying on an editable install's `.pth` file.
#
# Usage:  . ./tools/dev-env.sh

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
src_path="$repo_root/src"

if [ ! -d "$src_path" ]; then
    echo "dev-env: expected a src/ directory at $src_path" >&2
    return 1 2>/dev/null || exit 1
fi

if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH="$src_path:$PYTHONPATH"
else
    PYTHONPATH="$src_path"
fi
export PYTHONPATH

echo "thesis-refinery dev shell"
echo "  repo    : $repo_root"
echo "  PYTHONPATH += $src_path"
echo ""
echo "  python -m thesis_refinery --help"
echo "  python -m pytest"
echo "  python tools/check_desensitized.py"
