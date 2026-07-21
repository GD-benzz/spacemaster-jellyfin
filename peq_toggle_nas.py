#!/usr/bin/env python3
# 空间大师 · 统一控制台 (8777)
# 合并: PEQ 开关 + 调整页面(房间→模态生成器) + 下混参数
# 纯标准库, 无外部依赖; wrapper 热读 /opt/spacemaster/peq_af.txt
# 生成算法(房间→PEQ / 几何→延时 / 下混矩阵)已抽离到【闭源引擎 engine_core / sm_dsp_engine】，
#   服务端经 engine_runtime 调用，本文件【不含任何公式】。
#   本文件供【私有 NAS】使用, 勿直接塞进公开 docker 镜像/仓库(改用 Worker 版 peq-config)。
import json, os, shutil, time, threading
import engine_runtime
import urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

PEQ_AF    = os.environ.get("PEQ_AF",   "/opt/spacemaster/peq_af.txt")
PEQ_GEN   = os.environ.get("PEQ_GEN",  "/opt/spacemaster/peq_gen.json")
PEQ_JSON  = os.environ.get("PEQ_JSON", "/opt/spacemaster/peq.json")
PEQ_DELAY = os.environ.get("PEQ_DELAY","/opt/spacemaster/peq_delay.txt")
PEQ_BALANCE = os.environ.get("PEQ_BALANCE","/opt/spacemaster/peq_balance.txt")
PEQ_SDELAY  = os.environ.get("PEQ_SDELAY", "/opt/spacemaster/peq_sdelay.txt")
DOWNMIX   = os.environ.get("DOWNMIX",  "/opt/spacemaster/downmix.env")
PORT      = int(os.environ.get("PORT", "8777"))

# 本房间手调终版预设(2026-07-16 用户手动微调最佳, 电视2喇叭) — 仅结果曲线, 非算法
# 生成器默认输入(用户房间 + 已调滑块)
GEN_DEFAULT = {"L":3.1,"W":4.4,"H":2.8,"sys":"tv","low":0.0,"mid":0.7,"hi":1.5,
               "delayOn":False,"tvW":1.2,"dist":2.5,"offX":0.0,"tvH":0.9,"earH":1.1,
               "balance":0.0,
               "surroundDelay":0.0,
               "delayManual":{"FL":0.0,"FR":0.0,"FC":0.0,"SL":0.0,"SR":0.0,"LFE":0.0}}


def read_peq():
    try:
        s = open(PEQ_AF).read().strip()
    except FileNotFoundError:
        return False, []
    if not s:
        return False, []
    bands = []
    for part in s.split(','):
        part = part.strip()
        if part.startswith('equalizer='):
            part = part[len('equalizer='):]
            ptype = None
        elif part.startswith('lowshelf='):
            part = part[len('lowshelf='):]
            ptype = 'lowshelf'
        else:
            ptype = None
        d = {}
        for kv in part.split(':'):
            if '=' in kv:
                k, v = kv.split('=', 1)
                d[k] = v
        if 'f' in d and 'g' in d:
            band = {"f": float(d['f']), "g": float(d['g']),
                    "Q": float(d.get('w', d.get('q', 1)))}
            if ptype == 'lowshelf':
                band["t"] = "lowshelf"
            bands.append(band)
    return True, bands


def write_peq(bands):
    parts = []
    for b in bands:
        if b.get('t') == 'lowshelf':
            parts.append("lowshelf=f=%s:g=%s:w=%s:t=q" % (b['f'], b['g'], b.get('Q', 0.7)))
        else:
            parts.append("equalizer=f=%s:g=%s:w=%s:t=q" % (b['f'], b['g'], b['Q']))
    s = ",".join(parts)
    open(PEQ_AF, 'w').write(s)
    try:  # 兼容旧机制(wrapper 实际只读 peq_af.txt)
        json.dump({"peq": bands, "meta": {}}, open(PEQ_JSON, 'w'))
    except Exception:
        pass


def read_gen():
    try:
        return json.load(open(PEQ_GEN))
    except Exception:
        return None


def write_gen(g):
    try:
        json.dump(g, open(PEQ_GEN, 'w'))
    except Exception:
        pass


