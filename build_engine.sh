#!/usr/bin/env bash
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

if [ ! -f engine_core.py ]; then
  echo "❌ 未找到 engine_core.py（闭源算法源码）。它只在你本地，请先放到本目录再构建。" >&2
  exit 1
fi

VENV="$HERE/.build-venv"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  echo "→ 创建构建用 venv ..."
  python3 -m venv "$VENV"
fi
echo "→ 安装/更新 Nuitka ..."
"$VENV/bin/pip" install -q --upgrade pip nuitka

echo "→ Nuitka 编译 engine_core.py → sm_dsp_engine（原生机器码，请稍候 1~3 分钟）..."
"$PY" -m nuitka --onefile --clang --assume-yes-for-downloads \
  --output-filename=sm_dsp_engine engine_core.py

echo
echo "✅ 已生成 sm_dsp_engine"
ls -lh sm_dsp_engine
echo
echo "下一步："
echo "  docker build -t ghcr.io/gd-benzz/spacemaster-jellyfin:latest ."
echo "  docker push  ghcr.io/gd-benzz/spacemaster-jellyfin:latest"

