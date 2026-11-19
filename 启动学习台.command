#!/bin/bash
set -eu
APP_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$APP_ROOT"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.9+ is required."
  echo "Run: xcode-select --install"
  echo "Or install Miniforge, then run: conda activate learn"
  echo "For Chinese instructions, open app/README.md."
  read -r -p "Press Enter to close..." _answer
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  python3 -c 'print("\u5b66\u4e60\u53f0\u9700\u8981 Python 3.9 \u6216\u66f4\u65b0\u7248\u672c\u3002\u8bf7\u5148\u66f4\u65b0 Python\u3002")'
  read -r -p "Press Enter to close..." _answer
  exit 1
fi
exec python3 -B -m app.server.main --root "$APP_ROOT" --open
