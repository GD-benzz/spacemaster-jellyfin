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

**2. Pull official Jellyfin and fetch the SpaceMaster engine** (the engine ships in a tiny public image — no manual file transfer)

```bash
docker pull jellyfin/jellyfin:latest
sudo mkdir -p /opt/spacemaster
sudo docker run --rm -v /opt/spacemaster:/out \
  ghcr.io/gd-benzz/spacemaster-engine:latest \
  sh -c 'cp /spacemaster/* /out/ && chmod +x /out/sm_dsp_engine /out/setup.sh'
```

**3. Start Jellyfin + the correction wrapper**

```bash
docker compose up -d
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

### For maintainers — build & publish the engine image

You compile the engine **once** (native binary, not cross-platform) and bake it into a **tiny public image**. End users pull the **official** `jellyfin/jellyfin` themselves and fetch your engine image to get the binary — you never hand-deliver a file.

```bash
# 1. Compile the engine on a Linux x86_64 host (Nuitka output is NOT cross-platform):
#    bash build_engine.sh
#    -> produces ./sm_dsp_engine (native machine code, not Python bytecode)

# 2. Build the tiny engine image (Dockerfile copies ./sm_dsp_engine in):
docker build -t ghcr.io/gd-benzz/spacemaster-engine:latest .

# 3. Log in to GHCR and push (make the package public in GitHub settings afterwards):
echo "$GITHUB_TOKEN" | docker login ghcr.io -u gd-benzz --password-stdin
docker push ghcr.io/gd-benzz/spacemaster-engine:latest
```

> The image name must be lowercase: `ghcr.io/gd-benzz/...`.
>
> ⚠️ **The GitHub token used to log in must carry the `write:packages` scope.** A token with only `repo` is rejected with `denied: permission_denied: The token provided does not match expected scopes.` Generate a *classic* PAT at `github.com/settings/tokens`, tick **write:packages** (this also ticks `read:packages`). After pushing, set the package to **Public** in GitHub → Packages, or end users get a 403 on `docker pull`.

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

**2. 拉取官方 Jellyfin 并取回空间大师引擎**（引擎随一个 tiny 公开镜像分发，无需手动传文件）

```bash
docker pull jellyfin/jellyfin:latest
sudo mkdir -p /opt/spacemaster
sudo docker run --rm -v /opt/spacemaster:/out \
  ghcr.io/gd-benzz/spacemaster-engine:latest \
  sh -c 'cp /spacemaster/* /out/ && chmod +x /out/sm_dsp_engine /out/setup.sh'
```

**3. 启动 Jellyfin + 校正薄壳**

```bash
docker compose up -d
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

### 维护者——构建并发布引擎镜像

你只需**编译一次**引擎（原生二进制，不跨平台），把它烤进一个**tiny 公开镜像**。终端用户自己 `docker pull jellyfin/jellyfin`，再用你的引擎镜像把二进制取回本机——你永远不用手动发文件。

```bash
# 1. 在 Linux x86_64 主机上编译引擎（Nuitka 产物不跨平台）：
#    bash build_engine.sh
#    -> 生成 ./sm_dsp_engine（原生机器码，已非 Python 字节码，反编译难度高）

# 2. 构建 tiny 引擎镜像（Dockerfile 会自动把 ./sm_dsp_engine 烤进镜像）：
docker build -t ghcr.io/gd-benzz/spacemaster-engine:latest .

# 3. 登录 GHCR 并推送（推完到 GitHub 里把该 package 设为 public）：
echo "$GITHUB_TOKEN" | docker login ghcr.io -u gd-benzz --password-stdin
docker push ghcr.io/gd-benzz/spacemaster-engine:latest
```

> 镜像名必须全小写：`ghcr.io/gd-benzz/...`。
>
> ⚠️ **登录用的 GitHub Token 必须带 `write:packages` 权限**。只有 `repo` 权限的 token 会被拒：`denied: permission_denied: The token provided does not match expected scopes.`。在 `github.com/settings/tokens` 生成 **classic** PAT，勾选 **write:packages**（会自动连带 read:packages）。推完务必到 GitHub → Packages 把该包设为 **Public**，否则终端用户 `docker pull` 会 403。

### 安全说明（实话实说）

镜像里的引擎是**编译过的机器码**，不是可读源码。有人硬要抠（`docker cp`）是能抠出来的——任何分发的二进制都一样——但你的公式源码 `engine_core.py` 始终没离开你的机器。这和"直接发编译二进制"是同一层保护。若哪天要绝对保密，只能把算法做成服务端 API（运维成本高得多）。

---

<div align="center">

详细的安装步骤见 [`安装指南.md`](安装指南.md)；架构与部署说明见 [`开源说明.md`](开源说明.md) 与 [`ARCHITECTURE.md`](ARCHITECTURE.md)。
See [`安装指南.md`](安装指南.md) for step-by-step setup; [`开源说明.md`](开源说明.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md) for architecture & deployment details.

</div>