def read_downmix():
    d = {"SM_DOWNMIX": "1", "SM_FL": "1.00", "SM_CENTER": "0.707", "SM_SURR": "0.707",
         "SM_LFE": "0.707", "SM_MAKEUP": "6", "SM_LIMIT": "1.0"}
    try:
        for line in open(DOWNMIX):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                d[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return d


def write_downmix(upd):
    d = read_downmix()
    d.update({k: str(v) for k, v in upd.items()})
    open(DOWNMIX, 'w').write("\n".join("%s=%s" % (k, v) for k, v in d.items()) + "\n")


def sync_downmix_from_sys(gen):
    """根据系统类型同步下混开关，wrapper 热读 downmix.env 下次转码即生效：
    ht51=真5.1直出(SM_DOWNMIX=0, KEEP_MULTI)；
    ht21=真实2.1(L+R+LFE独立, SM_DOWNMIX=2, DOWNMIX21)；
    tv=强制下混2.0(SM_DOWNMIX=1, APPLY)。"""
    sys_t = (gen or {}).get('sys', 'tv')
    if sys_t == 'ht51':
        dm = '0'
    elif sys_t == 'ht21':
        dm = '2'
    else:
        dm = '1'
    write_downmix({'SM_DOWNMIX': dm})


# ============ 延时校准（adelay 字符串生成，移植演示版 perChannelDelays）============
# 每模式输出声道（决定 adelay delays 的通道数与顺序）：
#   tv   -> 2.0 (FL,FR)             电视内置/电脑自带音箱，下混立体声
#   ht21 -> 2.1 (FL,FR,LFE)         真实2.1（L+R+LFE独立）
#   ht51 -> 5.1 (FL,FR,FC,SL,SR,LFE)
# MVP 仅 tv 启用；5.1/2.1 延时后续开放（compute_delay_string 对非 tv 仍返回 None）。
DELAY_CHANNELS = {
    'tv':   ['FL', 'FR'],
    'ht21': ['FL', 'FR', 'LFE'],
    'ht51': ['FL', 'FR', 'FC', 'SL', 'SR', 'LFE'],
}

# auto_baseline 已移入闭源引擎 engine_core（几何走时差算法）。
# 此处仅通过 engine_runtime 调用，本文件不含公式。

def compute_delay_string(gen):
    """算 adelay 滤镜字符串。仅 tv 支持；其余返回 None。
    手动微调(delayManual)按声道叠加在自动基线之上；范围 0～200ms、步进 0.01ms。
    自动基线以最远声道为 0ms 基准、其余为正；手动微调只加正延迟，
    故总延迟恒 >= 0，所见即所得（滑块数值即最终延迟），无需归一化。
    返回 None = 无延迟（delayOn=false 或 tv 且总延迟全 0），wrapper 跳过、不强制重编码。"""
    if not (gen or {}).get('delayOn'):
        return None
    sys_t = (gen or {}).get('sys', 'tv')
    if sys_t != 'tv':
        return None  # 5.1/2.1 延时后续开放
    chans = DELAY_CHANNELS.get(sys_t, ['FL', 'FR'])
    auto = engine_runtime.auto_baseline(gen)
    man = gen.get('delayManual') or {}
    try:
        total = [round(float(auto.get(c, 0)) + float(man.get(c, 0)), 2) for c in chans]
    except (TypeError, ValueError):
        return None
    if all(t == 0 for t in total):
        return None  # 无实际延迟不写文件，避免误触发重编码
    return "adelay=delays=" + "|".join(str(t) for t in total)


def write_delay(s):
    """写 peq_delay.txt。s 为 None 或空字符串时清空（=无延迟）。"""
    open(PEQ_DELAY, 'w').write((s or '').strip() + "\n")


def compute_balance_string(gen):
    """算左右平衡增益串，写入 peq_balance.txt（内容 "gL|gR"，0~1）。
    居中(balance=0)时 gL=gR=1（不改动）；左偏(b<0)右声道衰减，右偏(b>0)左声道衰减。
    balance 范围 -100~+100（前端滑块），0=居中。返回 None = 不启用（=不写文件，避免无谓重编码）。"""
    try:
        b = float((gen or {}).get('balance', 0) or 0)
    except (TypeError, ValueError):
        b = 0
    b = max(-100.0, min(100.0, b)) / 100.0   # 归一化到 -1..1
    if abs(b) < 0.005:
        return None   # 居中：不启用平衡（走透传，不强制重编码）
    gL = round(1.0 - max(0.0, b), 4)
    gR = round(1.0 - max(0.0, -b), 4)
    return "%.4f|%.4f" % (gL, gR)


def write_balance(s):
    """写 peq_balance.txt。s 为 None 或空字符串时清空（=无平衡）。"""
    open(PEQ_BALANCE, 'w').write((s or '').strip() + "\n")


def compute_surround_delay_string(gen):
    """环绕固定延时（毫秒），写入 peq_sdelay.txt（内容单个数字）。
    0 或空 = 不启用（不写文件，避免无谓重编码）。wrapper 在多声道输入下把它作用于
    5.1/7.1 的环绕声道（下混前），立体声输入无意义（无环绕声道）。"""
    try:
        s = float((gen or {}).get('surroundDelay', 0) or 0)
    except (TypeError, ValueError):
        s = 0
    if s <= 0:
        return None
    s = max(0.0, min(200.0, s))
    return "%.1f" % s


def write_surround_delay(s):
    """写 peq_sdelay.txt。s 为 None 或空字符串时清空（=无环绕延时）。"""
    open(PEQ_SDELAY, 'w').write((s or '').strip() + "\n")


# ============ Jellyfin 连接（用于「应用」）============
def read_jellyfin_cfg():
    p = os.environ.get("JELLYFIN_CFG", "/opt/spacemaster/jellyfin.json")
    try:
        return json.load(open(p))
    except Exception:
        return {}


def write_jellyfin_cfg(cfg):
    p = os.environ.get("JELLYFIN_CFG", "/opt/spacemaster/jellyfin.json")
    try:
        json.dump(cfg, open(p, "w"))
    except Exception:
        pass


def _jf_req(cfg, path, method="GET", body=None):
    """直连 Jellyfin：绕过系统代理（避免 localhost 走 http_proxy），带 api_key。"""
    base = (cfg.get("url") or "").rstrip("/")
    key = cfg.get("key") or ""
    sep = "&" if "?" in path else "?"
    url = base + path + sep + "api_key=" + urllib.parse.quote(key, safe="")
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as r:
        txt = r.read().decode("utf-8", "replace")
        try:
            return json.loads(txt) if txt else {}
        except Exception:
            return {}


def clear_transcode_cache(cfg=None):
    """清 Jellyfin 转码缓存，强制下次播放按新 peq_af.txt 重新转码。
    优先用 Jellyfin 配置里的转码目录，否则尝试常见路径 / 环境变量 TRANSCODE_DIR。"""
    dirs = []
    if cfg:
        try:
            sc = _jf_req(cfg, "/System/Configuration")
            tp = sc.get("TranscodingTempPath") or sc.get("TranscodedPath") or ""
            if tp:
                dirs.append(tp)
        except Exception:
            pass
    env = os.environ.get("TRANSCODE_DIR", "")
    if env:
        dirs += [x.strip() for x in env.split(",") if x.strip()]
    if not dirs:
        dirs = ["/var/lib/jellyfin/transcodes", "/var/cache/jellyfin/transcodes",
                "/config/transcodes", "/cache/transcodes", "/opt/spacemaster/transcodes"]
    cleared = []
    for d in dirs:
        if d and os.path.isdir(d):
            try:
                for name in os.listdir(d):
                    p = os.path.join(d, name)
                    if os.path.isfile(p) or os.path.islink(p):
                        os.remove(p)
                    elif os.path.isdir(p):
                        shutil.rmtree(p)
                cleared.append(d)
            except Exception:
                pass
    # 容器内转码缓存：peq_toggle.py 在宿主机跑，但 Jellyfin 在 Docker 容器内，
    # 转码缓存在容器 /cache/transcodes，宿主机 os.path.isdir 判断不到 → 必须用 docker exec 清。
    # 不清的话 Stop+Play 后 Jellyfin 复用旧 HLS 段，新 peq_af.txt 不生效。
    try:
        import subprocess
        cid = subprocess.check_output(
            ["docker", "ps", "--filter", "name=jellyfin", "--format", "{{.ID}}"],
            stderr=subprocess.DEVNULL).decode().strip().split("\n")[0].strip()
        if cid:
            subprocess.call(["docker", "exec", cid, "sh", "-c",
                "rm -rf /cache/transcodes/* /transcodes/* 2>/dev/null; true"],
                stderr=subprocess.DEVNULL)
            cleared.append("docker:%s:/cache/transcodes" % cid)
    except Exception:
        pass
    return cleared


def _jellyfin_direct_url(cfg):
    """proxy 在 8098，Jellyfin 直连在 8097（docker 把容器 8096 映射到宿主 8097）。
    发 DELETE 等控制类请求直连 Jellyfin，避开 8098 proxy 的路径差异。"""
    import re
    u = (cfg.get("url") or "").strip()
    if not u:
        return u
    m = re.match(r'^(https?://[^:/]+):(\d+)(/.*)?$', u)
    if m:
        return '%s:8097%s' % (m.group(1), m.group(3) or '')
    return u


def _clear_full_transcode_dir():
    """删整个 Jellyfin 转码目录（含 master.m3u8 / main.m3u8 / *.ts）。
    关键：只删 .ts 不够 —— Jellyfin 内存转码会话还在时，TV 拿旧 playlist 去重试 .ts →
    ExoPlayer 走"段失败退避重试"路径（卡帧不黑屏）。删整个目录 → TV 重新请求 playlist
    拿不到任何文件 → 重新加载 manifest（黑屏）→ Jellyfin 全新转码（重读新 peq_af.txt）。"""
    try:
        cid = _sp.check_output(
            ["docker", "ps", "--filter", "name=jellyfin", "--format", "{{.ID}}"],
            stderr=_sp.DEVNULL).decode().strip().split("\n")[0].strip()
        if cid:
            _sp.call(["docker", "exec", cid, "sh", "-c",
                      "rm -rf /cache/transcodes/* 2>/dev/null; true"],
                     stderr=_sp.DEVNULL)
            return "docker:%s:/cache/transcodes" % cid
    except Exception:
        pass
    return ""


def _kill_ffmpeg(cid):
    """容器内杀残留 ffmpeg：Jellyfin 镜像里没有 pkill，改用 pgrep 取 PID 再 kill -9。"""
    if not cid:
        return
    try:
        _sp.check_call(["docker", "exec", cid, "sh", "-c",
                        "for p in $(pgrep -f ffmpeg 2>/dev/null); do kill -9 $p 2>/dev/null; done; true"],
                       stderr=_sp.DEVNULL)
        print("[replay] 已 kill 残留 ffmpeg", flush=True)
    except Exception:
        pass


def _jf_delete_active_encoding(cfg, dev, pses):
    """直连 Jellyfin(8097) 发 DELETE /Videos/ActiveEncodings 拆当前转码会话。返回 HTTP 码。
    绕过 8098 proxy（proxy 只透传，但控制类请求直连更稳）。实测返回 204=干净拆流成功。"""
    import re
    u = (cfg.get("url") or "").strip()
    m = re.match(r'^(https?://[^:/]+):(\d+)(/.*)?$', u)
    base = '%s:8097%s' % (m.group(1), m.group(3) or '') if m else u
    key = cfg.get("key") or ""
    path = "/Videos/ActiveEncodings?DeviceId=%s&PlaySessionId=%s" % (
        urllib.parse.quote(dev), urllib.parse.quote(pses))
    sep = "&" if "?" in path else "?"
    url = base + path + sep + "api_key=" + urllib.parse.quote(key, safe="")
    req = urllib.request.Request(url, method="DELETE", headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as r:
            return r.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def _jellyfin_restart_reload(cfg, clear_cache=True):
    """温和重载 EQ —— 不重启整个 Jellyfin 容器（2026-07-20 改）。

    原理：wrapper 在每个 ffmpeg 启动时热读 /opt/spacemaster/peq_af.txt / peq_sdelay.txt
    等全部 EQ 参数，所以只要让 Jellyfin 对当前播放「重新起一个 ffmpeg」，新 EQ 即生效。
    无需重启容器，只需「拆掉当前转码会话 + 清掉已生成的旧分片缓存」：

      1) DELETE /Videos/ActiveEncodings（官方干净拆流端点，直连 8097 实测 204）：
         拆除当前转码会话（内存 manifest 失效），客户端(HLS)命中缺失段会自动重新
         请求 manifest → Jellyfin 全新转码 → 重读新 EQ。
      2) 清 /cache/transcodes/*（删旧 .ts，逼 Jellyfin 在客户端下次请求时重新生成分片）。
      3) pgrep+kill 杀残留 ffmpeg（容器内无 pkill，用 pgrep 取 PID 再 kill -9）。

    相比旧方案（docker restart 整个容器，10~20s 卡顿+被踢出登录页），本方案：
      - 不重启容器，Jellyfin 主进程 / 其他会话 / ws 连接全不受影响；
      - 客户端留在播放器内，仅一次极短重协商（约 1~5s），无黑屏或秒切；
      - 若个别客户端未自动续播，拖一下进度条即秒切（与旧方案等效，但不打断服务器）。"""
    # 解析容器
    try:
        cid = _sp.check_output(
            ["docker", "ps", "--filter", "name=jellyfin", "--format", "{{.ID}}"],
            stderr=_sp.DEVNULL).decode().strip().split("\n")[0].strip()
    except Exception:
        cid = ""
    if not cid:
        cid = cfg.get("container") or "jellyfin-sm"
    # 1) 拆当前转码会话（直连 8097，绕过 8098 proxy）
    try:
        sessions = _jf_req(cfg, "/Sessions")
    except Exception:
        sessions = []
    cur = next((s for s in (sessions or []) if s.get("NowPlayingItem") and s.get("IsActive")), None)
    dev = pses = ""
    if cur:
        ps = cur.get("PlayState") or {}
        dev = cur.get("DeviceId") or ""
        pses = cur.get("PlaySessionId") or ps.get("PlaySessionId") or ""
    if dev and pses:
        code = _jf_delete_active_encoding(cfg, dev, pses)
        print("[replay] DELETE ActiveEncodings 已发 (code=%s, 拆旧转码会话)" % code, flush=True)
    else:
        print("[replay] 未取到活跃会话 DeviceId/PlaySessionId，跳过 DELETE", flush=True)
    # 等 Jellyfin 完成拆流（避免我们清缓存时它还在写）
    time.sleep(1.0)
    # 2) 清转码缓存（逼重新生成分片，带新 EQ）
    if clear_cache and cid:
        try:
            _sp.call(["docker", "exec", cid, "sh", "-c",
                      "rm -rf /cache/transcodes/* 2>/dev/null; true"],
                     stderr=_sp.DEVNULL)
            print("[replay] 已清 /cache/transcodes/*（旧分片移除）", flush=True)
        except Exception:
            pass
    # 3) 杀残留 ffmpeg（容器内无 pkill，用 pgrep+kill）
    _kill_ffmpeg(cid)
    return True, "已温和重载（拆旧转码+清缓存，不重启容器）；客户端续播即从新位置应用新 EQ"


def _jellyfin_seek_reload(cfg, session):
    """可控客户端(Web/Jellyfin Media Player)：远程发 Seek（=程序化"拖一下进度条"），
    触发 Jellyfin 重启当前转码——新 ffmpeg 启动时热读新 peq_af.txt / peq_sdelay.txt 等
    全部 EQ 参数，客户端留在播放器内、约 1~2s 内无缝续播、不打断服务器、不影响其他会话。
    不做 rm -rf /cache/transcodes/* 或 kill ffmpeg：那些会留"陈旧 manifest 指向已删分片"
    → 反复 404 → 卡→拖条续一会→又 404 的死循环（TV 端 2026-07-20 实测确认）。"""
    sid = session.get('Id') or ''
    pos = (session.get('PlayState') or {}).get('PositionTicks') or 0
    new_pos = pos + 10000000  # 向前 1s（10^7 ticks）：必须真移动才触发重转码
    body = {"Command": "Seek", "SeekPositionTicks": new_pos}
    try:
        _jf_req(cfg, "/Sessions/%s/Playing/PlayState" % urllib.parse.quote(sid, safe=""),
                method="POST", body=body)
        print("[replay] 已远程 Seek(+1s) 触发新转码，重读新 EQ", flush=True)
        return True, "已远程重载（Web 端 seek 触发新转码，重读新 EQ）；约 1~2s 内无缝续播"
    except Exception as e:
        print("[replay] Seek 失败 %s，回退温和拆流" % e, flush=True)
        return _jellyfin_restart_reload(cfg, clear_cache=True)


def replay_active_session(cfg, clear_cache=True):
    """应用音效后让客户端重新加载（拿到新 EQ/延时）。按客户端能力分两条路：

    ① 可控 Web 客户端（Jellyfin Web / Media Player，SupportsMediaControl=true）：
       服务端先拆旧转码（DELETE+清缓存+pkill），再推 **Playstate Seek**（= 程序化
       "拖一下进度条"）。客户端留在播放器内不退出，seek 触发 manifest 重新加载 →
       Jellyfin 全新转码（重读新 peq_af.txt）→ 约 1~2s 内秒切，无黑屏/无错位/不卡死。
       ⚠️ 不用 Stop+Play：Stop 会让 Web 客户端退出播放器回到详情页，且紧随其后的 Play
           常被丢弃（客户端处于退出动画中）→ 表现就是"被踢回介绍页、不自动续播"。

    ② 不听命令的客户端（Android TV 等 SupportsMediaControl=false）：
       无法远程控制 → 保留原暴力拆流（DELETE+删目录+pkill），靠客户端 HLS 缓冲耗尽后
       重新加载 manifest（黑屏/被动），或用户手动拖进度条秒切。
    """
    if not (cfg.get('url') and cfg.get('key')):
        return False, "未配置 Jellyfin，无法重载（文件已写，播放时生效）", "tv"
    try:
        sessions = _jf_req(cfg, "/Sessions")
    except Exception as e:
        return False, ("无法连接 Jellyfin: %s" % e), "tv"
    cur = None
    cur_web = None
    for s in (sessions or []):
        if s.get('NowPlayingItem') and s.get('IsActive'):
            if cur is None:
                cur = s
            # 多设备同时播放时，优先选可控 Web 客户端
            if s.get('SupportsMediaControl') and s.get('Id'):
                cur_web = s
    if cur_web:
        cur = cur_web
    if not cur:
        return False, "当前没有正在播放的内容（文件已写，播放时生效）", "tv"
    session_id = cur.get('Id') or ''
    device_id = cur.get('DeviceId', '') or ''
    client = cur.get('Client', '') or ''
    play_state = cur.get('PlayState') or {}
    play_session = cur.get('PlaySessionId') or play_state.get('PlaySessionId') or ''
    pos = play_state.get('PositionTicks') or 0
    item = cur.get('NowPlayingItem') or {}
    item_id = item.get('Id', '') or ''
    audio_idx = play_state.get('AudioStreamIndex')
    sub_idx = play_state.get('SubtitleStreamIndex')
    media_src = ''
    ms = item.get('MediaSources') or []
    if ms:
        media_src = (ms[0] or {}).get('Id', '') or ''
    # 按客户端能力选择重载方式（详见 _jellyfin_seek_reload / TV 分支注释）：
    if cur.get('SupportsMediaControl') and cur.get('Id'):
        # ① 可控 Web 客户端：远程 Seek 无缝重载（不拆流）
        ok, msg = _jellyfin_seek_reload(cfg, cur)
        return (ok, msg, "web")
    # ② TV / 不可控客户端：不做任何拆流，提示用户拖进度条
    return (True,
            "EQ 已保存。TV 端请在播放器里把进度条拖动 1~2 秒即可听到新效果"
            "（TV 应用不支持远程重载；此方式不打断播放、不会反复卡）。",
            "tv")


# ============ 重载状态管理（按钮触发，DELETE+删目录 黑屏重载）============
# 设计变更（2026-07-18）：Android TV SupportsMediaControl=false，
# Stop/Play 远程命令对 TV 完全无效 → 旧 Stop+Play 方案 ffmpeg 永不重启 → 新 EQ 不生效。
# 修正（2026-07-18 中午）：之前误判 DELETE ActiveEncodings 会 404 不重启，实际它返回 204
# = 干净拆除转码会话（杀 ffmpeg+清内存+manifest失效）。配合删整个转码目录，
# TV 重新加载 manifest = 黑屏（与 Jellyfin 改画质同内核），全新转码重读新 peq_af.txt。
# 状态流转：idle → loading(正在重载音效) → loaded(新音效已生效) / error
import subprocess as _sp

_replay_state = {"status": "idle", "msg": "", "started_at": 0.0}
_replay_lock = threading.Lock()


def _detect_transcode_started(cfg, timeout=90, grace=6.0):
    """检测新 ffmpeg 进程启动（拆旧转码后 Jellyfin 重启 = 新 EQ 生效）。
    grace=检测到新进程后额外等待秒数：TV 需 6s 黑屏切流；Web 秒切只需 1s。"""
    try:
        cid = _sp.check_output(
            ["docker", "ps", "--filter", "name=jellyfin", "--format", "{{.ID}}"],
            stderr=_sp.DEVNULL).decode().strip().split("\n")[0].strip()
        if not cid:
            return False
        # 先等 ffmpeg 死透（pkill -9 / DELETE 后进程不会立刻消失）
        time.sleep(1.5)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                out = _sp.check_output(
                    ["docker", "exec", cid, "sh", "-c", "pgrep -c ffmpeg 2>/dev/null || echo 0"],
                    stderr=_sp.DEVNULL).decode().strip()
                count = int(out) if out.isdigit() else 0
                if count > 0:
                    print("[replay] 新 ffmpeg 进程检测到 (count=%d)，等待切流 grace=%.1fs" % (count, grace), flush=True)
                    time.sleep(grace)
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _do_replay(cfg):
    """后台线程：温和重载 —— 拆当前转码会话 + 清缓存 → 客户端重协商 → 全新转码(重读新 EQ)。
    不重启容器，客户端留在播放器内。"""
    t0 = time.time()
    with _replay_lock:
        _replay_state["status"] = "loading"
        _replay_state["msg"] = "正在重载音效（不重启容器）"
        _replay_state["started_at"] = time.time()
    print("[replay] 开始 (t=%.1fs)" % (time.time() - t0), flush=True)
    res = replay_active_session(cfg, clear_cache=True)
    if isinstance(res, tuple) and len(res) == 3:
        ok, msg, kind = res
    else:
        ok, msg = res
        kind = "gentle"
    if not ok:
        with _replay_lock:
            _replay_state["status"] = "error"
            _replay_state["msg"] = msg
        print("[replay] 失败: %s (t=%.1fs)" % (msg, time.time() - t0), flush=True)
        return
    print("[replay] 已触发温和重载(kind=%s) (t=%.1fs)" % (kind, time.time() - t0), flush=True)
    # 温和重载约 1~5s 生效，不在此阻塞等待；前端提示"客户端续播即生效"
    with _replay_lock:
        _replay_state["status"] = "loaded"
        _replay_state["msg"] = msg + "（客户端续播即生效，约几秒）"
    print("[replay] 完成 kind=%s (t=%.1fs)" % (kind, time.time() - t0), flush=True)


def trigger_replay(cfg):
    """触发重载（非阻塞，后台线程执行 kill ffmpeg）。返回是否已启动。"""
    if not (cfg.get('url') and cfg.get('key')):
        return False, "未配置 Jellyfin 连接"
    with _replay_lock:
        if _replay_state["status"] == "loading":
            # 陈旧保护：若上一次 loading 起始已超 120s 仍没结束，视为卡死，允许重新触发，
            # 避免"应用"被永久阻塞（如 Web 端 Play 未自动续播导致检测空等）。
            if time.time() - _replay_state.get("started_at", 0) < 120:
                return False, "正在重载中，请稍候"
    threading.Thread(target=_do_replay, args=(cfg,), daemon=True).start()
    return True, "已触发重播"


_DOUBLE_EQ_CACHE = {"ts": 0.0, "data": None}


def _detect_double_eq_risk(cfg, use_cache=True):
    """检测当前是否有 Windows 类客户端正在串流 NAS（即服务端已烤音）。

    若命中，意味着该用户若在 Windows 本地再开『空间大师 Win 版』会双重 EQ，
    需在控制台提醒。纯只读、非破坏性，带 10s 缓存避免频繁打 Jellyfin。
    """
    now = time.time()
    if use_cache and _DOUBLE_EQ_CACHE["data"] is not None and (now - _DOUBLE_EQ_CACHE["ts"] < 10):
        return _DOUBLE_EQ_CACHE["data"]
    result = {"windows_streaming": [], "advice": ""}
    try:
        if not (cfg.get('url') and cfg.get('key')):
            raise RuntimeError("no cfg")
        sessions = _jf_req(cfg, "/Sessions")
        for s in (sessions or []):
            if not (s.get('NowPlayingItem') and s.get('IsActive')):
                continue
            client = (s.get('Client') or '').strip()
            os_name = (s.get('OperatingSystem') or '').strip().lower()
            device = (s.get('DeviceName') or '').strip().lower()
            user = (s.get('UserName') or '').strip()
            is_win = False
            if client in ("Jellyfin Web", "Jellyfin Media Player", "Jellyfin Theater"):
                is_win = True
            if "windows" in os_name or "windows" in device:
                is_win = True
            if not is_win:
                continue
            result["windows_streaming"].append({
                "client": client or "未知客户端",
                "device": s.get('DeviceName') or "未知设备",
                "user": user or "未知用户",
            })
    except Exception:
        # 检测失败不阻塞：返回空（视为无风险），不影响控制台其他功能
        result = {"windows_streaming": [], "advice": ""}
    if result["windows_streaming"]:
        names = "、".join("%s（%s）" % (c["client"], c["device"]) for c in result["windows_streaming"])
        result["advice"] = (
            "检测到 Windows 客户端正在串流 NAS 音效（%s）。"
            "NAS 已在服务端把音效烤进音频，请勿在该 Windows 电脑上再开启「空间大师 Win 版」"
            "（CamillaDSP / Equalizer APO），否则同一路声音会被加两次 EQ，导致双重音效、发闷发刺。"
            "Win 版仅用于「Windows 电脑本地直接播放影片」的场景。" % names
        )
    _DOUBLE_EQ_CACHE["ts"] = time.time()
    _DOUBLE_EQ_CACHE["data"] = result
    return result


def get_replay_state():
    with _replay_lock:
        state = dict(_replay_state)
    try:
        cfg = read_jellyfin_cfg()
        eq = _detect_double_eq_risk(cfg)
    except Exception:
        eq = {"windows_streaming": [], "advice": ""}
    state["windows_streaming"] = eq["windows_streaming"]
    state["double_eq_advice"] = eq["advice"]
    return state


HTML_PAGE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>空间大师 · 控制台 (8777)</title>
<style>
  :root{--bg:#0f1419;--card:#1a212b;--ink:#e6edf3;--mut:#8b97a6;--acc:#3fb950;--off:#888780;--low:#f0883e;--mid:#58a6ff;--hi:#bc8cff;--comp:#3fb950;--warn:#d29922}
  *{box-sizing:border-box}
  body{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:20px;max-width:880px;margin:20px auto}
  h1{font-size:20px;margin:0 0 2px}
  .sub{color:var(--mut);font-size:12px;margin-bottom:14px}
  .card{background:var(--card);border-radius:12px;padding:16px;margin-bottom:14px}
  .row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
  label{font-size:12px;color:var(--mut);display:block;margin-bottom:4px}
  input[type=number]{width:84px;padding:8px;border-radius:8px;border:1px solid #2d3744;background:#0d1117;color:var(--ink);font-size:15px}
  select{width:280px;padding:8px;border-radius:8px;border:1px solid #2d3744;background:#0d1117;color:var(--ink);font-size:14px}
  .slider{margin:14px 0}
  .slider .lab{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px}
  .slider .val{color:var(--acc);font-variant-numeric:tabular-nums}
  input[type=range]{width:100%;accent-color:var(--acc)}
  .bass input[type=range]{accent-color:var(--low)}
  .mid input[type=range]{accent-color:var(--mid)}
  .hi input[type=range]{accent-color:var(--hi)}
  svg{width:100%;height:200px;background:#0d1117;border-radius:8px;display:block}
  .seg{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--mut);line-height:1.7;max-height:170px;overflow:auto;white-space:pre-wrap}
  textarea{width:100%;height:70px;background:#0d1117;color:var(--acc);border:1px solid #2d3744;border-radius:8px;padding:10px;font-family:ui-monospace,monospace;font-size:11px}
  button{background:var(--acc);color:#04210d;border:none;padding:9px 16px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;margin:4px 6px 4px 0}
  button.sec{background:#21262d;color:var(--ink)}
  button.off{background:var(--off);color:#fff}
  .toggle{position:relative;width:52px;height:28px;border-radius:14px;cursor:pointer;transition:background .2s;flex:0 0 auto}
  .toggle .knob{position:absolute;top:3px;width:22px;height:22px;border-radius:50%;background:#fff;transition:left .2s;box-shadow:0 1px 3px rgba(0,0,0,.4)}
  .toggle.on{background:var(--acc)}
  .toggle.off{background:var(--off)}
  .toggle.on .knob{left:3px}
  .toggle.off .knob{left:27px}
  .note{color:var(--mut);font-size:11px;margin-top:8px;line-height:1.5}
  .pill{display:inline-block;padding:3px 12px;border-radius:20px;font-size:13px;font-weight:600;background:#21262d}
  .pill.on{background:var(--acc);color:#04210d}
  .pill.off{background:var(--off);color:#fff}
  .badge{font-size:11px;color:var(--mut);margin-left:8px}
  .err{color:#f85149;font-size:12px;margin-top:6px;min-height:14px}
  .canvas-wrap{background:#0d1320;border:1px solid #2a3a52;border-radius:8px;padding:6px;margin:6px 0}
  .canvas-wrap canvas{width:100%;height:auto;display:block;border-radius:4px;cursor:crosshair;touch-action:none}
  .canvas-title{font-size:13px;margin:12px 0 2px;color:var(--ink)}
</style>
</head>
<body>
<h1>空间大师 · 控制台</h1>
<div class="sub">端口 8777 · PEQ 开关 + 调整页面。拖动滑块后点「应用」按钮才会重载新音效（强制 kill 转码进程让 Jellyfin 用新 EQ 重新转码），改动由 wrapper 热读，无需重启 Jellyfin。</div>

<div id="eqWarn" style="display:none;margin:0 0 14px;padding:12px 16px;border-radius:12px;background:rgba(248,81,73,.12);border:1px solid #f85149;color:#ffb4ab;font-size:13px;line-height:1.7">
  <b style="color:#f85149">⚠ 双重 EQ 提醒</b> <span id="eqWarnMsg"></span>
</div>

<div class="card">
  <div style="display:flex;align-items:center;gap:14px">
    <span>PEQ 开关：</span>
    <div class="toggle off" id="toggle" title="点击切换 开/关"><div class="knob"></div></div>
    <span class="badge" id="statusBands"></span>
  </div>
  <div class="err" id="msg"></div>
</div>

<div class="card">
  <h3 style="margin-top:0">Jellyfin 连接（用于「应用」）</h3>
  <div class="row">
    <div style="flex:1"><label>服务器地址</label><input id="jfUrl" type="text" placeholder="http://127.0.0.1:8098" style="width:100%"></div>
  </div>
  <div style="margin-top:10px"><label>API Key</label><input id="jfKey" type="password" placeholder="Jellyfin 后台 → 控制台 → API 密钥 生成" style="width:100%"></div>
  <div style="margin-top:10px"><button class="sec" id="jfSave">保存连接</button><span class="badge" id="jfState"></span></div>
  <div class="note">API Key 仅存于本机 /opt/spacemaster/jellyfin.json，不上传任何地方。</div>
</div>

<div class="card">
  <h3 style="margin-top:0">应用</h3>
  <div class="note">调好滑块后点此按钮，会重启 Jellyfin 容器强制重载正在播放的片源音效（约 10~20 秒后客户端自动重连续播、新音效生效）。拖动滑块只更新曲线不重载。需先配置上方 Jellyfin 连接，且当前正在播放。</div>
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
    <button id="applyCurrentBtn">应用</button>
    <span id="pendingBadge" style="display:none;color:var(--warn);font-size:13px;font-weight:600">● 有未应用的更改</span>
  </div>
  <div class="err" id="acMsg"></div>
  <div style="font-size:13px;margin-top:6px;min-height:18px" id="replayStatus"></div>
</div>

<div class="card">
  <div class="row">
    <div><label>房间长 L (m)</label><input id="L" type="number" step="0.1" value="3.1"></div>
    <div><label>房间宽 W (m)</label><input id="W" type="number" step="0.1" value="4.4"></div>
    <div><label>房间高 H (m)</label><input id="H" type="number" step="0.1" value="2.8"></div>
  </div>
  <div style="margin-top:12px">
    <label>系统类型</label>
    <select id="sys">
      <option value="ht21">家庭影院 2.1（独立音箱+低音炮）</option>
      <option value="ht51">家庭影院 5.1</option>
      <option value="tv" selected>电视机 / 电脑 自带音箱</option>
    </select>
  </div>
  <div class="slider bass"><div class="lab"><span>低音</span><span class="val" id="lowV">0.0 dB</span></div>
    <input id="low" type="range" min="-10" max="10" step="0.5" value="0"></div>
  <div class="slider mid">    <div class="lab"><span>中音</span><span class="val" id="midV">0.0 dB</span></div>
    <input id="mid" type="range" min="-10" max="10" step="0.1" value="0"></div>
  <div class="slider hi"><div class="lab"><span>高音</span><span class="val" id="hiV">1.5 dB</span></div>
    <input id="hi" type="range" min="-10" max="10" step="0.1" value="1.5"></div>
</div>

<div class="card">
  <label>均衡响应曲线（频响 · dB）</label>
  <svg id="viz" viewBox="0 0 820 220" style="width:100%;height:auto;display:block"></svg>
</div>

<div class="card">
  <h3 style="margin-top:0">延时校准（电视内置音箱）</h3>
  <div class="note">按声学几何自动算各声道到聆听位的走时差（亚毫秒级，已足够，一般无需手调）+ 每声道手动微调：范围 0～10ms、步进 0.01ms，在参考值之上再加正延迟。最远喇叭自动为 0ms 基准、其余声道加正延迟对齐；手动微调只能往大调（不能提前）。<b>⚠ 手动微调超过约 5ms 会产生明显回音（梳状滤波）</b>——自动几何已按声学算好，正常不用动它。<b>「聆听距离」由你输入固定值、图上不可拖拽</b>；平面图上左右拖动皇帝位改变水平偏移时，下方左右声道延时<b>滑动块会实时联动</b>（滑块左端对齐自动基线、位置=总延迟），你只是在这条联动数据之上再手动加延迟，这样微调才有意义。想确认音效是否真的生效，请用下方「左右平衡」滑块。</div>
  <div style="margin:8px 0"><label style="display:inline-flex;align-items:center;gap:6px"><input type="checkbox" id="delayOn"> 启用延时</label></div>
  <div class="row" id="delayGeom">
    <div><label>电视宽度 (m)</label><input id="tvW" type="number" step="0.05" value="1.2"></div>
    <div><label>聆听距离·固定 (m)</label><input id="dist" type="number" step="0.1" value="2.5"></div>
    <div><label>水平偏移 (m)</label><input id="offX" type="number" step="0.05" value="0"></div>
    <div><label>电视中心高 (m)</label><input id="tvH" type="number" step="0.05" value="0.9"></div>
    <div><label>耳朵高度 (m)</label><input id="earH" type="number" step="0.05" value="1.1"></div>
  </div>
  <div class="err" id="delayErr"></div>
  <div class="canvas-title">平面图（拖动 👤 左右移动，改变水平偏移）</div>
  <div class="canvas-wrap"><canvas id="plan" width="640" height="300"></canvas></div>
  <div class="note" id="delayAuto">自动基线：FL 0.00 ms · FR 0.00 ms</div>
  <div id="delayManualWrap"></div>
</div>

<div class="card">
  <h3 style="margin-top:0">环绕固定延时（加强 tv 下混空间感）</h3>
  <div class="note">给 5.1 源的环绕声道（SL/SR，7.1 含 BL/BR）加一个固定延时：在「下混压成立体声」之前先把环绕推后几毫秒，折进 stereo 后让环绕信息听起来更靠后 / 更开阔，整体空间感更强。0 = 不延时（该级透传）。范围 0~200ms、步进 0.5ms。此延时独立于上方「左右声道几何延时」，两者可叠加。</div>
  <div class="slider">
    <div class="lab"><span>环绕固定延时</span><span class="val" id="sdVal">0.0 ms</span></div>
    <input id="surroundDelay" type="range" min="0" max="200" step="0.5" value="0">
  </div>
</div>

<div class="card">
  <h3 style="margin-top:0">左右平衡（验证音效是否生效）</h3>
  <div class="note">居中时左右声道音量一致；向左=左声道更响，向右=右声道更响。这个滑块用来<b>确认当前音效链是否真的在生效</b>：改完延时/EQ 后点「应用」重载，再拖动它，若能立刻听到声音左右偏移，说明新音效已加载；若毫无变化，说明还没重载成功（去电视上拖一下进度条强制刷新）。<b>平时保持居中即可，它不参与房间校正。</b></div>
  <div class="slider">
    <div class="lab"><span>左右平衡</span><span class="val" id="balVal">居中</span></div>
    <input id="balance" type="range" min="-100" max="100" step="1" value="0">
  </div>
</div>

<script>
const DELAY_CHANNELS = {tv:['FL','FR'], ht21:['FL','FR','LFE'], ht51:['FL','FR','FC','SL','SR','LFE']};
const CH_LABEL = {FL:'左声道', FR:'右声道', FC:'中置', SL:'左环绕', SR:'右环绕', LFE:'低音炮'};
let delayManualState = {FL:0, FR:0, FC:0, SL:0, SR:0, LFE:0};
// ===== 房间→PEQ / 几何→延时 算法已移入闭源引擎（engine_core / sm_dsp_engine）=====
// 前端只把原始输入发给 /api/compute，拿回算好的 bands + delays，绝不本地算公式。
let currentDelays = {};   // 来自引擎的每声道自动基线延时(ms)
async function computeAll(){
  const gen = genInputs();
  try{
    const r = await fetch('/api/compute', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(gen)});
    const j = await r.json();
    currentBands = Array.isArray(j.bands) ? j.bands : [];
    currentDelays = (j.delays && typeof j.delays==='object') ? j.delays : {};
    renderViz(currentBands);
    renderDelayReadout();
  }catch(e){ console.error('compute failed', e); }
}
// build() 保留为兼容名，内部走引擎计算
async function build(){ await computeAll(); }
function renderViz(bands){
  const W=820,H=220,m=34,fMin=20,fMax=20000,gMin=-12,gMax=14,Fs=48000;
  const lx=f=>(Math.log10(f)-Math.log10(fMin))/(Math.log10(fMax)-Math.log10(fMin))*(W-2*m)+m;
  const ly=g=>m+(gMax-g)/(gMax-gMin)*(H-2*m);
  // 构造每个 band 的 peaking biquad（与 ffmpeg equalizer t=q 一致）
  const bqs=bands.map(b=>{
    const A=Math.pow(10,b.g/40), w0=2*Math.PI*b.f/Fs, cw=Math.cos(w0), sw=Math.sin(w0);
    if(b.t==="lowshelf"){
      const S=b.Q, alpha=sw/2*Math.sqrt((A+1/A)*(1/S-1)+2), sq=2*Math.sqrt(A)*alpha;
      return {b0:A*((A+1)-(A-1)*cw+sq), b1:2*A*((A-1)-(A+1)*cw), b2:A*((A+1)-(A-1)*cw-sq),
              a0:(A+1)+(A-1)*cw+sq, a1:-2*((A-1)+(A+1)*cw), a2:(A+1)+(A-1)*cw-sq};
    }
    const al=sw/(2*b.Q);
    return {b0:1+al*A,b1:-2*cw,b2:1-al*A,a0:1+al/A,a1:-2*cw,a2:1-al/A};
  });
  function magDb(f){
    let lin=1; const w=2*Math.PI*f/Fs,cw=Math.cos(w),sw=Math.sin(w),c2=Math.cos(2*w),s2=Math.sin(2*w);
    for(const q of bqs){
      const nr=Math.hypot(q.b0+q.b1*cw+q.b2*c2, -(q.b1*sw+q.b2*s2));
      const dr=Math.hypot(q.a0+q.a1*cw+q.a2*c2, -(q.a1*sw+q.a2*s2));
      lin*=nr/dr;
    }
    return 20*Math.log10(lin);
  }
  let s='';
  [20,50,100,200,500,1000,2000,5000,10000,20000].forEach(f=>{
    const x=lx(f);
    s+='<line x1="'+x+'" y1="'+m+'" x2="'+x+'" y2="'+(H-m)+'" stroke="#1c2530"/>';
    s+='<text x="'+x+'" y="'+(H-10)+'" fill="#5b6675" font-size="10" text-anchor="middle">'+(f>=1000?(f/1000)+'k':f)+'</text>';
  });
  s+='<line x1="'+m+'" y1="'+ly(0)+'" x2="'+(W-m)+'" y2="'+ly(0)+'" stroke="#33404f" stroke-dasharray="4 3"/>';
  [6,-6].forEach(g=>{ s+='<line x1="'+m+'" y1="'+ly(g)+'" x2="'+(W-m)+'" y2="'+ly(g)+'" stroke="#202b38"/>'; });
  const N=240; let d='';
  for(let i=0;i<=N;i++){
    const fr=fMin*Math.pow(fMax/fMin,i/N), g=magDb(fr);
    const x=lx(fr), y=ly(Math.max(gMin,Math.min(gMax,g)));
    d+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1)+' ';
  }
  s+='<path d="'+d+'" fill="none" stroke="#3fb950" stroke-width="2"/>';
  viz.innerHTML=s;
}

function genInputs(){
  return {L:+L_.value,W:+W_.value,H:+H_.value,sys:sys_.value,low:+low_.value,mid:+mid_.value,hi:+hi_.value,
    delayOn:delayOn_.checked, tvW:+tvW_.value, dist:+dist_.value, offX:+offX_.value,
    tvH:+tvH_.value, earH:+earH_.value,
    balance:+balance_.value,
    surroundDelay:+surroundDelay_.value,
    delayManual:{...delayManualState}};
}
// 几何走时差已移入闭源引擎（auto_baseline）。前端用 currentDelays（来自 /api/compute）。

// ============ 平面图（电视内置音箱，移植自演示版 drawPlan；侧面图已按需求取消）============
function drawPlanTV(){
  const cv=plan_; if(!cv) return;
  const x=cv.getContext('2d'), W=cv.width, H=cv.height, pad=38;
  x.clearRect(0,0,W,H);
  if(sys_.value!=='tv'){
    x.fillStyle='#6f7d96'; x.font='13px sans-serif'; x.textAlign='center';
    x.fillText('仅电视内置/电脑自带音箱支持延时校准', W/2, H/2);
    return;
  }
  // 实际房间地面尺寸：W=前墙宽(电视所在墙)，L=进深(前→后)
  const roomW=Math.max(0.1,(+W_.value)||4.4);
  const roomD=Math.max(0.1,(+L_.value)||3.1);
  const tvW=+tvW_.value, dist=+dist_.value, offX=+offX_.value, tvH=+tvH_.value, earH=+earH_.value;
  // 统一比例尺(px/米)，保持房间真实长宽比，居中放置
  const ux=W-2*pad, uy=H-2*pad;
  const scale=Math.min(ux/roomW, uy/roomD);
  const dw=roomW*scale, dh=roomD*scale;
  const ox=pad+(ux-dw)/2, oy=pad+(uy-dh)/2;
  const PX=vx=>ox+(vx+roomW/2)/roomW*dw;   // 水平：-roomW/2..roomW/2（左负右正）
  const PY=vy=>oy+vy/roomD*dh;              // 深度：0(前墙)..roomD(后墙)
  x.fillStyle='#0d1320'; x.strokeStyle='#2a3a52'; x.lineWidth=2;
  x.fillRect(ox,oy,dw,dh); x.strokeRect(ox,oy,dw,dh);
  // 固定垂直距离线：皇帝位只能沿此线左右移动（dist=用户→电视正中心，由输入框固定）
  x.strokeStyle='rgba(245,200,66,.25)'; x.setLineDash([3,4]); x.beginPath(); x.moveTo(ox, PY(dist)); x.lineTo(ox+dw, PY(dist)); x.stroke(); x.setLineDash([]);
  x.fillStyle='rgba(94,156,255,.22)'; x.fillRect(PX(-tvW/2), oy+2, tvW/roomW*dw, 4);
  x.fillStyle='#6f7d96'; x.font='12px sans-serif'; x.textAlign='center';
  x.fillText('前墙 · 电视（房间 '+roomW.toFixed(1)+'×'+roomD.toFixed(1)+'m）', ox+dw/2, oy-12);
  x.fillText('后墙', ox+dw/2, oy+dh+16);
  x.textAlign='right'; x.fillText('左', ox-6, oy+dh/2);
  x.textAlign='left'; x.fillText('右', ox+dw+6, oy+dh/2);
  const spk=(pxv,pyv,c,lab)=>{ x.fillStyle=c; x.beginPath(); x.arc(PX(pxv),PY(pyv),6,0,7); x.fill(); x.fillStyle='#aab6cc'; x.font='11px sans-serif'; x.textAlign='center'; x.fillText(lab, PX(pxv), PY(pyv)-10); };
  spk(-tvW/2,0,'#5e9cff','L'); spk(tvW/2,0,'#5e9cff','R');
  const ex=PX(offX), ey=PY(dist);
  x.strokeStyle='#f5c842'; x.lineWidth=2; x.beginPath(); x.arc(ex,ey,14,0,7); x.stroke();
  x.fillStyle='#f5c842'; x.font='16px sans-serif'; x.textAlign='center'; x.textBaseline='middle'; x.fillText('👤', ex, ey);
  x.strokeStyle='rgba(240,200,66,.35)'; x.setLineDash([4,4]); x.font='10px sans-serif'; x.textBaseline='middle';
  [['L',[-tvW/2,0]],['R',[tvW/2,0]]].forEach(([lab,p])=>{
    const sx=PX(p[0]), sy=PY(p[1]);
    x.beginPath(); x.moveTo(ex,ey); x.lineTo(sx,sy); x.stroke();
    const d=Math.sqrt((p[0]-offX)**2 + (p[1]-dist)**2 + (tvH-earH)**2);
    const mx=ex+(sx-ex)*0.6, my=ey+(sy-ey)*0.6, t=d.toFixed(2)+'m';
    const tw=x.measureText(t).width;
    x.fillStyle='rgba(13,19,32,.85)'; x.fillRect(mx-tw/2-3,my-7,tw+6,14);
    x.fillStyle='#f5c842'; x.fillText(t,mx,my);
  });
  x.setLineDash([]); x.textBaseline='alphabetic'; x.textAlign='center';
}
function redrawCanvases(){ drawPlanTV(); }
function setupDrag(cv, onMove){
  if(!cv) return;
  const toCanvas=e=>{ const r=cv.getBoundingClientRect(); return [(e.clientX-r.left)/r.width*cv.width, (e.clientY-r.top)/r.height*cv.height]; };
  let drag=false;
  cv.addEventListener('pointerdown', e=>{ drag=true; try{cv.setPointerCapture(e.pointerId);}catch(_){} onMove(toCanvas(e)); });
  cv.addEventListener('pointermove', e=>{ if(drag) onMove(toCanvas(e)); });
  cv.addEventListener('pointerup', ()=>{ drag=false; });
  cv.addEventListener('pointercancel', ()=>{ drag=false; });
}
function renderDelayManual(){
  const sys=sys_.value;
  const wrap=delayManualWrap_;
  wrap.innerHTML='';
  if(sys!=='tv'){
    const div=document.createElement('div');
    div.className='note'; div.style.marginTop='8px';
    div.textContent='5.1 / 2.1 延时校准后续开放，当前仅电视内置/电脑自带音箱支持。';
    wrap.appendChild(div);
    return;
  }
  DELAY_CHANNELS[sys].forEach(c=>{
    if(delayManualState[c]==null || delayManualState[c]<0) delayManualState[c]=0;
    const auto=currentDelays;
    const total=auto[c]+delayManualState[c];   // 滑块初始=总延迟(联动基线+手动)
    const div=document.createElement('div');
    div.className='slider';
    div.innerHTML='<div class="lab"><span>'+CH_LABEL[c]+' 延时 ('+c+')</span><span class="val" id="dV_'+c+'">'+total.toFixed(2)+' ms</span></div>'+
      '<input id="d_'+c+'" type="range" min="'+auto[c].toFixed(2)+'" max="'+(auto[c]+10).toFixed(2)+'" step="0.01" value="'+total.toFixed(2)+'">';
    wrap.appendChild(div);
    const inp=div.querySelector('input');
    inp.addEventListener('input', ()=>{
      // 滑块=总延迟；手动微调=总延迟−联动基线(自动基线)，只能往大调(>=0)
      const a=(currentDelays[c]||0);
      delayManualState[c]=Math.max(0, +inp.value - a);
      const lbl=document.getElementById('dV_'+c);
      if(lbl) lbl.textContent=(+inp.value).toFixed(2)+' ms';
      onDelayInput();
    });
  });
}
function renderDelayReadout(){
  const sys=sys_.value;
  const chans=(sys==='tv')?DELAY_CHANNELS.tv:[];
  const auto=currentDelays;
  chans.forEach(c=>{
    const total=auto[c]+(delayManualState[c]||0);   // 总延迟=联动基线+手动微调
    const lbl=document.getElementById('dV_'+c);
    const inp=document.getElementById('d_'+c);
    if(inp){  // 滑块实时联动：左端对齐自动基线，位置=总延迟（随平面图左右拖动变化）
      inp.min=auto[c].toFixed(2);
      inp.max=(auto[c]+10).toFixed(2);
      inp.value=total.toFixed(2);
    }
    const _manual=delayManualState[c]||0;
    if(lbl) lbl.textContent=total.toFixed(2)+' ms'+(_manual>5?' ⚠回音风险':'');
  });
  if(chans.length){
    delayAuto_.textContent='几何参考值（含电视中心与耳朵高度差，随左右位置变化，可在此基础上手动微调）：'+chans.map(c=>c+' '+auto[c].toFixed(2)+' ms').join(' · ');
  }else{
    delayAuto_.textContent='当前系统延时校准暂未开放（仅电视内置/电脑自带音箱支持）';
  }
}
function clampDelayGeom(){
  const Lv=Math.max(0.1,+L_.value||3.1);
  const Wv=Math.max(0.1,+W_.value||4.4);
  // 聆听距离不能大于房间长度(进深 L)
  let d=+dist_.value;
  if(!isFinite(d)||d<=0) d=0.1;
  if(d>Lv){
    d=Lv; dist_.value=d.toFixed(1);
    if(delayErr_) delayErr_.textContent='⚠ 聆听距离不能超过房间长度（'+Lv.toFixed(1)+'m），已自动限制为 '+d.toFixed(1)+'m';
  }else if(delayErr_){
    delayErr_.textContent='';
  }
  // 水平偏移不能超出房间半宽
  let o=+offX_.value; if(!isFinite(o)) o=0;
  const maxO=Wv/2;
  if(o>maxO) offX_.value=maxO.toFixed(2);
  else if(o<-maxO) offX_.value=(-maxO).toFixed(2);
}
function onDelayInput(){
  clampDelayGeom();
  redrawCanvases();
  renderDelayReadout();
  // 延时是独立功能，不联动 PEQ 开关
  api('/api/apply',{gen:genInputs(),bands:currentBands}).catch(()=>{});
  showMsg('已写入延时，点「应用」按钮重载',true);
  pendingBadge.style.display='inline';
}
function onBalanceInput(){
  const v=+balance_.value;
  balVal_.textContent = (v===0) ? '居中' : (v<0 ? '左偏 '+Math.abs(v)+'%' : '右偏 '+v+'%');
  // 平衡独立功能，不联动 PEQ 开关
  api('/api/apply',{gen:genInputs(),bands:currentBands}).catch(()=>{});
  showMsg('已写入平衡，点「应用」按钮重载',true);
  pendingBadge.style.display='inline';
}
function onSurroundDelayInput(){
  const v=+surroundDelay_.value;
  sdVal_.textContent=v.toFixed(1)+' ms';
  // 环绕延时独立功能，不联动 PEQ 开关
  api('/api/apply',{gen:genInputs(),bands:currentBands}).catch(()=>{});
  showMsg('已写入环绕延时，点「应用」按钮重载',true);
  pendingBadge.style.display='inline';
}
function setStatus(on,bands){
  toggle.className="toggle "+(on?"on":"off");
  statusBands.textContent=on?"已开启":"已关闭（透传）";
}
function showMsg(t,ok){ msg.textContent=t; msg.style.color=ok?"var(--acc)":"#f85149"; }

const L_=document.getElementById('L'),W_=document.getElementById('W'),H_=document.getElementById('H');
const sys_=document.getElementById('sys');
const low_=document.getElementById('low'),mid_=document.getElementById('mid'),hi_=document.getElementById('hi');
const lowV=document.getElementById('lowV'),midV=document.getElementById('midV'),hiV=document.getElementById('hiV');
const delayOn_=document.getElementById('delayOn');
const tvW_=document.getElementById('tvW'),dist_=document.getElementById('dist'),offX_=document.getElementById('offX');
const tvH_=document.getElementById('tvH'),earH_=document.getElementById('earH');
const delayErr_=document.getElementById('delayErr');
const plan_=document.getElementById('plan');
const delayAuto_=document.getElementById('delayAuto');
const delayManualWrap_=document.getElementById('delayManualWrap');
const balance_=document.getElementById('balance'), balVal_=document.getElementById('balVal');
const surroundDelay_=document.getElementById('surroundDelay'), sdVal_=document.getElementById('sdVal');
const viz=document.getElementById('viz');
const toggle=document.getElementById('toggle'),statusBands=document.getElementById('statusBands'),msg=document.getElementById('msg');
let currentBands=[];
let applyTimer=null;

// 拖动滑块：实时写 peq_af.txt，但不触发重载。
// 显示"有未应用的更改"提示，用户需点"应用"按钮才 kill ffmpeg 重载。
[L_,W_,H_,sys_,low_,mid_,hi_].forEach(el=>el.addEventListener('input',()=>{
  lowV.textContent=(+low_.value).toFixed(1)+' dB';
  midV.textContent=(+mid_.value).toFixed(1)+' dB';
  hiV.textContent=(+hi_.value).toFixed(1)+' dB';
  clampDelayGeom();  // L 改变时重新限制聆听距离不超过房间长度
  build();
  redrawCanvases();
  if(!toggle.classList.contains('on')){ toggle.className='toggle on'; statusBands.textContent='已开启'; }
  api('/api/apply',{gen:genInputs(),bands:currentBands}).catch(()=>{});
  showMsg('已写入曲线，点「应用」按钮重载',true);
  pendingBadge.style.display='inline';
}));
[L_,W_,H_,sys_,low_,mid_,hi_].forEach(el=>el.addEventListener('change',()=>{ build(); redrawCanvases(); }));
// 切换系统类型时重建每声道微调滑块（tv 2.0 = FL/FR；5.1/2.1 后续开放），并重绘画布
sys_.addEventListener('input',()=>{ renderDelayManual(); renderDelayReadout(); redrawCanvases(); });
sys_.addEventListener('change',()=>{ renderDelayManual(); renderDelayReadout(); redrawCanvases(); });
[delayOn_,tvW_,dist_,offX_,tvH_,earH_].forEach(el=>el.addEventListener('input',onDelayInput));
balance_.addEventListener('input',onBalanceInput);
surroundDelay_.addEventListener('input',onSurroundDelayInput);
// 平面图：仅左右拖拽设 水平偏移(offX)；垂直距离(dist)为固定输入值，不随拖拽改变
setupDrag(plan_, ([cx,cy])=>{
  const W=plan_.width,H=plan_.height,pad=38;
  const roomW=Math.max(0.1,(+W_.value)||4.4), roomD=Math.max(0.1,(+L_.value)||3.1);
  const ux=W-2*pad, uy=H-2*pad;
  const scale=Math.min(ux/roomW, uy/roomD);
  const dw=roomW*scale;
  const ox=pad+(ux-dw)/2;
  const vx=Math.max(-roomW/2,Math.min(roomW/2,(cx-ox)/dw*roomW - roomW/2));
  offX_.value=(+vx.toFixed(2));
  onDelayInput();
});

// 重载状态轮询：loading→"正在重载音效（TV端需要约50秒或手动重播立即生效）"，loaded→"新音效已生效"，error→显示错误
let replayPolling=false;
async function pollReplayStatus(){
  if(replayPolling) return;
  replayPolling=true;
  try{
    while(true){
      const r=await (await fetch('/api/replay-status')).json();
      if(r.status==='loading'){
        replayStatus.innerHTML='<span style="color:var(--warn)">● 正在重载音效，请稍候…</span>';
        await new Promise(r=>setTimeout(r,1000));
      }else if(r.status==='loaded'){
        replayStatus.innerHTML='<span style="color:var(--acc)">✓ 新音效已生效</span>';
        pendingBadge.style.display='none';
        break;
      }else if(r.status==='error'){
        replayStatus.innerHTML='<span style="color:#f85149">✗ '+(r.msg||'失败')+'</span>';
        break;
      }else{ // idle
        replayStatus.textContent='';
        break;
      }
    }
  }catch(e){}
  replayPolling=false;
}

// 双重 EQ 提醒轮询：检测到 Windows 客户端正在串流 NAS（服务端已烤音）时，
// 提示该用户勿在 Windows 本地再开『空间大师 Win 版』，避免同一路声音被加两次 EQ。
const eqWarn_=document.getElementById('eqWarn'), eqWarnMsg_=document.getElementById('eqWarnMsg');
async function pollDoubleEq(){
  try{
    const r=await (await fetch('/api/replay-status')).json();
    if(r.double_eq_advice){
      eqWarnMsg_.textContent=r.double_eq_advice;
      eqWarn_.style.display='block';
    }else{
      eqWarn_.style.display='none';
    }
  }catch(e){}
}
setInterval(pollDoubleEq, 10000);
pollDoubleEq();

async function api(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
toggle.onclick=async()=>{
  if(toggle.classList.contains('on')){
    try{ const res=await api('/api/off-current',{}); setStatus(false,[]); showMsg(res.msg||'PEQ 已关闭，正在重载…',true); pollReplayStatus(); }
    catch(e){ showMsg('关闭失败: '+e,false); }
  }else{
    try{
      const res=await api('/api/apply-current',{gen:genInputs(),bands:currentBands, apply:true});
      setStatus(true, res.bands||[]); showMsg(res.msg||'已应用，正在重载…',true); pollReplayStatus();
    }catch(e){ showMsg('开启失败: '+e,false); }
  }
};

// ---------- Jellyfin 连接 + 应用 ----------
const jfUrl=document.getElementById('jfUrl'), jfKey=document.getElementById('jfKey');
const jfState=document.getElementById('jfState'), jfSave=document.getElementById('jfSave');
const applyCurrentBtn=document.getElementById('applyCurrentBtn'), acMsg=document.getElementById('acMsg');
const replayStatus=document.getElementById('replayStatus');
const pendingBadge=document.getElementById('pendingBadge');
async function loadJf(){
  try{
    const r=await (await fetch('/api/jf')).json();
    if(r.url) jfUrl.value=r.url;
    else if(!jfUrl.value) jfUrl.value='http://127.0.0.1:8098';
    jfState.textContent = r.hasKey ? '已配置 ✓' : '未配置';
  }catch(e){}
}
jfSave.onclick=async()=>{
  try{
    const r=await api('/api/jf',{url:jfUrl.value,key:jfKey.value});
    if(r.ok){ jfState.textContent='已保存 ✓'; showMsg('Jellyfin 连接已保存',true); }
    else showMsg('保存失败: '+(r.error||''),false);
  }catch(e){ showMsg('保存失败: '+e,false); }
};
applyCurrentBtn.onclick=async()=>{
  acMsg.textContent='处理中…';
  try{
    // 滑块已通过 /api/apply 写好 peq_af.txt，按钮只触发重播
    const r=await api('/api/replay',{});
    if(r.ok){
      acMsg.textContent='';
      pendingBadge.style.display='none';
      pollReplayStatus();
    }
    else acMsg.textContent='✗ '+(r.msg||'失败');
  }catch(e){ acMsg.textContent='✗ '+e; }
};

(async()=>{
  try{
    const st=await (await fetch('/api/state')).json();
    setStatus(st.on,st.bands||[]);
    const g=st.gen||{L:3.1,W:4.4,H:2.8,sys:'tv',low:0,mid:0.7,hi:1.5};
    L_.value=g.L; W_.value=g.W; H_.value=g.H; sys_.value=g.sys;
    low_.value=g.low; mid_.value=g.mid; hi_.value=g.hi;
    delayOn_.checked=!!(g.delayOn);
    tvW_.value=(g.tvW!=null)?g.tvW:1.2; dist_.value=(g.dist!=null)?g.dist:2.5; offX_.value=(g.offX!=null)?g.offX:0;
    tvH_.value=(g.tvH!=null)?g.tvH:0.9; earH_.value=(g.earH!=null)?g.earH:1.1;
    balance_.value=(g.balance!=null)?g.balance:0;
    balVal_.textContent=(+balance_.value===0)?'居中':((+balance_.value<0?'左偏 ':'右偏 ')+Math.abs(+balance_.value)+'%');
    surroundDelay_.value=(g.surroundDelay!=null)?g.surroundDelay:0;
    sdVal_.textContent=(+surroundDelay_.value).toFixed(1)+' ms';
    clampDelayGeom();
    delayManualState=Object.assign({FL:0,FR:0,FC:0,SL:0,SR:0,LFE:0}, (g.delayManual||{}));
    lowV.textContent=(+low_.value).toFixed(1)+' dB';
    midV.textContent=(+mid_.value).toFixed(1)+' dB';
    hiV.textContent=(+hi_.value).toFixed(1)+' dB';
    await computeAll();
    renderDelayManual();
    redrawCanvases();
    showMsg('',true);
    loadJf();
  }catch(e){ showMsg('加载状态失败: '+e,false); }
})();
</script>
</body>
</html>
"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split('?')[0].rstrip('/') or '/'
        if p in ('/', '/index.html'):
            self._send(200, HTML_PAGE, "text/html; charset=utf-8")
        elif p == '/api/state':
            on, bands = read_peq()
            self._send(200, json.dumps({"on": on, "bands": bands,
                                        "gen": read_gen(), "downmix": read_downmix(),
                                        "replayStatus": get_replay_state()}))
        elif p == '/api/jf':
            cfg = read_jellyfin_cfg()
            self._send(200, json.dumps({"url": cfg.get("url", ""), "hasKey": bool(cfg.get("key"))}))
        elif p == '/api/replay-status':
            self._send(200, json.dumps(get_replay_state()))
        else:
            self._send(404, '{"error":"not found"}')

    def do_POST(self):
        p = self.path.split('?')[0].rstrip('/')
        n = int(self.headers.get('Content-Length', 0) or 0)
        raw = self.rfile.read(n) if n else b'{}'
        try:
            data = json.loads(raw or b'{}')
        except Exception:
            data = {}
        if p == '/api/compute':
            # 闭源引擎：原始输入 -> {bands, delays}。本端点不含公式。
            self._send(200, json.dumps(engine_runtime.compute(data)))
        elif p == '/api/apply':
            # 滑块拖动用：只写 peq_af.txt，不触发重载。
            # 用户需点"应用"按钮才 kill ffmpeg 重载（避免频繁拖滑块导致反复 kill）。
            bands = data.get('bands', [])
            if data.get('gen'):
                write_gen(data['gen'])
                sync_downmix_from_sys(data['gen'])
                write_delay(compute_delay_string(data['gen']))
                write_balance(compute_balance_string(data['gen']))
                write_surround_delay(compute_surround_delay_string(data['gen']))
            write_peq(bands)
            self._send(200, json.dumps({"ok": True, "on": True, "bands": bands}))
        elif p == '/api/off':
            open(PEQ_AF, 'w').write('')
            self._send(200, json.dumps({"ok": True, "on": False}))
        elif p == '/api/off-current':
            # 关闭 PEQ：清空曲线文件 + 触发重载（kill ffmpeg 回到透传）。
            open(PEQ_AF, 'w').write('')
            cfg = read_jellyfin_cfg()
            ok, m = trigger_replay(cfg)
            self._send(200, json.dumps({"ok": True, "on": False,
                                        "msg": "PEQ 已关闭，" + ("正在重载…" if ok else m)}))
        elif p == '/api/jf':
            # 保存 Jellyfin 连接（地址 + API Key）
            cfg = read_jellyfin_cfg()
            if data.get('url') is not None:
                cfg['url'] = data['url'].strip()
            if data.get('key'):
                cfg['key'] = data['key'].strip()
            write_jellyfin_cfg(cfg)
            self._send(200, json.dumps({"ok": True}))
        elif p == '/api/apply-current':
            # PEQ 开启用：写文件 + 触发重载（kill ffmpeg 用新 EQ 重转码）。
            bands = data.get('bands', [])
            if data.get('gen'):
                write_gen(data['gen'])
                sync_downmix_from_sys(data['gen'])
                write_delay(compute_delay_string(data['gen']))
                write_balance(compute_balance_string(data['gen']))
                write_surround_delay(compute_surround_delay_string(data['gen']))
            if bands:
                write_peq(bands)
            cfg = read_jellyfin_cfg()
            ok, m = trigger_replay(cfg)
            self._send(200, json.dumps({"ok": True,
                                        "msg": "参数已写入，" + ("正在重载…" if ok else m)}))
        elif p == '/api/replay':
            # "应用"按钮用：文件已由滑块写好，只触发重载（kill ffmpeg）。
            cfg = read_jellyfin_cfg()
            ok, m = trigger_replay(cfg)
            self._send(200, json.dumps({"ok": ok, "msg": m if not ok else "已触发重载"}))
        else:
            self._send(404, '{"error":"not found"}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"空间大师统一控制台已启动: http://0.0.0.0:{PORT}")
    print("[replay] 按钮触发模式：拖滑块只写文件不重载，点'应用'按钮才 kill ffmpeg 强制重载")
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
