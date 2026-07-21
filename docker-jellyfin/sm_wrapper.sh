#!/bin/bash
# 空间大师 ffmpeg wrapper（薄壳，OPEN SOURCE）
# ===================================================================
# 本脚本【不含任何算法】。所有"空间大师独有方法"——房间驻波->PEQ、几何走时差、
# 5.1->2.0/2.1 下混矩阵、环绕对称延时——都封装在闭源二进制 sm_dsp_engine 中
# （源码 engine_core.py 不进公开 git，见 .gitignore）。
#
# 本脚本只做"集成层"工作：
#   1. 探测输入声道数/布局（ffprobe）
#   2. 读取用户 EQ/几何延时/平衡/环绕延时配置文件（/opt/spacemaster/*.txt）
#   3. 拼一个 JSON 喂给 sm_dsp_engine build-filter，拿回完整 ffmpeg -af 滤镜串
#   4. 决定 eac3 重编码码率/元数据、声道目标(-ac)、把 -af 注入转码命令
#
# 二进制缺失时【安全透传】（不套任何 DSP），保证播放可用。
#
# 三种模式（由 SM_DOWNMIX 切换）：
#   0 = 保留原始声道布局只注 EQ（电视自己做下混，空间感最好）
#   1 = 多声道 -> pan 下混到立体声（给无 5.1 解码能力的设备）
#   2 = 真实 2.1（L+R+LFE 三路独立，低音炮不折进左右）
# 参数全部环境变量可调（见下方 SM_* 默认值）。
# ===================================================================

REAL_FFMPEG=/usr/lib/jellyfin-ffmpeg/ffmpeg.orig
AF_FILE=/opt/spacemaster/peq_af.txt
LOG=/opt/spacemaster/wrapper.log

# 闭源算法引擎二进制：构建时烤入镜像(/usr/local/bin)，或宿主卷提供(/opt/spacemaster)。
# 二选一即可；宿主卷那份同时被 NAS 控制台(宿主进程)复用，最省事。
ENGINE_BIN=""
if [ -x /usr/local/bin/sm_dsp_engine ]; then
  ENGINE_BIN=/usr/local/bin/sm_dsp_engine
elif [ -x /opt/spacemaster/sm_dsp_engine ]; then
  ENGINE_BIN=/opt/spacemaster/sm_dsp_engine
fi

# 可选：从 downmix.env 读取用户覆盖（NAS 上调参不用重建镜像）
if [ -f /opt/spacemaster/downmix.env ]; then
  . /opt/spacemaster/downmix.env
fi

# ---- 可调参数（环境变量，默认值如下）----
SM_DOWNMIX=${SM_DOWNMIX:-1}     # 1=pan下混到立体声(无5.1解码设备); 0=保留原始声道只注EQ(电视); 2=真实2.1(L+R+LFE独立)
SM_FL=${SM_FL:-1.00}            # 主声道 FL/FR 权重
SM_CENTER=${SM_CENTER:-0.707}   # 中置 FC 权重（Jellyfin 标准 -3dB=0.707）
SM_SURR=${SM_SURR:-0.707}       # 环绕(BL/BR 或 SL/SR) 权重（Jellyfin 标准 -3dB=0.707）
SM_LFE=${SM_LFE:-0.707}         # LFE(.1) 权重
SM_MAKEUP=${SM_MAKEUP:-6}       # 下混补偿增益 dB（对齐 Jellyfin ×2 下混 ≈ +6dB）
SM_LOUD=${SM_LOUD:-0}           # 响度补偿 dB：让"开PEQ(平坦)"≈原盘透传响度

echo "$(date '+%F %T') WRAP args=$*" >> "$LOG"

# ---- 读取用户配置（均为已算好的结果，本脚本不计算任何公式）----
AF=""
if [ -s "$AF_FILE" ]; then
  AF=$(cat "$AF_FILE")
fi
DELAY=""                               # 几何每声道走时差（adelay 串），来自 peq_delay.txt
DELAY_FILE=/opt/spacemaster/peq_delay.txt
if [ -s "$DELAY_FILE" ]; then
  DELAY=$(cat "$DELAY_FILE")
fi
BALANCE=""                             # 左右平衡 "gL|gR"，来自 peq_balance.txt
BALANCE_FILE=/opt/spacemaster/peq_balance.txt
if [ -s "$BALANCE_FILE" ]; then
  BALANCE=$(cat "$BALANCE_FILE")
fi
SDELAY=""                              # 环绕固定对称延时（毫秒数），来自 peq_sdelay.txt
SDELAY_FILE=/opt/spacemaster/peq_sdelay.txt
if [ -s "$SDELAY_FILE" ]; then
  SDELAY=$(cat "$SDELAY_FILE")
