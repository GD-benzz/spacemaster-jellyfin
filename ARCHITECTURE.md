# 空间大师 · 文件架构与功能清单

> 数据来源：2026-07-18 从 NAS `/opt/spacemaster/` 实读全部文件。
> 以后交接先看这里，不要再去逐个 cat 文件。

---

## 一、系统拓扑

```
电视(Android TV)
    │
    ▼ HTTP
宿主机:8098 ← profile-proxy.py (systemd 服务, PID 随机)
    │  拦截 POST /Items/{id}/PlaybackInfo
    │  PEQ开(peq_af.txt非空) → 抠 DirectPlay/Transcoding 的 AudioCodec → 逼转码
    │  PEQ关 → 完全透传
    ▼
127.0.0.1:8097 ← docker-proxy (compose 端口映射)
    │
    ▼
容器:8096 ← Jellyfin Server
    │  收到被篡改的 PlaybackInfo → 判定需转码 → 生成 ffmpeg 命令
    ▼
容器:/usr/lib/jellyfin-ffmpeg/ffmpeg ← sm_wrapper.sh (顶替同名, 原版备为 ffmpeg.orig)
    │  热读 peq_af.txt( EQ曲线 ) + downmix.env( 下混参数 )
    │  空 → 透传原版 ffmpeg
    │  非空 → 注入 -af (pan下混+volume补偿+EQ) → 调用 ffmpeg.orig 执行
    ▼
HLS 分片 → 电视播放

并行:
宿主机:8777 ← peq_toggle.py (Python http.server, 控制台)
    │  内嵌 HTML+JS: PEQ开关 / 房间尺寸输入 / 滑块 / 频响曲线可视化
    │  前端【不本地算公式】：原始输入 POST /api/compute -> 闭源引擎算回 {bands,delays}
    │  服务端(经 engine_runtime 调 sm_dsp_engine 二进制): 写 peq_af.txt + 配置文件
    │  点"应用到当前播放"按钮才重播
    │  连接 Jellyfin API (jellyfin.json) 实现 Stop+Play 原进度重播
```

### 端口对照
| 端口 | 进程 | 用途 |
|------|------|------|
| **8098** | profile-proxy.py (宿主机) | TV 实际连接口，拦 PlaybackInfo |
| **8097** | docker-proxy → 容器 8096 | 中间跳转，非 loopback 已 iptables DROP |
| **8096** | Jellyfin (容器内) | 真正的 Jellyfin 服务 |
| **8777** | peq_toggle.py (宿主机) | PEQ 控制台网页 |

> ⚠️ 宿主机 8096 = Emby（非 Jellyfin），别搞混。

---

## 二、核心文件（当前在用）

### 控制层

| 文件 | 大小 | 功能 |
|------|------|------|
| **peq_toggle.py** | 33503B | 8777控制台后端。Python http.server，内嵌完整 HTML+JS。①PEQ 开关 ②房间尺寸输入(L/W/H) ③系统类型选择 ④低音/中音/高音滑块 ⑤频响曲线实时可视化(SVG+biquad仿真) ⑥Jellyfin 连接配置 ⑦**kill ffmpeg 强制重载**：点"应用到当前播放"按钮 → `docker exec pkill -9 ffmpeg` + 清缓存 → Jellyfin 在 TV 下次请求 .ts 时重启 ffmpeg（重读新 peq_af.txt）→ 黑屏一下 → 新 EQ 流。**不用 Stop/Play 远程命令**（Android TV SupportsMediaControl=false 不响应），**不用 DELETE ActiveEncodings**（会清理转码会话→404）。检测：pgrep ffmpeg 从无到有=新EQ生效。**算法全在客户端 JS，服务端只写文件+kill ffmpeg。** |
| **profile-proxy.py** | 15189B | 8098 sidecar 代理。透明转发所有请求到 Jellyfin，唯独拦 `POST /Items/{id}/PlaybackInfo`：PEQ 开时抠 DirectPlayProfiles(全抠) + TranscodingProfiles(只抠aac/mp3) 的 AudioCodec → 客户端无法 DirectPlay → 走转码 → ffmpeg 启动 → wrapper 注入 PEQ。PEQ 关时完全透传。日志写 proxy.log。 |
| **peq-proxy.service** | 462B | systemd 服务文件。`UPSTREAM=http://127.0.0.1:8097` `LISTEN_PORT=8098` `ENABLE=true`。 |

