# 空间大师 · Jellyfin 版（开源集成层 + 闭源算法二进制）
# ===================================================================
# 本 Dockerfile 只打包"集成层"：wrapper 薄壳 + 配置。
# 所有算法（房间驻波->PEQ、几何走时差、下混矩阵、环绕对称延时）都在
# 闭源二进制 sm_dsp_engine 里，源码 engine_core.py 不进本公开仓（见 .gitignore）。
#
# 二进制两种供给方式（任选其一）：
#   A. 构建时拉取：docker build --build-arg SM_ENGINE_URL=<下载地址> .
#      -> 烤进镜像 /usr/local/bin/sm_dsp_engine（镜像自包含）
#   B. 宿主卷提供：把 sm_dsp_engine 放到宿主 /opt/spacemaster/，
#      容器挂载该卷后 wrapper 自动发现（推荐：一份二进制，控制台+容器两处共用）
# ===================================================================

FROM jellyfin/jellyfin:latest

# 闭源二进制下载地址（可选）。留空则靠宿主卷 /opt/spacemaster/sm_dsp_engine。
ARG SM_ENGINE_URL=

# wrapper 是 bash 脚本；镜像装 curl（拉二进制）+ 确保 bash 在。
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl bash \
 && rm -rf /var/lib/apt/lists/*

# 把官方 ffmpeg 备份为 ffmpeg.orig，wrapper 顶替 ffmpeg 同名位置。
RUN mv /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/lib/jellyfin-ffmpeg/ffmpeg.orig

# 可选：构建时拉取闭源二进制烤入镜像。
RUN if [ -n "$SM_ENGINE_URL" ]; then \
      curl -fsSL "$SM_ENGINE_URL" -o /usr/local/bin/sm_dsp_engine \
      && chmod +x /usr/local/bin/sm_dsp_engine ; \
    fi

# 拷贝开源 wrapper 薄壳，替代 ffmpeg 同名文件。
COPY docker-jellyfin/sm_wrapper.sh /usr/lib/jellyfin-ffmpeg/ffmpeg
RUN chmod +x /usr/lib/jellyfin-ffmpeg/ffmpeg
