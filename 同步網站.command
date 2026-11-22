#!/bin/zsh
cd "$(dirname "$0")"
/usr/bin/env -u PYTHONPATH python3 build_site.py || { echo "建置失敗"; read -k1; exit 1; }
open index.html
