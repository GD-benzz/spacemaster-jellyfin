# -*- coding: utf-8 -*-
"""
空间大师 · 开放运行时垫片（OPEN SOURCE，可进公开 git）
===================================================================
本文件【不含任何算法】。它只负责定位并调用闭源算法引擎：
  优先调用编译后的二进制 sm_dsp_engine（分发给用户的最终形态）；
  开发期回退到同目录的 engine_core.py（该文件被 .gitignore 排除，不进公开仓）。

公开代码（peq_toggle_nas.py / sm_wrapper.sh）只通过本垫片拿结果，
永远不直接包含 PEQ / 延时 / 下混 的任何公式。
"""

import os
import sys
import json
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
# 搜索顺序：① 同目录 ② 宿主卷(/opt/spacemaster，与 NAS 控制台/容器共享) ③ 镜像内置路径
_ENGINE_CANDIDATES = [
    os.path.join(_HERE, "sm_dsp_engine"),
    "/opt/spacemaster/sm_dsp_engine",
    "/usr/local/bin/sm_dsp_engine",
]


def _engine():
    """返回 (kind, engine)。kind='bin' 用二进制；'py' 用源码(开发期)；None 未找到。"""
    for cand in _ENGINE_CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return "bin", cand
    try:
        import engine_core  # 开发期源码（gitignored，不进公开仓）
        return "py", engine_core
    except Exception:
        return None, None


def compute(gen):
    """房间/系统/滑块 -> {bands, delays}。"""
    kind, eng = _engine()
    if kind == "bin":
        out = subprocess.run([ENGINE_BIN, "compute", json.dumps(gen, ensure_ascii=False)],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)
    if kind == "py":
        return eng.compute(gen)
    raise RuntimeError("空间大师算法引擎未找到：需 sm_dsp_engine 二进制或 engine_core 模块")


def auto_baseline(gen):
    """几何走时差 -> {FL, FR}（供后端 compute_delay_string 使用）。"""
    kind, eng = _engine()
    if kind == "py":
        return eng.auto_baseline(gen)
    if kind == "bin":
        out = subprocess.run([ENGINE_BIN, "auto-baseline", json.dumps(gen, ensure_ascii=False)],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)
    return {}


def build_filter(params):
    """拼完整 ffmpeg -af 滤镜串（下混 + 对称延时 + PEQ + 几何 + 平衡）。"""
    kind, eng = _engine()
    if kind == "bin":
        out = subprocess.run([ENGINE_BIN, "build-filter", json.dumps(params, ensure_ascii=False)],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    if kind == "py":
        return eng.build_filter_string(params)
    raise RuntimeError("空间大师算法引擎未找到")


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "compute":
        print(json.dumps(compute(json.loads(sys.argv[2])), ensure_ascii=False, indent=2))
