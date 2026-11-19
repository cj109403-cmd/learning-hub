#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3 is required. Run xcode-select --install, or install Miniforge and run conda activate learn.'
    exit 1
fi
update_status=0
python3 -B app/updater/update_content.py "$@" || update_status=$?
python3 -c 'import sys; print("\n\u8bf7\u6309 Enter \u5173\u95ed\u8fd9\u4e2a\u7a97\u53e3\u3002", flush=True); sys.stdin.readline()'
exit "$update_status"
