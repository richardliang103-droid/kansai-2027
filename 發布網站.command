#!/bin/zsh
# 重建 → commit → push；GitHub Pages 約 1 分鐘後更新
cd "$(dirname "$0")" || exit 1
/usr/bin/env -u PYTHONPATH python3 build_site.py || { echo "建置失敗"; read -k1; exit 1; }
git add -A
if git diff --cached --quiet; then
  echo "沒有變更，略過 push"
else
  git commit -qm "update $(date '+%Y-%m-%d %H:%M')"
  git push -q origin main && echo "已推送，約 1 分鐘後更新"
fi
open "https://richardliang103-droid.github.io/kansai-2027/"