fi

# PEQ / 几何延时 / 平衡 / 环绕延时 均为空 -> 透明直出
if [ -z "$AF" ] && [ -z "$DELAY" ] && [ -z "$BALANCE" ] && [ -z "$SDELAY" ]; then
  exec "$REAL_FFMPEG" "$@"
fi

args=("$@")

# 无音频输出（-an，如抽帧/字幕提取）-> 直出
for a in "$@"; do
  if [ "$a" = "-an" ]; then
    exec "$REAL_FFMPEG" "$@"
  fi
done

# 找最后一个 -i（输入之后才能注入 -af）
last_i=-1
for ((i=0;i<${#args[@]};i++)); do
  if [ "${args[i]}" = "-i" ]; then last_i=$i; fi
done
if [ $last_i -lt 0 ]; then
  exec "$REAL_FFMPEG" "$@"
fi

# 探测输入声道数/布局（仅用 ffprobe，非算法）
INPUT="${args[last_i+1]}"
CH=0; LAYOUT=""
FFPROBE_BIN=""
if [ -x /usr/lib/jellyfin-ffmpeg/ffprobe ]; then
  FFPROBE_BIN=/usr/lib/jellyfin-ffmpeg/ffprobe
elif command -v ffprobe >/dev/null 2>&1; then
  FFPROBE_BIN=ffprobe
fi
if [ -n "$FFPROBE_BIN" ]; then
  PROBE_IN="$INPUT"
  case "$PROBE_IN" in
    file:*) PROBE_IN="${PROBE_IN#file:}" ;;
  esac
  PROBE=$("$FFPROBE_BIN" -v error -select_streams a:0 -show_entries stream=channels,channel_layout -of csv=p=0 "$PROBE_IN" 2>/dev/null)
  CH=$(echo "$PROBE" | head -1 | cut -d, -f1)
  LAYOUT=$(echo "$PROBE" | head -1 | cut -d, -f2)
fi
[ -z "$CH" ] && CH=0
echo "$(date '+%F %T') WRAP input channels=$CH layout=$LAYOUT" >> "$LOG"

# 模式判定（仅决定 -ac 目标声道数，与闭源引擎一致；非秘密）
APPLY=0; DOWNMIX21=0; KEEP_MULTI=0
if [ "$CH" -gt 2 ]; then
  case "$SM_DOWNMIX" in
    1) APPLY=1 ;;
    2) DOWNMIX21=1 ;;
    *) KEEP_MULTI=1 ;;
  esac
fi

# 无引擎 -> 无法拼 DSP 滤镜，安全透传（不套任何 EQ/延时）
if [ -z "$ENGINE_BIN" ]; then
  echo "$(date '+%F %T') WRAP engine binary missing -> transparent passthrough (no DSP)" >> "$LOG"
  exec "$REAL_FFMPEG" "$@"
fi

# 把各配置烤进 JSON（滤镜串可能含 : = 等，需转义双引号/反斜杠）
json_escape() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; printf '%s' "$s"; }
AF_J=$(json_escape "$AF")
DELAY_J=$(json_escape "$DELAY")
BAL_J=$(json_escape "$BALANCE")
LAY_J=$(json_escape "$LAYOUT")
SDELAY_J=$(json_escape "$SDELAY")

JSON=$(cat <<EOF
{"ch":$CH,"layout":"$LAY_J","sm_downmix":$SM_DOWNMIX,"sm_fl":$SM_FL,"sm_center":$SM_CENTER,"sm_surr":$SM_SURR,"sm_lfe":$SM_LFE,"sm_makeup":$SM_MAKEUP,"sm_loud":$SM_LOUD,"sdelay":"$SDELAY_J","peq_af":"$AF_J","delay":"$DELAY_J","balance":"$BAL_J"}
EOF
)

# 调闭源引擎拼完整 -af 滤镜串（下混+对称延时+几何+PEQ+平衡，全在二进制内）
AF_OUT=$("$ENGINE_BIN" build-filter "$JSON" 2>>"$LOG")
rc=$?
if [ $rc -ne 0 ] || [ -z "$AF_OUT" ]; then
  echo "$(date '+%F %T') WRAP engine build-filter failed (rc=$rc) -> passthrough" >> "$LOG"
  exec "$REAL_FFMPEG" "$@"
fi
AF="$AF_OUT"
echo "$(date '+%F %T') WRAP engine build-filter ok af_len=${#AF}" >> "$LOG"