### 处理层

| 文件 | 大小 | 功能 |
|------|------|------|
| **sm_wrapper.sh** | 16237B | 容器内 ffmpeg wrapper（顶替 `/usr/lib/jellyfin-ffmpeg/ffmpeg`，原版备为 `ffmpeg.orig`）。热读 peq_af.txt + downmix.env。空→`exec ffmpeg.orig "$@"`透传；非空→①copy 强制重编码 Dolby(ac3/eac3) ②注入 -af(pan下混+volume补偿+EQ) ③SM_DIALNORM=0 时不注 dialnorm。每次转码热读，改参数不用重启。 |
| **build_af.py** | 1163B | 读 peq.json → 输出 ffmpeg -af 滤镜串（equalizer 链 + adelay）。空 peq 且无 delays → 输出空串 → wrapper 透传。**注意：peq_toggle.py 也直接写 peq_af.txt，build_af.py 是另一条路径，当前可能未在 wrapper 主路径使用。** |

### 配置/数据文件

| 文件 | 大小 | 功能 |
|------|------|------|
| **peq_af.txt** | 695B | **PEQ 总开关 + EQ 曲线**。非空=PEQ开(wrapper注入/proxy抠codec)；空=PEQ关(透传)。内容=逗号分隔的 ffmpeg equalizer/lowshelf 滤镜串。wrapper+proxy 都热读此文件判断开关。当前20段(16驻波+comp+mid+hi+lowshelf)。 |
| **downmix.env** | 671B | 下混参数。wrapper 每次 `source` 它。当前：`SM_DOWNMIX=1 SM_MAKEUP=6 SM_DIALNORM=0 SM_AUDIO_CODEC=ac3 SM_FL=1.0 SM_CENTER=0.707 SM_SURR=1.0 SM_LFE=0.4 SM_LOUD=0.0`。 |
| **peq.json** | 979B | 当前 PEQ 配置 JSON（peq 数组 + meta）。peq_toggle.py 写入，build_af.py 读取。 |
| **peq_gen.json** | ~80B | 生成器输入参数 JSON：`{L,W,H,sys,low,mid,hi}`。peq_toggle.py 写入，页面刷新时恢复滑块状态。⚠️ 文件名前有 ANSI 码残留(`\x1b[;32m`)。 |
| **jellyfin.json** | 75B | Jellyfin 连接配置 `{url, key}`。控制台用此连 Jellyfin API 实现自动重播。**含 API Key，敏感。** |

### 测量工具

| 文件 | 大小 | 功能 |
|------|------|------|
| **measure_loud.sh** | 7656B | 响度实测脚本。取当前播放片→进容器用 ffmpeg.orig 测原盘 vs AAC 电平差(LUFS/RMS)→写 SM_LOUD 到 downmix.env→部署 wrapper。 |
| **measure.py** | 1268B | 纯 Python Goertzel 算法测指定频率(100/500/1k/3k/5k Hz)幅度，可两文件对比输出增量(dB)。 |
| **delay_measure.py** | 503B | 测立体声 L/R 通道冲激峰位置差 → 实际延时(ms)。 |
| **loudness_delta.txt** | 134B | 响度测量记录。当前：`comp=+0.0dB`（原盘和 AAC 电平一致）。 |

### 日志

