
FROM jellyfin/jellyfin:latest

ARG SM_ENGINE_URL=

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl bash \
 && rm -rf /var/lib/apt/lists/*

RUN mv /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/lib/jellyfin-ffmpeg/ffmpeg.orig

COPY docker-jellyfin/sm_wrapper.sh sm_dsp_engine* /tmp/smbuild/
RUN if [ -f /tmp/smbuild/sm_dsp_engine ]; then \
      mv /tmp/smbuild/sm_dsp_engine /usr/local/bin/sm_dsp_engine \
      && chmod +x /usr/local/bin/sm_dsp_engine ; \
    fi

RUN if [ -n "$SM_ENGINE_URL" ]; then \
      curl -fsSL "$SM_ENGINE_URL" -o /usr/local/bin/sm_dsp_engine \
      && chmod +x /usr/local/bin/sm_dsp_engine ; \
    fi

COPY docker-jellyfin/sm_wrapper.sh /usr/lib/jellyfin-ffmpeg/ffmpeg
RUN chmod +x /usr/lib/jellyfin-ffmpeg/ffmpeg

