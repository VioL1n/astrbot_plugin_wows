#!/usr/bin/env bash
# One-time fast install of this plugin's dependencies into AstrBot desktop's
# shared site-packages, reusing local uv/pip caches and an already downloaded
# Playwright Chromium. Run it while AstrBot is quit.
set -euo pipefail

PLUGIN_NAME="astrbot_plugin_wows"
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ASTRBOT_PYTHON:-/Applications/AstrBot.app/Contents/Resources/backend/python/bin/python3}"
DATA_DIR="${ASTRBOT_DATA:-$HOME/.astrbot/data}"
TARGET="$DATA_DIR/site-packages"
BROWSERS_DIR="$DATA_DIR/plugin_data/$PLUGIN_NAME/browsers"
INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
BROWSER_SOURCES=(
  "$HOME/Library/Caches/ms-playwright"
  "$HOME/Applications/kokomibot/storage/hikari/browsers"
)

if pgrep -f "backend/launch_backend.py" >/dev/null; then
  echo "AstrBot 仍在运行，请先退出 AstrBot 再运行本脚本。" >&2
  exit 1
fi
[ -x "$PY" ] || { echo "找不到 AstrBot 自带的 Python：$PY" >&2; exit 1; }
UV="$(command -v uv || true)"
[ -n "$UV" ] || UV="$HOME/.local/bin/uv"
[ -x "$UV" ] || { echo "找不到 uv，请先安装：https://docs.astral.sh/uv/" >&2; exit 1; }

PLAYWRIGHT_REQ="$(grep -E '^playwright' "$PLUGIN_DIR/requirements.txt")"
HIKARI_REQ="$(grep -E '^hikari-core' "$PLUGIN_DIR/requirements.txt")"
# A wheel pip already built from the pinned git commit avoids cloning GitHub.
HIKARI_WHEEL="$(find "$HOME/Library/Caches/pip/wheels" -name 'hikari_core-*.whl' 2>/dev/null | head -1 || true)"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
# Same idea as AstrBot's own `-c constraints`: never replace the core packages
# AstrBot ships with different versions.
"$PY" -I -m pip list --format=freeze 2>/dev/null > "$WORK_DIR/constraints.txt"

echo "==> 安装依赖到 $TARGET"
mkdir -p "$TARGET"
"$UV" pip install \
  --python "$PY" \
  --target "$TARGET" \
  --index-url "$INDEX_URL" \
  -c "$WORK_DIR/constraints.txt" \
  "${HIKARI_WHEEL:-$HIKARI_REQ}" \
  "$PLAYWRIGHT_REQ"

echo "==> 复用已下载的 Chromium"
read -r CHROMIUM_REV SHELL_REV FFMPEG_REV < <(
  "$PY" -I - "$TARGET/playwright/driver/package/browsers.json" <<'EOF'
import json, sys
revisions = {b["name"]: b["revision"] for b in json.load(open(sys.argv[1]))["browsers"]}
print(revisions["chromium"], revisions["chromium-headless-shell"], revisions["ffmpeg"])
EOF
)
[ -n "${CHROMIUM_REV:-}" ] || { echo "无法读取 playwright 期望的浏览器版本" >&2; exit 1; }
MARKER="$BROWSERS_DIR/.chromium-env"
if [ -f "$MARKER" ] && grep -q "REVISION=$CHROMIUM_REV" "$MARKER"; then
  echo "    已存在 chromium-$CHROMIUM_REV，跳过"
else
  SOURCE=""
  for candidate in "${BROWSER_SOURCES[@]}"; do
    if [ -d "$candidate/chromium-$CHROMIUM_REV" ]; then SOURCE="$candidate"; break; fi
  done
  if [ -z "$SOURCE" ]; then
    echo "    本机没有 chromium-$CHROMIUM_REV，首次出图时由 Hikari 自动下载"
  else
    mkdir -p "$BROWSERS_DIR"
    for name in "chromium-$CHROMIUM_REV" "chromium_headless_shell-$SHELL_REV" "ffmpeg-$FFMPEG_REV"; do
      if [ -d "$SOURCE/$name" ] && [ ! -e "$BROWSERS_DIR/$name" ]; then
        # -c clones on APFS: instant and no extra disk space.
        cp -cR "$SOURCE/$name" "$BROWSERS_DIR/$name"
        echo "    $SOURCE/$name"
      fi
    done
    # Hikari's own "browser installed" marker (minimal_screens_hot_service.setup_playwright).
    printf 'PLAYWRIGHT_BROWSERS_PATH=%s\nREVISION=%s\n' "$BROWSERS_DIR" "$CHROMIUM_REV" > "$MARKER"
  fi
fi

echo "==> 校验"
"$PY" -I - "$TARGET" <<'EOF'
import importlib.metadata as metadata, importlib.util, sys
sys.path.insert(0, sys.argv[1])
for module in ("hikari_core", "playwright", "apscheduler"):
    assert importlib.util.find_spec(module), f"missing {module}"
versions = {d.metadata["Name"].lower(): d.version for d in metadata.distributions(path=[sys.argv[1]])}
print(f"    hikari-core {versions.get('hikari-core')}, playwright {versions.get('playwright')}")
EOF
echo "完成。现在可以启动 AstrBot。"