| 文件 | 大小 | 功能 |
|------|------|------|
| **wrapper.log** | ~1.5MB | wrapper 运行日志。每次转码记录：args/channels/layout/AF注入/downmix applied。 |
| **proxy.log** | ~3.7MB | profile-proxy 运行日志。记录每个 PlaybackInfo 请求的 AudioCodec 抠除情况。 |
| **peq_toggle.log** | 0B | 空日志。 |
| **2mtoggle.log** | 22B | 旧日志，可忽略。 |

---

## 三、已废弃/旧版文件（勿参考）

| 文件 | 大小 | 说明 |
|------|------|------|
| **sm_wrapper.new** | 5929B | 旧版 wrapper（等量下混 0.35 权重，强制 eac3）。已被 sm_wrapper.sh(16237B) 取代。 |
| **peq_af.txt.on** | 342B | 旧版 EQ 曲线备份（固定10段30-300Hz）。已被 peq_toggle.py 动态生成取代。 |
| **peq_on.json** | 164B | 旧版测试用 PEQ 配置（3段：100Hz+6, 1kHz-12, 5kHz-8）。 |
| **peq_off.json** | 32B | 空 PEQ 配置（`{"peq":[],"delays":{}}`）。 |
| **peq_delay.json** | 70B | 延迟校正配置（L=0, R=10ms）。当前未在 wrapper 主路径使用。 |
| **profile-proxy/** | 目录 | 可能是旧版 Node 版 proxy。 |
| **本地 peq_control.html** | 12038B | ⚠️ 本地 spacemaster-nas/ 下的旧版控制台 HTML(7/15)，**与 NAS 上 peq_toggle.py 完全不同**，勿参考。 |

---

## 四、PEQ 算法（闭源，公式不公开）

> ⚠️ 本算法全部公式与系数均在闭源二进制 `sm_dsp_engine` 中，源码 `engine_core.py` 不进 git。
> 下文仅描述**结构与层次**，不含任何可复现的具体常数 / 系数。

### 第一层：房间驻波校正（动态多段）
- 基于房间三维尺寸推导轴向驻波模态（限定在低频区间）。
- 按模态重合度映射为动态多段 PEQ 增益（增益深度与模态重合度相关，并做区间钳制）。
- 大系统与小系统采用不同的增益缩放比例与低频起点。
- 输出段数随房间尺寸变化（典型约 16 段）。

### 第二层：音调滑块
- 低音 / 中音 / 高音三档搁架 / 峰值滤波，增益由用户滑块控制；低音为宽频 lowshelf，不改动驻波 notch 的位置与深度。

### 第三层：下混补偿带
- 针对下混（5.1→2.0）带来的中频损失做固定基础补偿，并跟随中音滑块偏移。

### 当前配置（peq.json）
- 三层叠加后的总段数随房间尺寸与滑块变化（驻波段 + comp + mid + hi + lowshelf）。

---

## 五、关键操作速查

| 操作 | 方法 |
|------|------|
| 开/关 PEQ | 8777 控制台拨开关，或写/清 peq_af.txt |
| 调低音/中音/高音 | 8777 控制台拖滑块（只写文件不重载），点"应用到当前播放"按钮才触发 DELETE+删目录 黑屏重载（TV 黑屏→新EQ流） |
| 改下混参数 | 改 downmix.env（容器热读，挂载卷即时生效） |
| 改 wrapper 代码 | SCP sm_wrapper.sh → docker cp 进容器（重启容器会丢，确认后 docker compose build 固化） |
| 看 wrapper 日志 | `sudo cat /opt/spacemaster/wrapper.log \| tail` |
| 看 proxy 日志 | `sudo cat /opt/spacemaster/proxy.log \| tail` |
| 重启 proxy | `sudo systemctl restart peq-proxy` |
| SSH | `ben@192.168.31.155` pw `147369wcl` |

---

## 六、PEQ 问题排查总结（2026-07-17~18）

| # | 问题 | 根因 | 最终方案 |
|---|------|------|---------|
| 1 | 开PEQ比关PEQ小声10-15dB | eac3的`dialnorm -31`被电视按最狠归一衰减 | 改用ac3 + `SM_DIALNORM=0`不注入dialnorm（ac3默认-31=0衰减，与关PEQ透传一致） |
| 2 | 开PEQ报"播放错误"(0ms停止) | wrapper把协商的AC3换成AAC，HLS manifest仍写AC3→TV按AC3解AAC段失败 | `SM_DOWNMIX=1`时保留Jellyfin协商的AC3/EAC3不换AAC |
| 3 | 开PEQ立体声效果消失+小声 | 给2喇叭端点硬塞5.1让电视自己下混→电视廉价折叠压对白+左右糊 | wrapper用pan矩阵自己折2.0(`SM_DOWNMIX=1`)：L=FL+0.707FC+0.4LFE+1.0BL |
| 4 | copy+PEQ时ffmpeg崩溃 | `-c:a copy`流注`-af`→"Filtering and streamcopy cannot be used together" | AF非空且codec=copy时强制重编码为Dolby(ac3) |
| 5 | EQ-only模式(SM_DOWNMIX=0)响度不正常 | 让Jellyfin服务端下混算法虽好，但电平对不上 | 回退`SM_DOWNMIX=1`+`SM_MAKEUP=6`补偿下混电平损失 |
| 6 | 空间感差 | 环绕声道SM_SURR=0.707(-3dB)太弱，空间信息丢失 | `SM_SURR=1.0`(0dB)完整保留环绕 |
| 7 | 低频效果缺失 | LFE被丢弃(SM_LFE=0.0)防轰头 | `SM_LFE=0.4`(≈-8dB，补偿LFE录音+10dB后等效+2dB) |
| 8 | 改滑块后正在播放的电影不重载新EQ | peq_toggle.py在宿主机跑，clear_transcode_cache清不到容器内`/cache/transcodes`→Stop+Play后Jellyfin复用旧HLS缓存 | clear_transcode_cache加`docker exec`清容器内转码缓存 |
| 9 | 频繁拖滑块导致服务器反复Stop+Play反应不过来 | 旧设计：watch线程监控peq_af.txt变化→1.2s后自动重播，拖一下重播一次 | 删watch线程，改为"拖滑块只写文件不重播，点'应用到当前播放'按钮才触发重播"；新增`/api/replay`(POST触发)+`/api/replay-status`(GET轮询)端点；装载检测：docker exec检查容器内.ts文件出现=新EQ已装载 |
| 10 | 点"应用到当前播放"后TV不黑屏重播，提示"已加载"过早，3分钟后才生效 | ①Stop→Play仅等1s，TV没完全停止就收到Play→TV复用旧HLS缓冲继续播旧EQ；②Play用原位StartPositionTicks→TV从缓冲恢复而非请求新分段；③.ts文件出现=转码开始≠TV已在播放新流，检测过早报告loaded | **此方案无效（见#11）**——根因是 Android TV 根本不响应 Stop/Play 远程命令，调时序没用 |
| 11 | kill ffmpeg 方案前的所有 Stop+Play 变体都不生效/过一会才生效/不黑屏 | **Android TV 客户端 `SupportsMediaControl=false`**，`SupportedCommands` 只有音量/字幕/音轨切换，**不含 Play/Stop/Seek** → Session API 的 Stop/Play 命令对 TV 完全无效，TV 一直在播旧 HLS，ffmpeg 进程一直不重启 → 新 EQ 永远不生效 | **彻底放弃 Stop+Play 远程命令**。改为 **`DELETE /Videos/ActiveEncodings(deviceId)`（直连 Jellyfin 8097）+ 删整个转码目录（含 m3u8）+ `pkill -9 ffmpeg` 兜底**：DELETE 让 Jellyfin 干净拆除转码会话（**204=Clean stop**，杀 ffmpeg+清内存+manifest 失效）；删整个目录让 TV 重新加载 manifest = **黑屏**；TV 继续请求 playlist → Jellyfin 全新转码（重读新 peq_af.txt）→ 影音同步新 EQ。这**正是 Jellyfin 自己改画质黑屏重载的内核路径**。检测：pgrep ffmpeg 从无到有（再等 6s 让 TV 切流）= 新 EQ 生效。 |

> 详细排查过程见 `2026-07-17.md` / `2026-07-18.md` 日日志。

---

## 七、🔴 元教训：为什么之前无数次都失败（必读，避免重蹈覆辙）

前面 #1~#11 是逐条问题，这里提炼**跨问题的共同失败模式**——之前所有"改了无数次都不行"的尝试，本质上都踩了同一个思维坑。

### 元教训 A（响度 / 编码类问题：声音变小、播放错误、立体声塌）

**❌ 失败的共同假设**：「wrapper / 控制台比 Jellyfin 更懂，应该替它改 codec / 声道数 / dialnorm 来『优化』。」

每次这样改都失败，且失败方式不同：
- **改 codec（AAC 替 AC3）** → HLS manifest 已声明 `ac-3` → TV 按 AC3 解 AAC 段 → 播放错误（0ms 停止）。
- **改声道（`-ac 6` 覆盖 Jellyfin 的 `-ac 2`）** → 撤销了 Jellyfin 的下混决策 → TV 拿到 5.1 自己廉价折叠 → 小声 + 立体声塌。
- **调 dialnorm（`-31` → `-27`）** → dialnorm 是给解码器的**响度衰减指令**，不是音量旋钮。用户对比的参照是**透传（无 dialnorm = 0 衰减）**，任何注入值都让转码比透传小声。调到 -27 仍注入 -27 衰减，照样小声。

**✅ 成功的共同模式**：**只注入滤波（EQ / pan 下混 / volume 补偿），绝不改 Jellyfin 与 TV 之间的 codec / 声道 / dialnorm 契约。**
- 需要下混 → 在 wrapper 内用 pan 矩阵做好再输出，编码保持 Jellyfin 协商的 AC3/EAC3，不换 AAC。
- 需要响度对齐 → 靠 `volume=+6dB`（`SM_MAKEUP`）补偿下混电平损失，**不碰 dialnorm**（`SM_DIALNORM=0` = 不注入 = 与透传一致）。
- **关键认知**：dialnorm 没有「中性值」，唯一中性就是「不注入」。任何「调到某个值就匹配」的尝试都是追鬼。

### 元教训 B（重载 / 重播类问题：改滑块不重载、3 分钟延迟、不黑屏）

**❌ 失败的共同假设**：「要让 TV 重新装载新 EQ，就得发 Stop 再 Play 命令让客户端重播。」

每次这样改都失败：watch 线程自动 Stop+Play、按钮 Stop→Play、调时序（1s→3s→+1s 偏移→4s）——**全是白发**。因为 **Android TV `SupportsMediaControl=false`，`SupportedCommands` 只有音量/字幕/音轨切换，不含 Play/Stop/Seek** → TV 直接忽略所有远程播放控制命令 → 一直播旧 HLS → ffmpeg 不重启 → 新 EQ 永不生效。时序调得再精细也白费，因为命令根本没送达 TV。

**✅ 成功的共同模式**：**不要命令客户端，要动服务端转码会话。**
- **`DELETE /Videos/ActiveEncodings(deviceId)`（直连 Jellyfin 8097，绕过 proxy）+ `docker exec rm -rf /cache/transcodes/*`（删整个目录含 m3u8）+ `pkill -9 ffmpeg` 兜底** → Jellyfin 干净拆除转码会话（**204=Clean stop**）→ TV 重新加载 manifest（**黑屏**）→ Jellyfin 全新转码（重读新 peq_af.txt）→ 影音同步新 EQ。
- 这**复刻了 Jellyfin 自己改画质时的「黑屏重载」**——它也是服务端重启转码（客户端主动重新协商 manifest），从不是命令客户端重播。我们的重载机制从一开始就该镜像这个，而不是试图遥控 TV。

**⚠️ 两个配套陷阱（曾分别踩过）：**
1. **✅ 正确做法：用 `DELETE /Videos/ActiveEncodings(deviceId)`**（直连 8097）。返回 204=Clean stop，干净拆除转码会话（杀 ffmpeg+清内存+manifest 失效），**正是**触发 TV 黑屏重载的正确入口。❌ 旧结论「DELETE 会 404 不重启」是**误判**——之前只试了错误参数（返回 400）就放弃，实测 204 会让 TV 下次请求确定性地触发全新转码。配合 `rm -rf /cache/transcodes/*`（删整个目录含 m3u8）双保险，确保 TV 重新加载 manifest（黑屏）而非走段重试（卡帧）。
2. **别用「.ts 文件出现」作为「新转码已启动」信号**：旧 ffmpeg 清缓存后也会继续生成 `.ts`，检测到的可能是旧进程。用 `pgrep ffmpeg` 进程「从无到有」才是真信号（新 ffmpeg 启动 = 新 EQ 生效），且检测后多等 6s 让 TV 从黑屏切到新流，避免过早报「已加载」。

### 元教训 C（通用诊断铁律，早用可省下整个 Stop+Play saga）

> **任何「远程控制客户端」的方案落地前，先查该客户端的 Sessions 能力：`SupportsMediaControl` / `SupportedCommands`。若为 false 或不含 Play/Stop/Seek，立刻放弃远程控制思路，改服务端进程操作。**

这条铁律如果一开始就用，Stop+Play 的整个反复调试（watch 线程、按钮 Stop→Play、3s/+1s/4s 时序）可以直接跳过。

### 元教训 D（服务端控制边界：能撤流、不能命令客户端播放状态）

**❌ 易混的误区**：「服务端停止播放再重新播放」听起来像一招，其实它内部有两层完全不同的事，必须拆开：

| 层级 | 服务端能不能动 | 走的通道 | Android TV 0.19.9 实测 |
|---|---|---|---|
| ① **转码流（服务端资源）** | ✅ 完全控制 | `DELETE /Videos/ActiveEncodings` / `pkill ffmpeg` / 删分段 | 有效。这正是现在用的「杀 ffmpeg → TV 被动重连」 |
| ② **电视播放状态（客户端状态）** | ❌ 不能控制 | Session API 的 Stop/Play/Seek 远程命令 | 无效。TV 直接忽略（实测：发 Stop/Play 后 pos 一直涨、ffmpeg 从未重启） |

- **「服务端停止播放再重新播放」若指 ②（命令 TV app 停/播）**：做不到，TV 不收，实测失败 → 这正是之前 Stop+Play 方案被废弃的根因。
- **若指 ①（撤掉转码流）**：做得到，且是现在的方案。但这不是「命令电视」，而是**把电视的粮食（流）撤了**——电视 app 还在跑、缓冲还在，过一会儿发现没新段可要了，才被动重新连、拿到新 ffmpeg。

**✅ 本质结论**：Jellyfin 是**流媒体服务器，不是远程桌面**——它管「媒体怎么送到客户端」，「客户端怎么播」由客户端自己决定。
- 服务端对客户端只有**间接杠杆**（撤流 → 逼它重连），没有**直接权威**（命令它停/播/重载）。
- 客户端不「opt-in」（`SupportsMediaControl=false`）→ 服务端碰不到它的播放状态。哪些客户端交出控制权（true）、哪些死死攥着（false），因客户端而异；**Android TV 0.19.9 属于后者**。
- 这也解释了「45 秒沉默、不黑屏」是 **Android TV 的 ExoPlayer 缓冲太长 + 静默重试**的锅，不是方案的问题：撤流（①）做得到，但「电视多久才重连」取决于电视自己的缓冲，服务端命令不了。换成 PC 网页端（缓冲短、重载时显示转圈），同一套方案就是「秒切 + 装载中转圈」。

> 元教训 C 与 D 是同一铁律的两个侧面：**C 说「先查客户端能力，再决定远程控制是否可行」；D 说「即便不可行，服务端仍有『撤流』这唯一的间接杠杆可用」。** 两者合起来 = 永远别去发 Stop/Play 命令碰壁，直接走撤流。

**🔴 2026-07-18 实测证伪「服务端发指令逼 TV 主动重载」假设**：曾设想 TV 的 `SupportedCommands` 含 `SetAudioStreamIndex/SetSubtitleStreamIndex`，或许能像「按遥控器字幕/音轨键」那样逼 TV 自己 reload（等同「回详情页点继续播放」）。实测：①专用子端点 `POST /Sessions/{sid}/Playing/AudioStreamIndex` → **400**（10.11 路由已把它并入 Play）；②改用 **Play 端点** `POST /Sessions/{sid}/Playing?itemIds=&audioStreamIndex=2&startPositionTicks=当前&playCommand=PlayNow` → **204**（Jellyfin 接受并转发 PlayMessage）。但监控转码目录 GUID 前缀 40 秒**始终不变（无新转码会话）**，且会话复查 `AudioStreamIndex` 仍=1（发的 target=2 未生效）、进度正常递增、未暂停 → **TV 0.19.9 连 PlayMessage 也彻底忽略**。结论：服务端**没有任何命令**能逼 TV 主动 reload；唯一杠杆仍是撤流（被动等缓冲 ≈45s），真正秒切只剩**用户手动拖进度条**（客户端动作）。此假设已证伪，今后勿再提出「服务端发指令逼 TV 重载」类方案。

### 关于「两个方案」的准确关系（避免误删好代码）

用户口中的「两个方案」不是「一个失败一个成功」的替代关系，而是**外壳 + 内核**：
- **方案 1（控制流重构）= 保留**：删 watch 线程 → 拖滑块只写文件不重播 → 点「应用到当前播放」按钮才触发 → 新增 `/api/replay`(POST) + `/api/replay-status`(GET 轮询) + 前端每 1s 轮询显示「正在重载音效…」→「新音效已生效」。这解决的是「频繁拖滑块令服务器反应不过来」，**与 Stop/Play 是否生效无关，本身是对的。**
- **方案 2（重载内核替换）= 真正解决问题**：把方案 1 里失效的 Stop/Play 内核，替换成 `docker exec pkill -9 ffmpeg` + 清缓存。**外壳（按钮 + 状态轮询）全部保留**，只换了「怎么让 ffmpeg 重启」这一层。
- ❌ 切勿因为「Stop/Play 不行」就回退整个按钮架构——那会连带着丢掉状态轮询体验。

---

## 八、IP 边界

- **护"方法"（闭源）**：全部算法在 `engine_core.py`（不进 git），编译为二进制 `sm_dsp_engine` 分发。
  包括：驻波推导(axialModes/mergeModes/rawDepth/qOf/base_gain)、系数体系、三层模型、几何走时差、
  下混矩阵(5.1→2.0 / 2.1)、环绕对称延时。前端与控制台都只发输入、收结果，绝不本地算公式。
- **不护"曲线"**：peq_af.txt 是具体 EQ 曲线，随房间/口味变，不算 IP，可公开。
- **wrapper 机制须开源**：sm_wrapper.sh（薄壳，只探测声道+调 sm_dsp_engine build-filter+注入）的拦截+注入机制公开。
- **分发架构**：公开 git 持集成层(Web/Wrapper/Docker) + 闭源二进制 sm_dsp_engine 单独分发（不进 git）。
- **铁律**：纯对称环绕延时，绝不复制环绕声道做对侧交叉馈送（无哈斯/回声）；此逻辑仅存于闭源二进制。
