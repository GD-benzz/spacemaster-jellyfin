# 空间大师 · Jellyfin 版（开源集成层 + 闭源算法二进制）
# ===================================================================
# 本 Dockerfile 只打包"集成层"：wrapper 薄壳 + 配置。
# 所有算法（房间驻波->PEQ、几何走时差、下混矩阵、环绕对称延时）都在
# 闭源二进制 sm_dsp_engine 里，源码 engine_core.py 不进本公开仓（见 .gitignore）。
#
# 二进制三种供给方式（维护者构建镜像时任选其一）：
#   A. 本地烤入（最简单）：把编译好的 sm_dsp_engine 放到本目录，直接 docker build，
#      Dockerfile 自动 COPY 进镜像 /usr/local/bin/sm_dsp_engine。
#   B. 构建时拉取：docker build --build-arg SM_ENGINE_URL=<下载地址> .
#   C. 宿主卷提供：把 sm_dsp_engine 放到宿主 /opt/spacemaster/，容器挂载后自动发现。
# 发布公开镜像给终端用户时，用 A 或 B（二进制随镜像分发，用户零操作）。
# ===================================================================

FROM jellyfin/jellyfin:latest

# 闭源二进制下载地址（可选，方式 B）。留空则用方式 A/C。
ARG SM_ENGINE_URL=

# wrapper 是 bash 脚本；镜像装 curl（拉二进制）+ 确保 bash 在。
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl bash \
 && rm -rf /var/lib/apt/lists/*

# 把官方 ffmpeg 备份为 ffmpeg.orig，wrapper 顶替 ffmpeg 同名位置。
RUN mv /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/lib/jellyfin-ffmpeg/ffmpeg.orig

# 方式 A：若本目录存在 sm_dsp_engine，则烤入镜像（wrapper 薄壳一并带上做通配锚点，
# 保证即使没有 sm_dsp_engine 时 COPY 也不报错）。
COPY docker-jellyfin/sm_wrapper.sh sm_dsp_engine* /tmp/smbuild/
RUN if [ -f /tmp/smbuild/sm_dsp_engine ]; then \
      mv /tmp/smbuild/sm_dsp_engine /usr/local/bin/sm_dsp_engine \
      && chmod +x /usr/local/bin/sm_dsp_engine ; \
    fi

# 方式 B：构建时从 URL 拉取闭源二进制烤入镜像。
RUN if [ -n "$SM_ENGINE_URL" ]; then \
      curl -fsSL "$SM_ENGINE_URL" -o /usr/local/bin/sm_dsp_engine \
      && chmod +x /usr/local/bin/sm_dsp_engine ; \
    fi

# 拷贝开源 wrapper 薄壳，替代 ffmpeg 同名文件。
COPY docker-jellyfin/sm_wrapper.sh /usr/lib/jellyfin-ffmpeg/ffmpeg
RUN chmod +x /usr/lib/jellyfin-ffmpeg/ffmpeg
