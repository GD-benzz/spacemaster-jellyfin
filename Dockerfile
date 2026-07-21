# 空间大师引擎镜像 —— 仅含闭源二进制 + wrapper + setup，**零 Jellyfin**。
#
# 本镜像【不打包】Jellyfin：终端用户自己 `docker pull jellyfin/jellyfin`，
# 再用本镜像把引擎取回宿主 /opt/spacemaster（见 安装指南.md 第四步）。
#
# 维护者构建：先把编译好的 sm_dsp_engine 放到本目录，再 build & push。
FROM alpine:3.20
COPY sm_dsp_engine                 /spacemaster/sm_dsp_engine
COPY docker-jellyfin/sm_wrapper.sh /spacemaster/sm_wrapper.sh
COPY docker-jellyfin/setup.sh      /spacemaster/setup.sh
RUN chmod +x /spacemaster/sm_dsp_engine /spacemaster/setup.sh

# 终端用户取回命令（见 安装指南.md）：
#   sudo docker run --rm -v /opt/spacemaster:/out \
#     ghcr.io/gd-benzz/spacemaster-engine:latest \
#     sh -c 'cp /spacemaster/* /out/ && chmod +x /out/sm_dsp_engine /out/setup.sh'
