<div align="center">

# 空间大师 · Jellyfin 版 / SpaceMaster for Jellyfin

**基于房间声学的 Jellyfin 转码校正层**
**Room-acoustics correction transcoding layer for Jellyfin**

[English](#english) · [中文](#中文)

</div>

---

<a name="english"></a>

## English

### What is this?

SpaceMaster is a drop-in audio-correction layer for [Jellyfin](https://jellyfin.org/). You enter your room dimensions in a small web console; it computes and applies, **during transcoding**, a set of corrections tuned to your room and speaker setup:

- **Parametric EQ (PEQ)** — tames standing-wave (room-mode) peaks derived from your room size.
- **Geometric channel delay** — time-aligns speakers based on their distance to the listener.
- **Downmix matrix** — clean 5.1 → 2.0 / 2.1 downmix (no crosstalk echo).
- **Symmetric surround delay** — a small distance-compensation delay on the surround channels for a more enveloping 5.1 feel.

It works by replacing Jellyfin's `ffmpeg` with a thin wrapper that injects the correction filters computed by the audio engine.

### How it works

```
Room dimensions (web console :8777)
        │
        ▼
  Audio engine (sm_dsp_engine)  ──►  writes EQ / delay / balance config to /opt/spacemaster
        │
        ▼
Jellyfin transcode  ──►  ffmpeg wrapper reads config, asks engine for the -af filter, injects it
        │
        ▼
   Corrected audio stream to your player
```

- **Ports:** `8096` Jellyfin · `8777` correction console (runs on the host).
- **Config lives on the host** at `/opt/spacemaster/` (`peq_af.txt`, `peq_sdelay.txt`, `peq_delay.txt`, `peq_balance.txt`, `downmix.env`). Changes are hot-read on the next transcode.

### Open-source vs. closed-source

The **integration layer is open source** (this repo). The **algorithms are closed source** and ship only as a compiled binary — the source (`engine_core.py`) is never published.

| Component | Where it lives | In this public repo? |
|-----------|----------------|:---:|
| Integration layer (wrapper shell, console UI, Docker files, config format) | This GitHub repo | ✅ Yes |
| Algorithm **source** `engine_core.py` | Author's machine only | ❌ Never |
| Algorithm **compiled binary** `sm_dsp_engine` | **Baked inside the prebuilt Docker image** | ✅ (as a compiled binary, inside the image — not as source) |

You never have to send anyone a file: the compiled engine is distributed **inside the public Docker image**. Cloning the repo alone gives you only the "shell" — without the engine binary the system degrades gracefully (audio plays uncorrected).

### Install (end users)

> Requirements: a Linux host (NAS, server, etc.) with Docker + Docker Compose, and Python 3 for the console.

**1. Get the integration layer**

```bash
git clone https://github.com/GD-benzz/spacemaster-jellyfin.git
cd spacemaster-jellyfin
```

**2. Start Jellyfin + the correction wrapper** (pulls the prebuilt public image; the engine binary is already inside)

```bash
docker compose up -d
```

**3. Give the host console the engine binary** (extract it from the image you just pulled — nothing to download separately)

```bash
sudo mkdir -p /opt/spacemaster
sudo docker cp jellyfin-sm:/usr/local/bin/sm_dsp_engine /opt/spacemaster/sm_dsp_engine
sudo chmod +x /opt/spacemaster/sm_dsp_engine
```

**4. Set up the web console** (host, systemd)

```bash
sudo cp peq_toggle_nas.py engine_runtime.py /opt/spacemaster/
# create a systemd service that runs: python3 /opt/spacemaster/peq_toggle_nas.py
sudo systemctl daemon-reload && sudo systemctl enable --now peq-console.service
```

**5. Use it**

Open `http://<HOST_IP>:8777`, enter your room dimensions, adjust the sliders, and click **Apply**. Playback in Jellyfin is now corrected.

> If the engine binary is missing, nothing crashes: the wrapper **passes audio through untouched** (no correction) and the console reports the engine is unavailable.

### For maintainers — build & publish the image

You build the image **once** with the engine baked in, push it to a public registry, and end users just `docker compose up`. You never hand-deliver a binary.

```bash
# 1. Compile the engine on a Linux x86_64 host (PyInstaller is NOT cross-platform):
#    pyinstaller --onefile engine_core.py -n sm_dsp_engine
#    -> produces ./dist/sm_dsp_engine ; copy it next to the Dockerfile as ./sm_dsp_engine

# 2. Build the image (Dockerfile auto-copies ./sm_dsp_engine into the image):
docker build -t ghcr.io/gd-benzz/spacemaster-jellyfin:latest .

# 3. Log in to GHCR and push (make the package public in GitHub settings afterwards):
echo "$GITHUB_TOKEN" | docker login ghcr.io -u GD-benzz --password-stdin
docker push ghcr.io/gd-benzz/spacemaster-jellyfin:latest
```

> The image name must be lowercase: `ghcr.io/gd-benzz/...`.

### Security note (honest)

The engine binary inside the image is **compiled machine code**, not readable source. A determined person could extract it (`docker cp`), just as with any distributed binary — but your formula source (`engine_core.py`) never leaves your machine. This is the same protection tier as handing out a compiled binary. If you ever need airtight secrecy, run the algorithm as a server-side API instead (heavier to operate).

---

<a name="中文"></a>

## 中文

### 这是什么？

空间大师是给 [Jellyfin](https://jellyfin.org/) 用的即插即用音频校正层。你在一个小网页控制台里输入房间尺寸，它就会在**转码过程中**按你的房间和音箱布局计算并施加一组校正：

- **参量均衡（PEQ）**——压制由房间尺寸推导出的驻波（房间模态）峰值。
- **几何声道延时**——按各音箱到听音位的距离做时间对齐。
- **下混矩阵**——干净的 5.1 → 2.0 / 2.1 下混（无交叉串扰回声）。
- **环绕对称延时**——给环绕声道加一点距离补偿延时，让 5.1 更有包围感。

原理：用一个薄壳脚本顶替 Jellyfin 的 `ffmpeg`，把音频引擎算好的校正滤镜注入转码。

### 工作原理

```
房间尺寸（网页控制台 :8777）
        │
        ▼
   音频引擎（sm_dsp_engine）  ──►  把 EQ/延时/平衡配置写到 /opt/spacemaster
        │
        ▼
Jellyfin 转码  ──►  ffmpeg 薄壳读配置，向引擎要 -af 滤镜串并注入
        │
        ▼
   校正后的音频流送到你的播放器
```

- **端口：** `8096` Jellyfin · `8777` 校正控制台（跑在宿主机上）。
- **配置在宿主机** `/opt/spacemaster/`（`peq_af.txt`、`peq_sdelay.txt`、`peq_delay.txt`、`peq_balance.txt`、`downmix.env`）。改动在下一次转码时热加载。

### 开源 vs. 闭源

**集成层开源**（本仓库）。**算法闭源**，只以编译后的二进制形式分发——源码 `engine_core.py` 永不发布。

| 组成部分 | 放在哪 | 进本公开仓？ |
|---------|--------|:---:|
| 集成层（wrapper 薄壳、控制台界面、Docker 文件、配置格式） | 本 GitHub 仓库 | ✅ 是 |
| 算法**源码** `engine_core.py` | 只在作者机器上 | ❌ 永不 |
| 算法**编译二进制** `sm_dsp_engine` | **烤在预构建的 Docker 镜像里** | ✅（以编译二进制形式在镜像内，非源码） |

你不需要给任何人发文件：编译好的引擎**随公开 Docker 镜像分发**。只 clone 本仓库拿到的是"空壳"——没有引擎二进制时系统安全降级（音频照常播放，只是不做校正）。

### 安装（终端用户）

> 前提：一台装了 Docker + Docker Compose 的 Linux 主机（NAS、服务器等），控制台需要 Python 3。

**1. 拿到集成层**

```bash
git clone https://github.com/GD-benzz/spacemaster-jellyfin.git
cd spacemaster-jellyfin
```

**2. 启动 Jellyfin + 校正薄壳**（自动拉取预构建公开镜像，引擎二进制已在镜像内）

```bash
docker compose up -d
```

**3. 把引擎二进制交给宿主控制台**（从刚拉下来的镜像里抠出来，无需另外下载）

```bash
sudo mkdir -p /opt/spacemaster
sudo docker cp jellyfin-sm:/usr/local/bin/sm_dsp_engine /opt/spacemaster/sm_dsp_engine
sudo chmod +x /opt/spacemaster/sm_dsp_engine
```

**4. 部署网页控制台**（宿主机，systemd）

```bash
sudo cp peq_toggle_nas.py engine_runtime.py /opt/spacemaster/
# 建一个 systemd 服务，运行：python3 /opt/spacemaster/peq_toggle_nas.py
sudo systemctl daemon-reload && sudo systemctl enable --now peq-console.service
```

**5. 使用**

浏览器打开 `http://<宿主IP>:8777`，输入房间尺寸，拖动滑块，点**应用**。Jellyfin 里的播放即被校正。

> 缺引擎二进制也不会崩：薄壳会**原样透传音频**（不校正），控制台提示引擎不可用。

### 维护者——构建并发布镜像

你只需**构建一次**（把引擎烤进镜像），推到公开镜像仓库，终端用户 `docker compose up` 即可。你永远不用手动发二进制。

```bash
# 1. 在 Linux x86_64 主机上编译引擎（PyInstaller 不跨平台）：
#    pyinstaller --onefile engine_core.py -n sm_dsp_engine
#    -> 生成 ./dist/sm_dsp_engine ；把它拷到 Dockerfile 同目录，命名 ./sm_dsp_engine

# 2. 构建镜像（Dockerfile 会自动把 ./sm_dsp_engine 烤进镜像）：
docker build -t ghcr.io/gd-benzz/spacemaster-jellyfin:latest .

# 3. 登录 GHCR 并推送（推完到 GitHub 里把该 package 设为 public）：
echo "$GITHUB_TOKEN" | docker login ghcr.io -u GD-benzz --password-stdin
docker push ghcr.io/gd-benzz/spacemaster-jellyfin:latest
```

> 镜像名必须全小写：`ghcr.io/gd-benzz/...`。

### 安全说明（实话实说）

镜像里的引擎是**编译过的机器码**，不是可读源码。有人硬要抠（`docker cp`）是能抠出来的——任何分发的二进制都一样——但你的公式源码 `engine_core.py` 始终没离开你的机器。这和"直接发编译二进制"是同一层保护。若哪天要绝对保密，只能把算法做成服务端 API（运维成本高得多）。

---

<div align="center">

详细的架构与部署说明见 [`开源说明.md`](开源说明.md) 与 [`ARCHITECTURE.md`](ARCHITECTURE.md)。
See [`开源说明.md`](开源说明.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md) for architecture & deployment details.

</div>
