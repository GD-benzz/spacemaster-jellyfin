#!/usr/bin/env bash
# 空间大师 · 闭源引擎构建脚本（维护者专用）
# ===================================================================
# 作用：把 engine_core.py（含全部算法，不进公开仓）用 Nuitka 编译成
#       原生机器码二进制 sm_dsp_engine。
#       编译后产物是机器码（非 Python 字节码），反编译难度远高于 PyInstaller。
#
# 用法：
#   1) 把 engine_core.py 放到本目录（它只在你本地，不进 git）
#   2) 在本目录执行：  bash build_engine.sh
#   3) 得到 sm_dsp_engine，随后 docker build 会把它烤进镜像
#
# ⚠️ 跨平台：Nuitka 编译产物不跨平台。给 Linux NAS 用户，必须在
#    Linux x86_64 上跑本脚本；不要在 Mac/Windows 上编了发给 Linux 用。
# ===================================================================
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