# PEQ/滤镜开启时，copy 流不能套 -af -> 强制 eac3 重编码
REENC=0
for ((i=0;i<${#args[@]};i++)); do
  case "${args[i]}" in
    -c:a|-c:a:*|-codec:a|-codec:a:*|-acodec|-acodec:*)
      v="${args[i+1]}"
      case "$v" in
        copy|aac|aac_latm|mp3|ac3|libmp3lame|libfaac|libfdk_aac)
          args[i+1]="eac3"
          REENC=1
          ;;
      esac
      ;;
  esac
done
if [ "$REENC" -eq 1 ]; then
  echo "$(date '+%F %T') WRAP forced audio re-encode ->eac3 for PEQ" >> "$LOG"
fi
# 任何滤镜注入都需重编码
[ -n "$AF" ] && REENC=1

# E-AC-3 重编码质量（LAN 带宽充足，越高越保真）
case "$CH" in
  2) BR=640000 ;;
  6) BR=1536000 ;;
  *) [ "$CH" -ge 8 ] && BR=2048000 || BR=1536000 ;;
esac
# 元数据：关闭 dialnorm（不压电平）；杜比标准 -3dB/-3dB 下混；Lo/Ro 普通立体声下混
EAC3_OPTS=(-b:a "$BR" -dialnorm -31 -center_mixlev 0.707 -surround_mixlev 0.707 -dmix_mode loro)
echo "$(date '+%F %T') WRAP eac3 target=${BR}bps downmix=std(-3/-3) dialnorm=off" >> "$LOG"

# 是否已带 -af（如 loudnorm/volume），合并进去
has_af=0
for ((i=0;i<${#args[@]};i++)); do
  if [ "${args[i]}" = "-af" ]; then has_af=1; break; fi
done

# 声道目标：APPLY->2; DOWNMIX21->3; KEEP_MULTI->保留(7.1+归6); 其余不动
if [ $has_af -eq 1 ]; then
  for ((i=0;i<${#args[@]};i++)); do
    if [ "${args[i]}" = "-af" ]; then
      args[i+1]="${args[i+1]},${AF}"
      break
    fi
  done
  echo "$(date '+%F %T') WRAP merged AF into existing -af" >> "$LOG"
  [ "$REENC" -eq 1 ] && args+=("${EAC3_OPTS[@]}")
  if [ "$APPLY" -eq 1 ]; then
    args+=(-ac 2)
  elif [ "$DOWNMIX21" -eq 1 ]; then
    ac_set=0
    for ((i=0;i<${#args[@]};i++)); do
      case "${args[i]}" in
        -ac|-ac:a|-ac:a:*) args[i+1]="3"; ac_set=1 ;;
      esac
    done
    [ "$ac_set" -eq 0 ] && args+=(-ac 3)
  elif [ "$KEEP_MULTI" -eq 1 ]; then
    if [ "$CH" -gt 6 ]; then
      for ((i=0;i<${#args[@]};i++)); do
        case "${args[i]}" in
          -ac|-ac:a|-ac:a:*) args[i+1]="6" ;;
        esac
      done
      echo "$(date '+%F %T') WRAP keep-multi: 7.1+ source forced to 5.1(6ch)" >> "$LOG"
    else
      for ((i=0;i<${#args[@]};i++)); do
        case "${args[i]}" in
          -ac|-ac:a|-ac:a:*) [ "${args[i+1]}" = "2" ] && args[i+1]="$CH" ;;
        esac
      done
    fi
  fi
  exec "$REAL_FFMPEG" "${args[@]}"
else
  new=("${args[@]:0:last_i+2}" -af "$AF" "${args[@]:last_i+2}")
  [ "$REENC" -eq 1 ] && new+=("${EAC3_OPTS[@]}")
  if [ "$APPLY" -eq 1 ]; then
    new+=(-ac 2)
  elif [ "$DOWNMIX21" -eq 1 ]; then
    ac_set=0
    for ((i=0;i<${#new[@]};i++)); do
      case "${new[i]}" in
        -ac|-ac:a|-ac:a:*) new[i+1]="3"; ac_set=1 ;;
      esac
    done
    [ "$ac_set" -eq 0 ] && new+=(-ac 3)
  elif [ "$KEEP_MULTI" -eq 1 ]; then
    if [ "$CH" -gt 6 ]; then
      for ((i=0;i<${#new[@]};i++)); do
        case "${new[i]}" in
          -ac|-ac:a|-ac:a:*) new[i+1]="6" ;;
        esac
      done
    else
      for ((i=0;i<${#new[@]};i++)); do
        case "${new[i]}" in
          -ac|-ac:a|-ac:a:*) [ "${new[i+1]}" = "2" ] && new[i+1]="$CH" ;;
        esac
      done
    fi
  fi
  echo "$(date '+%F %T') WRAP injected AF" >> "$LOG"
  exec "$REAL_FFMPEG" "${new[@]}"
fi
