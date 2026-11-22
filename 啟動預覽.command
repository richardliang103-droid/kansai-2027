#!/bin/zsh
cd "$(dirname "$0")"
( sleep 1; open "http://localhost:8787" ) &
exec /usr/bin/env -u PYTHONPATH python3 -m http.server 8787 --bind 127.0.0.1
