# reCamera Multimodal — 架构、运行流程与 Debug 手册

> 唯一流程主文档  
> 版本：4.0  
> 更新日期：2026-06-26  
> 当前 reCamera 无线 IP：`192.168.106.85`

本文档描述当前代码真实实现。架构深度说明见 `docs/ARCHITECTURE.md`。

---

## 1. 系统目标与当前能力

本项目把 reCamera 云台摄像头、视觉模型、ReSpeaker DOA 和 Web 控制台整合为单一感知与展示系统，实际硬件控制由独立控制进程负责。

| 能力 | 状态 | 说明 |
|---|---|---|
| 实时视频流（MJPEG） | ✅ 已实现 | reCamera SSCMA → FastAPI → 浏览器 |
| 人体/人脸检测与追踪 | ✅ 已实现 | FaceTrackerV2 + YOLO11 pose + SSCMA 框 |
| 情绪分析（8 类） | ✅ 已实现 | EmotiEffLib，需 insightface 依赖 |
| 专注度评分 | ✅ 已实现 | EMA + 个人基线，0–100 分 |
| 眼部指标 | ✅ 已实现 | EAR、PERCLOS、眨眼率 |
| 声源定位（DOA） | ✅ 已实现 | TCP 接收 ReSpeaker 数据（`0.0.0.0:9999`） |
| FSM 状态可观测 | ✅ 已实现 | Observe-only Orchestrator 镜像，零控制权 |
| 云台遥测（只读） | ✅ 已实现 | `RecameraClient.get_status()` 硬件 readback |
| LLM 对话/日记 | ✅ 已实现 | DeepSeek API + 本地轻量 fallback |
| 云台自动控制 | ✅ 已实现 | 仅通过 `main_phase3.py`（单独控制进程） |

---

## 2. 总体架构

```
reCamera 192.168.106.85
  ├─ :8090  SSCMA WebSocket ──────────────> recamera_fastapi.py :8001
  └─ :1880  Node-RED Socket.IO <────────── main_phase3.py（唯一控制进程）

ReSpeaker → xvf_host/Windows → TCP :9999 ──> recamera_fastapi.py

recamera_fastapi.py
  ├─ 视频: /video_feed（MJPEG）
  ├─ 感知: FaceTracker / Pose / Emotion / Attention / DOA
  ├─ 遥测镜像: Observe-only Orchestrator（不发控制指令）
  ├─ 云台状态: RecameraClient.get_status()（只读）
  ├─ WebSocket /ws（每 200ms 推状态快照）
  ├─ PAGE 1 /control /v2: 实时控制台（只读）
  └─ PAGE 2 /home:      产品 Demo（纯 mock）

控制链路（单一）：
  main_phase3.py → Orchestrator → RecameraClient.apply_command() → 云台
```

**架构保证**：FastAPI 进程**永远不调用** `RecameraClient.apply_command()`。  
所有云台指令唯一来源：`main_phase3.py` → `core/orchestrator.py` → `hardware/recamera_client.py`。

### 2.1 网络端口

| 地址/端口 | 服务 | 用途 |
|---|---|---|
| `192.168.42.1:22` | reCamera USB SSH | 初始化及查询无线 IP |
| `192.168.106.85:22` | reCamera Wi-Fi SSH | 无线维护 |
| `192.168.106.85:80` | 官方 Demo | reCamera 官方页面 |
| `192.168.106.85:1880` | Node-RED Dashboard | 云台 Socket.IO 控制（仅 `main_phase3.py` 使用） |
| `192.168.106.85:8090` | SSCMA WebSocket | 视频和检测框（FastAPI 接收） |
| `0.0.0.0:9999` | Network DOA Receiver | 接收远程 DOA 数据 |
| `0.0.0.0:8001` | FastAPI | 项目页面、API、MJPEG、WebSocket |

---

## 3. 目录与模块职责

### 3.1 主入口

| 文件 | 职责 |
|---|---|
| `recamera_fastapi.py` | **主生产入口**：视频、感知、遥测、页面服务；零控制权 |
| `main_phase3.py` | **控制进程**：单 FSM 控制平面，唯一可驱动云台的进程 |
| `recamera_demo.py` | 轻量演示入口（非主流程） |

日常开发和演示使用 `recamera_fastapi.py`。  
需要测试真实云台控制时，使用 `main_phase3.py`。

### 3.2 控制核心（`core/`）

| 文件 | 生产状态 | 职责 |
|---|---|---|
| `core/fsm.py` | **ACTIVE** | 单一 FSM；5 状态；事件表 + debounce |
| `core/orchestrator.py` | **ACTIVE** | 唯一控制决策引擎；audio/vision/fusion 三策略 |
| `core/event.py` | **ACTIVE** | 数据类：Event、BBox、ControlCommand（frozen） |
| `core/safety_layer.py` | **ACTIVE** | ControlCommand 约束过滤：rate limit / 步长 / 加速度 |
| `core/control_filter.py` | **ORPHAN** | 遗留比例控制模块；未被生产代码 import，可忽略 |

### 3.3 硬件

| 文件 | 职责 |
|---|---|
| `hardware/recamera_client.py` | 唯一硬件出口；`apply_command()` → Socket.IO 或 HTTP；`get_status()` 只读 |

### 3.4 视觉处理（`vision/`）

| 文件 | 职责 |
|---|---|
| `vision/video_stream.py` | SSCMA WebSocket 接收（JPEG + 检测框） |
| `vision/face_tracker_v2.py` | SCRFD + Kalman/ByteTrack + ArcFace 多脸追踪 |
| `vision/pose_estimator.py` | YOLO11 pose：人体框 + 17 关键点 |
| `vision/mediapipe_face.py` | Face Landmarker：精细面部点（可选精度提升） |
| `vision/emotieff_adapter.py` | EmotiEffLib 情绪推理 |
| `vision/attention_engine.py` | EMA 专注度评分 + 基线自适应 |
| `vision/eye_metrics.py` | EAR / 眨眼 / PERCLOS |
| `vision/llm_reflect.py` | 本地轻量日记反思 fallback |

### 3.5 音频（`audio/`）

| 文件 | 职责 |
|---|---|
| `audio/network_doa.py` | TCP `0.0.0.0:9999` DOA 接收器（当前默认） |
| `audio/doa.py` | DOA 文本解析 + 可插拔 source 基础实现 |
| `audio/respeaker_doa.py` | USB HID DOA（可选 fallback） |
| `audio/conversation_recorder.py` | 可选录音会话（`save_audio=true`） |

### 3.6 前端（`dashboard/`）

| 文件 | 路由 | 用途 |
|---|---|---|
| `recamera_v2_live.html` | `/control` `/v2` | PAGE 1：实时控制台（观测 FSM、遥测、感知、决策链） |
| `home.html` | `/home` | PAGE 2：产品 Demo（纯 mock，无任何真实硬件调用） |
| `manifest.webmanifest` `/sw.js` | 同名 | PWA 支持 |

`/`（根路由）重定向到 `/home`。

---

## 4. reCamera 初始化

1. 接 USB 线，SSH 登录：
   ```bash
   ssh recamera@192.168.42.1
   # 密码：recamera0526_
   ```

2. 查询无线 IP：
   ```bash
   ip addr   # 找 wlan0
   ```

3. 拔 USB，浏览器访问：
   ```text
   http://192.168.106.85/
   ```

无线 IP 可能被 DHCP 重新分配，网络变化后必须重新查询并更新代码默认值。

---

## 5. 安装与配置

### 5.1 Python 依赖

```bash
cd ~/recamera_multimodal
python3 -m pip install -r requirements.txt --break-system-packages
python3 -m pip install insightface --break-system-packages   # 推荐：FaceTrackerV2
```

### 5.2 环境变量完整参考

所有变量均需在启动 FastAPI **之前**在同一终端中 export。未设置的变量使用方括号内的默认值。

#### LLM / DeepSeek（关键：不 export 则 LLM 功能降级）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | **空**（必须手动设置） | 不设置 → `/api/chat` `/api/reflect` 自动回退本地轻量 `llm_reflect`（质量明显下降，无网络请求） |
| `DEEPSEEK_API_URL` | `https://api.deepseek.com/chat/completions` | API 端点；使用代理或其他兼容接口时修改 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名称 |
| `DEEPSEEK_MAX_TOKENS` | `600` | 单次回复最大 token 数 |

```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
# 以下三项有默认值，通常不需要修改：
# export DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions
# export DEEPSEEK_MODEL=deepseek-v4-flash
# export DEEPSEEK_MAX_TOKENS=600
```

> **fallback 说明**：未配置 `DEEPSEEK_API_KEY` 时，`/api/chat` 和 `/api/reflect` 由 `vision/llm_reflect.py`（本地规则引擎）处理，情绪日记和陪伴对话质量大幅下降。启动日志会打印：
> ```
> DeepSeek API key not configured; using local/fallback chat
> ```

#### DOA（声源定位，仅多人工作场景需要）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RECAMERA_DOA_SOURCE` | `tcp` | `tcp`（网络接收）或 `usb`（USB HID 直连 ReSpeaker） |
| `RECAMERA_DOA_HOST` | `0.0.0.0` | TCP 监听地址，通常不需修改 |
| `RECAMERA_DOA_PORT` | `9999` | TCP 监听端口 |
| `RECAMERA_DOA_SPEECH_HOLD` | `0.8` | 人声检测持续保持秒数（防抖） |

```bash
# USB 直连 ReSpeaker（推荐，需先 usbipd attach）：
export RECAMERA_DOA_SOURCE=usb

# 或网络转发模式（无 USB 时）：
export RECAMERA_DOA_SOURCE=tcp   # 默认，可不写
```

#### 音频录音设备（会议记录功能需要）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RECAMERA_AUDIO_DEVICE` | 空（系统默认麦克风） | sounddevice 设备索引；查询方式见下 |

```bash
# 查询可用音频设备索引：
python3 -c "import sounddevice; print(sounddevice.query_devices())"
# 找到 ReSpeaker 对应行号，设置：
export RECAMERA_AUDIO_DEVICE=2   # 示例：索引为 2
```

未设置时使用系统默认麦克风，ReSpeaker 可能无法被正确选中。

---

## 6. 系统启动与运行模式

### 6.0 架构前提（重要）

> **PAGE 1（`/control`）是只读观测界面，不能直接发出云台指令。**
>
> 真实云台控制由 `main_phase3.py` 独立进程负责，与 FastAPI 并行运行。
> PAGE 1 展示的 FSM 状态来自 FastAPI 内的 observe-only Orchestrator 镜像——
> 它运行与 `main_phase3.py` 相同的感知逻辑，计算"应该怎么控制"，但**永远不发指令**。
> 实际指令只能来自 `main_phase3.py → RecameraClient.apply_command()`。

三种运行模式：

| 模式 | 启动进程 | 能看到 | LLM 状态 | 不能做 |
|---|---|---|---|---|
| **M1 纯观测** | 仅 FastAPI（dry-run） | 视频、感知、FSM 镜像 | 需 export API_KEY | 云台遥测为 null，不控制硬件 |
| **M2 观测 + 遥测** | FastAPI `--no-dry-run` | 视频、感知、FSM 镜像、云台 yaw/pitch | 需 export API_KEY | 不自动控制云台 |
| **M3 完整系统** | FastAPI + main_phase3.py | 所有数据 + 云台移动 + 遥测 | 需 export API_KEY | — |

> **LLM 启用原则**：三种模式下 LLM 均通过 `DEEPSEEK_API_KEY` 环境变量开启，与 `--no-dry-run` 无关。未 export 时系统不报错，只是静默降级为本地 fallback，日记/对话质量明显下降。

---

### 6.1 启动前检查
```bash
cd ~/recamera_multimodal
ping -c 3 192.168.106.85          # reCamera 可达
nc -zv 192.168.106.85 8090        # SSCMA 视频端口
nc -zv 192.168.106.85 1880        # Node-RED（M3 需要）
usbipd.exe list | grep 2886       # ReSpeaker 状态（Shared / Attached）
```

---

### 6.2 模式 M1：纯观测（最安全，不接触硬件控制）

适合：首次测试、UI 调试、不需要云台遥测时。

```bash
cd ~/recamera_multimodal

# ── 必填：LLM 对话/日记/反思功能 ──
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# ── 可选：若有 ReSpeaker 且需要 DOA 声源定位 ──
# export RECAMERA_DOA_SOURCE=usb      # USB 直连
# export RECAMERA_AUDIO_DEVICE=2      # 麦克风设备索引

python3 recamera_fastapi.py
```

启动成功日志：
```
SSCMA connected to ws://192.168.106.85:8090/
Network DOA listening on 0.0.0.0:9999
DRY-RUN mode → gimbal commands NOT sent
Uvicorn running on http://0.0.0.0:8001
```

若 LLM 已配置，同时出现（无此行说明 KEY 未设置）：
```
Loading DeepSeek model: deepseek-v4-flash
```

PAGE 1 能看到：视频流、人体/人脸检测框、FSM 状态（IDLE 起点）、感知通道数值、情绪/专注指标。  
PAGE 1 看不到：云台真实 yaw/pitch（显示 null，因为 dry-run 不连硬件）。

---

### 6.3 模式 M2：观测 + 云台遥测（推荐日常开发）

适合：验证感知 → FSM 逻辑，同时看到云台真实角度 readback。

**步骤 1：挂载 ReSpeaker（如使用 USB 直连方式 A）**
```bash
usbipd.exe attach --busid 1-2 --wsl
lsusb | grep 2886   # 确认出现
```

**步骤 2：设置环境变量并启动 FastAPI**
```bash
cd ~/recamera_multimodal

# ── 必填：LLM 对话/日记 ──
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# ── DOA：USB 直连（已 attach）或 TCP 网络 ──
export RECAMERA_DOA_SOURCE=usb          # USB 直连；未 attach 时改为 tcp 或不设置

# ── 音频录音设备（多人会议录音需要） ──
# export RECAMERA_AUDIO_DEVICE=2        # 查询方式见 §5.2

python3 recamera_fastapi.py --no-dry-run
```

关键日志：
```
🎤 ReSpeaker XVF3800 connected (VID=0x2886 PID=0x001A)
🎤 DOA polling started @ 10 Hz
RecameraClient: CONNECTED via Socket.IO → 192.168.106.85:1880
Uvicorn running on http://0.0.0.0:8001
```

PAGE 1 能看到：视频、检测框、FSM 状态变化、DOA 角度、云台真实 yaw/pitch 遥测。  
注意：`--no-dry-run` 让 FastAPI 侧 RecameraClient 真实连接云台（仅用于 `get_status()` readback），**仍然不发任何控制指令**。

---

### 6.4 模式 M3：完整系统（感知 + FSM + 真实云台控制）

适合：端到端验证，确认人脸追踪 / 声源定位 → 云台实际移动。

**两个独立终端并行运行：**

```bash
# 终端 1 — FastAPI（观测 + 遥测 + LLM）
cd ~/recamera_multimodal
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx   # LLM 对话/日记
export RECAMERA_DOA_SOURCE=usb                        # USB ReSpeaker（已 attach）
# export RECAMERA_AUDIO_DEVICE=2                      # 会议录音设备索引（按需）
python3 recamera_fastapi.py --no-dry-run
```

```bash
# 终端 2 — 控制进程（真实云台控制）
cd ~/recamera_multimodal
python3 main_phase3.py \
  --enable-control \
  --gimbal-ip 192.168.106.85 \
  --fps 10 \
  --max-cycles 500
```

`main_phase3.py` 关键日志：
```
CONNECTED via Socket.IO → 192.168.106.85:1880
[0001] state=IDLE target=no command=hold
[0004] state=VISION_TRACK target=yes command=vision_track   ← 检测到目标
```

此时 PAGE 1 可观测到：
- FSM 镜像从 IDLE → VISION_TRACK
- 决策 trace 出现 `vision_track` 命令记录
- Gimbal 遥测 yaw/pitch 数值随追踪实时变化（由 main_phase3.py 驱动，FastAPI 读 readback）

**注意 SSCMA 双连接**：FastAPI 和 main_phase3.py 各自独立连接 `ws://192.168.106.85:8090/`，二者同时持有连接。reCamera 通常支持多客户端，但若出现视频断连，先停 main_phase3.py，单独验证 FastAPI。

---

### 6.5 Mock 控制进程（不连真实云台，仅验证 FSM 逻辑）

```bash
python3 main_phase3.py --mock --max-cycles 30 --log-level DEBUG
```

---

### 6.6 停止

```bash
Ctrl+C      # 停止各进程
# main_phase3.py 会自动发送 emergency stop（atexit hook）
```

---

---

## 7. ReSpeaker XVF3800 连接与 DOA 数据传输

本节描述两种接入方式，**推荐方式 A（usbipd WSL 直连）**，已在当前机器验证可用。

### 7.0 设备信息

| 属性 | 值 |
|---|---|
| USB VID:PID | `2886:001a`（Seeed Technology / reSpeaker XVF3800） |
| usbipd BUSID | `1-2`（当前机器，重插可能变化） |
| usbipd 状态 | `Shared`（已预 bind，无需再 bind） |
| WSL IP | `192.168.106.23`（mirror 网络模式，Windows → WSL 用此 IP） |
| pyusb 路径 | `audio/respeaker_doa.py`（VID=0x2886, PID=0x001a，10Hz 轮询） |
| TCP 接收路径 | `audio/network_doa.py`（`0.0.0.0:9999`） |

---

### 方式 A：usbipd WSL 直连（推荐）

USB 直接挂载到 WSL，FastAPI 用 pyusb 读取，零网络延迟。

#### 前置条件

- Windows 已安装 `usbipd-win`（当前版本 5.3.0，已确认可用）
- WSL 已安装 `pyusb`（当前已安装）

验证：
```bash
# 在 WSL 内执行
usbipd.exe list
# 应看到：1-2  2886:001a  reSpeaker XVF3800 ...  Shared
```

#### A1. 一次性 bind（已完成，无需再做）

状态为 `Shared` 说明已 bind。若设备变为 `Not shared`：
```powershell
# Windows 管理员 PowerShell
usbipd bind --busid 1-2
```

#### A2. 每次会话 attach（WSL 启动后执行一次）

```bash
# 在 WSL 内执行（无需管理员）
usbipd.exe attach --busid 1-2 --wsl
```

预期输出：
```text
usbipd: info: Using WSL distribution 'Ubuntu' to attach; the device will be available in all WSL 2 distributions.
usbipd: info: Loading vhci_hcd module.
usbipd: info: Detected networking mode 'mirrored'.
usbipd: info: Using IP address 127.0.0.1 to reach the host.
```

验证挂载成功：
```bash
lsusb | grep 2886
# Bus 001 Device 002: ID 2886:001a Seeed Technology Co., Ltd. reSpeaker XVF3800 4-Mic Array
ls /dev/hidraw0
# /dev/hidraw0
```

#### A3. 自动持久化 attach（可选，usbipd 4.x+）

若不想每次手动执行：
```bash
usbipd.exe attach --busid 1-2 --wsl --auto-attach
```
此命令在后台持续监听，USB 重插或 WSL 重启后自动重新 attach。建议在 Windows 启动项或独立终端中保持运行。

#### A4. 启动 FastAPI（USB 模式）

```bash
export RECAMERA_DOA_SOURCE=usb
python3 recamera_fastapi.py
```

启动成功日志：
```text
🎤 ReSpeaker XVF3800 connected (VID=0x2886 PID=0x001A)
🎤 DOA polling started @ 10 Hz
```

`/api/state` 中 `doa.source = "usb"`，`doa.available = true`。

#### A5. detach（归还给 Windows）

```bash
usbipd.exe detach --busid 1-2
```

---

### 方式 B：Windows 侧 Python + TCP 转发

ReSpeaker 留在 Windows 侧，通过 TCP 把 DOA 角度发到 WSL。不需要 usbipd，但需要 Windows 上有 Python 和 libusb。

#### B1. Windows 环境准备

1. 安装 Python 3.10+（Windows）
2. 安装 libusb 驱动（推荐用 [Zadig](https://zadig.akeo.ie/) 为 ReSpeaker Control Interface 安装 `WinUSB`）
3. 安装 pyusb：
   ```cmd
   pip install pyusb
   ```

#### B2. 确认 WSL IP

```bash
# 在 WSL 内执行
hostname -I | awk '{print $1}'
# 当前：192.168.106.23
```

#### B3. 启动 DOA 转发

```cmd
# Windows 命令行（tools/ 目录）
python tools\send_doa_tcp.py --host 192.168.106.23 --mock-angle 35
```

真实 xvf_host 输出（如 ReSpeaker SDK xvf_host.exe 可用）：
```cmd
python tools\send_doa_tcp.py --host 192.168.106.23 --command "xvf_host.exe AUDIO_MGR_SELECTED_AZIMUTHS"
```

Windows 防火墙放行：
```powershell
New-NetFirewallRule -DisplayName "WSL DOA 9999" -Direction Outbound -Protocol TCP -RemotePort 9999 -Action Allow
```

#### B4. 启动 FastAPI（TCP 模式，默认）

```bash
# WSL 内，无需设置 DOA_SOURCE
python3 recamera_fastapi.py
```

启动成功日志：
```text
Network DOA listening on 0.0.0.0:9999
DOA source: tcp, port: 9999
```

---

### 7.1 DOA 数据格式（TCP 接收器接受的格式）

`audio/network_doa.py` 默认监听 `0.0.0.0:9999`，支持以下输入格式（每行一条）：

```text
35                                                      # 纯角度（度）
35 deg                                                  # 角度 + 单位
0.6109 rad                                              # 弧度
AUDIO_MGR_SELECTED_AZIMUTHS 0.6109 (35.0 deg)         # xvf_host 格式
{"azimuth_deg":35,"speech":true}                       # JSON，推荐
{"doa":0.6109,"unit":"rad","has_speech":true}          # JSON 弧度
```

接收器维护字段：

| 字段 | 含义 |
|---|---|
| `doa_deg` | 最新角度（0–359°，0=正前方） |
| `has_speech` | speech hold 窗口内为 true（每个有效包自动维持 0.8s） |
| `age` | 距离最新有效包的秒数（>1s 则 FSM 不触发音频事件） |
| `packet_count` | 累计有效包数 |
| `sender_connected` | TCP 发送端是否在线 |

### 7.2 Mock 发送端（开发/测试）

```bash
# WSL 内
python3 tools/send_doa_tcp.py --host 127.0.0.1 --mock-angle 35
```

### 7.3 DOA → FSM 映射

两种模式下，FastAPI 的 observe-only Orchestrator 均消费 DOA 事件，FSM 在静音时维持 IDLE，在 speech 触发后进入 AUDIO_SEARCH。此过程**不发出实际控制指令**，仅用于 PAGE 1 决策链展示。

真实云台 DOA 跟随由 `main_phase3.py` 处理（Audio Event → Orchestrator._audio_command() → RecameraClient.apply_command()）。

### 7.4 模式环境变量

| 变量 | 值 | 含义 |
|---|---|---|
| `RECAMERA_DOA_SOURCE` | `tcp`（默认） | 使用 TCP NetworkDOA |
| `RECAMERA_DOA_SOURCE` | `usb` | 使用 pyusb ReSpeakerDOA（方式 A） |
| `RECAMERA_DOA_PORT` | `9999`（默认） | TCP 接收端口 |
| `RECAMERA_DOA_SPEECH_HOLD` | `0.8`（默认） | speech 保持时间（秒） |

---

## 8. 前端状态同步

### 8.1 WebSocket `/ws`

后端每 ~200ms 推送 `state_snapshot`，字段：

| 顶级字段 | 内容 |
|---|---|
| `video` | connected / fps / width / height / detections |
| `pose` | persons 列表 / count |
| `gimbal` | connected / yaw / pitch / speed / mode（硬件 readback） |
| `doa` | available / doa_deg / has_speech / age / packet_count |
| `attention` | has_face / score / state |
| `emotieff` | emotion / confidence / probabilities |
| `eye_metrics` | ear / perclos / blink_rate |
| `control` | observe_only=true / fsm_state / authority / last_event / command / safety |
| `trace` | 最近 12 条决策链（from → state → event → cmd） |
| `health` | video_fps / ws_clients / doa_age / gimbal_latency_ms |
| `conversation` | mode / active / state |

客户端可发送 `"request_state"` 文本消息立即拉取一次快照。其他 WS 消息被忽略（服务端只读）。

### 8.2 HTTP 轮询回退

```bash
curl http://localhost:8001/api/state
```

返回 `{"type":"state_snapshot","data":{...}}`。

---

## 9. 前端页面说明与验证手册

### 9.1 双页面访问入口

| 页面 | 路由 | 定位 | 数据来源 |
|---|---|---|---|
| PAGE 1 控制台 | `/control` 或 `/v2` | 实时 FSM 观测 + 感知 + 遥测 | FastAPI 真实后端（WebSocket + MJPEG） |
| PAGE 2 Demo | `/home`（`/` 重定向） | 产品演示 | 纯 mock，无任何硬件调用 |

```text
http://localhost:8001/control     # PAGE 1
http://localhost:8001/home        # PAGE 2
http://<局域网IP>:8001/control    # 平板/手机访问 PAGE 1
```

---

### 9.2 PAGE 1 控制台界面说明（`/control`）

> 访问路径：`http://localhost:8001/control`（FastAPI 必须运行）  
> 定位：**只读观测**。展示真实感知数据 + FSM 决策镜像 + 硬件遥测，不发出任何控制指令。

#### 布局

```
┌─────────────────────────────────┬──────────────────────────┐
│                                 │  FSM 状态机              │
│      实时视频                    │  控制权 (Authority)       │
│      MJPEG + 检测框 overlay      │  决策链 (Decision Chain)  │
│                                 │  决策 Trace 日志         │
│                                 ├──────────────────────────┤
│                                 │  云台遥测 (Gimbal)        │
│                                 │  感知通道 (Perception)    │
│                                 │  单人分析                 │
│                                 │  系统健康                 │
└─────────────────────────────────┴──────────────────────────┘
```

#### 各面板说明

**① 实时视频**
- MJPEG 流 `/video_feed`，直接来自 reCamera SSCMA
- overlay 叠加：蓝色框 = 人体检测框；绿色框 = 人脸；关键点连线
- 视频不加载：检查 `video.connected = true`，确认 SSCMA :8090 可达

**② FSM 状态机**
- 5 个状态节点：`IDLE` `AUDIO_SEARCH` `VISION_TRACK` `FUSED_TRACK` `LOST`
- 高亮节点 = 当前 FSM 状态（来自 observe-only Orchestrator）
- 顶部徽章：`OBSERVE · READ-ONLY`（提示：此 FSM 不控制云台）
- **验证**：进入摄像头画面 → 应变为 `VISION_TRACK`；说话 → 应变为 `FUSED_TRACK`

**③ 控制权（Authority）**
- `AUDIO` = 声源主导（AUDIO_SEARCH 态）
- `VISION` = 视觉主导（VISION_TRACK 态）
- `FUSION` = 音视融合（FUSED_TRACK 态）
- `IDLE` = 无目标
- 与 FSM 状态节点联动高亮

**④ 决策链（Decision Chain）**
- Last Event：最近触发的感知事件（`vision/target_detected`、`audio/speech_detected` 等）
- Last Command：observe Orchestrator 计算出"本该发出"的命令（`yaw=xxx, pitch=xxx, reason=vision_track`）
- Safety Gate：`PASS` / `BLOCK·rate_limit`（observe SafetyLayer 以 5Hz 限速，BLOCK 是正常展示状态）
- **注意**：Command 这里仅是观测计算结果，不会实际发到云台

**⑤ 决策 Trace 日志**
- 滚动记录最近 12 条 FSM 状态迁移
- 格式：`HH:MM:SS  IDLE → VISION_TRACK  [event: vision/target_detected]  cmd: vision_track`
- **验证**：走进画面 → 应出现 `IDLE → VISION_TRACK` 条目

**⑥ 云台遥测（Gimbal Telemetry）**
- 来源：`RecameraClient.get_status()` 硬件 readback，每 500ms 刷新
- 字段：`yaw` / `pitch` / `speed` / `mode` / `connected`
- dry-run 模式下全部显示 `null`；`--no-dry-run` 后显示真实值
- 若 main_phase3.py 正在控制，yaw/pitch 会随追踪实时变化（**这是验证真实控制的关键指标**）

**⑦ 感知通道（Perception）**
- DOA 角度（度）：ReSpeaker 实时方向，0=正前方，90=右，270=左
- 人体数量（pose.count）：当前帧检测到的人
- 声音（has_speech）：当前 speech hold 状态
- 视觉丢帧数（vision_lost_frames）：连续丢失目标帧数（>30 触发 LOST）

**⑧ 单人分析**
- 专注度（attention.score）：0–100，EMA 平滑
- 情绪（emotieff.emotion + confidence）：8 类
- 眼部：EAR / 眨眼率 / PERCLOS

**⑨ 系统健康**
- video_fps：SSCMA 视频帧率（正常 10–30 fps）
- ws_clients：当前连接的 WebSocket 客户端数
- doa_age：上次有效 DOA 包的时间（秒，>1s 则声音跟随暂停）
- gimbal_latency_ms：上次 get_status() 的 RTT

---

### 9.3 PAGE 1 端到端验证清单

运行 **模式 M3**（FastAPI + main_phase3.py）后，在 PAGE 1 逐项确认：

```
[ ] 视频显示正常（无黑屏，FPS > 5）
[ ] 进入画面 → FSM 节点从 IDLE 变为 VISION_TRACK（3 帧 debounce，约 0.3s）
[ ] Decision Trace 出现 "IDLE → VISION_TRACK" 条目
[ ] Last Command 显示 yaw/pitch 数值和 reason=vision_track
[ ] 说话 → FSM 变为 FUSED_TRACK（需要 DOA speech_detected + vision 同时）
[ ] DOA 角度显示实时数值（ReSpeaker 有音频输入时）
[ ] 云台遥测 yaw/pitch 随人脸位置变化（main_phase3.py 驱动，readback 确认）
[ ] 人离开画面 → 30 帧 debounce 后进入 LOST → 延迟后回到 IDLE
[ ] 系统健康面板：doa_age < 1.0，video_fps > 10
```

---

### 9.4 PAGE 2 Demo 界面说明（`/home`）

> 访问路径：`http://localhost:8001/home`（FastAPI 运行与否均可直接打开）  
> 定位：**产品演示**，无任何硬件依赖。

- `isFilePreview = true`：所有 `fetch` / POST 调用被强制短路，不发出网络请求
- `mockState()` 动画：情绪每 6 秒轮换、专注度正弦波动（0–100）、DOA 角度缓慢扫描
- 功能展示：情绪监测、专注度、多人场景、情绪日记、LLM 对话、护眼/久坐/呼吸提醒
- **不显示任何真实硬件状态**；所有数字均为 mock 演示值
- 无需 reCamera 在线、无需 ReSpeaker、无需 FastAPI 运行

---

## 10. API 速查

### 10.1 状态与视频
```bash
curl http://localhost:8001/api/health
curl http://localhost:8001/api/state
curl http://localhost:8001/api/snapshot --output snapshot.jpg
```

### 10.2 云台状态（只读 readback）
```bash
curl http://localhost:8001/api/gimbal/state
```
返回：`{"connected": bool, "yaw": float|null, "pitch": float|null, "speed": int|null, "mode": str|null}`

**注意**：FastAPI 侧已删除所有云台控制路由（yaw/pitch/speed/stop/standby/sleep/calibrate）。控制只能通过 `main_phase3.py`。

### 10.3 LLM 对话
```bash
curl http://localhost:8001/api/chat/status
curl -X POST http://localhost:8001/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"今天状态不错"}'
```

### 10.4 对话会话
```bash
curl -X POST http://localhost:8001/api/conversation/start \
  -H 'Content-Type: application/json' \
  -d '{"save_audio":false}'
curl http://localhost:8001/api/conversation/state
curl -X POST http://localhost:8001/api/conversation/stop \
  -H 'Content-Type: application/json' \
  -d '{"finalize":true}'
```

### 10.5 视频调试
```bash
curl http://localhost:8001/api/debug/video
```

### 10.6 监测模式与会议记录（v4.3 新增）

| 路由 | 方法 | 说明 |
|------|------|------|
| `/api/tracking_mode` | POST | 切换 `{"mode":"single"\|"multi"}` |
| `/api/single_track/start` | POST | 开启情绪/专注监测 |
| `/api/single_track/stop` | POST | 停止情绪/专注监测 |
| `/api/multi_track/start` | POST | 开启多人场景；`{"save_audio":true}` 同时启动录音 |
| `/api/multi_track/stop` | POST | 停止多人场景；`{"finalize":true}` 关闭录音并写入磁盘 |
| `/api/meeting/summarize` | POST | 转写当前 session WAV 片段 + DeepSeek 摘要 → 返回 JSON |

---

## 11. Debug 方法

### 11.1 页面/API 冒烟
```bash
curl -o /dev/null -w "%{http_code}" http://localhost:8001/control      # 200
curl -o /dev/null -w "%{http_code}" http://localhost:8001/home         # 200
curl -o /dev/null -w "%{http_code}" http://localhost:8001/api/state    # 200
curl -o /dev/null -w "%{http_code}" http://localhost:8001/api/health   # 200
```

### 11.2 视觉 Debug

检查 `/api/state`：
```text
video.connected        # SSCMA 是否连接
video.fps              # 帧率（正常 10–30）
pose.count             # 检测到的人数
control.fsm_state      # FSM 当前状态
control.authority      # 当前控制权（idle/audio/vision/fusion）
control.safety.ok      # 安全门是否通过
trace                  # 最近决策链
```

### 11.3 DOA Debug
```bash
ss -lntp | grep 9999
python3 tools/send_doa_tcp.py --host 127.0.0.1 --mock-angle 35
curl http://localhost:8001/api/state | python3 -m json.tool
```

重点检查：
```text
doa.available = true
doa.packet_count > 0
doa.doa_deg = 35
doa.has_speech = true
doa.age < 1
control.fsm_state = "AUDIO_SEARCH"   # DOA + speech → 触发
```

### 11.4 云台 Debug
```bash
curl http://localhost:8001/api/gimbal/state
```
```text
connected: true    # RecameraClient 已连接
yaw/pitch          # 硬件 readback（dry-run 下为 null）
```

dry-run 确认：
```bash
python3 recamera_fastapi.py --no-dry-run   # 启用真实 readback
```

### 11.5 情绪模型
```bash
python3 tools/check_emotion_model.py
```

### 11.6 FSM 观测

PAGE 1（`/control`）提供实时 FSM 可视化：
- 5 状态节点高亮当前态
- 决策 trace 滚动显示 `event → from → state → cmd`
- safety 安全门 PASS/BLOCK 状态

`control.observe_only = true` 表示这是只读镜像，不驱动云台。

### 11.7 会议记录 Debug

1. **faster-whisper 未安装**：`/api/meeting/summarize` 返回 `{"ok":false,"error":"转写结果为空..."}`
   ```bash
   pip install faster-whisper   # tiny 模型首次运行自动下载 ~150MB
   ```

2. **sounddevice 无法识别 ReSpeaker**：
   ```bash
   python3 -c "import sounddevice; print(sounddevice.query_devices())"
   ```
   找到 ReSpeaker 的设备 index，在 `recamera_fastapi.py` 的 `_ensure_conversation_recorder()` 中传入 `device=<index>`。

3. **"整理会议记录"按钮不显示**：按钮仅在多人场景激活时出现。检查 localStorage `xinyu_multi_scene_running` 是否为 `"true"`，并确认 `toggleMultiScene()` 已成功调用。

4. **DeepSeek 未配置时摘要质量差**：摘要降级为前100字原文，设置 `DEEPSEEK_API_KEY` 环境变量后质量大幅提升。

---

## 12. 排障

### 12.1 reCamera 不可达
```bash
ping 192.168.106.85
```
失败时通过 USB SSH 重新查询 `wlan0`。

### 12.2 视频未连接

**第一步：诊断**
```bash
nc -zv 192.168.106.85 8090
curl http://localhost:8001/api/health
```

**错误类型区分：**

| 错误 | 含义 | 解决方法 |
|------|------|----------|
| `Connection refused` | 设备在线但 SSCMA 未运行 | 见下方"启动 SSCMA" |
| `Connection timed out` / `No route to host` | 设备不可达或 IP 错误 | 见 §12.1，SSH 重查 wlan0 IP |

设备 IP 自动加入 `NO_PROXY`，避免代理干扰。

**启动 SSCMA（当出现 `Connection refused` 时）：**

SSCMA 是 reCamera 上的 AI 推理服务，不随设备开机自动运行，需手动部署模型后启动。

方法 1 — 官方 Web UI（推荐）：
1. 浏览器打开 `http://192.168.106.85`
2. 进入模型部署页面，选择 YOLO 模型（如 YOLO11n），点击**部署 / 运行**
3. 等待加载完成，`:8090` WebSocket 自动开放

方法 2 — SSH 排查：
```bash
ssh recamera@192.168.106.85   # 密码：recamera0526_
ps aux | grep -i sscma        # 查看是否有 SSCMA 进程
systemctl status sscma 2>/dev/null
```

**确认 SSCMA 已启动：**
```bash
nc -zv 192.168.106.85 8090    # 应输出 "succeeded"
# 此后我们的服务日志会出现：📷 SSCMA connected
```

### 12.3 云台不动（main_phase3.py 侧）
```bash
python3 main_phase3.py --enable-control --fps 5 --max-cycles 10
```
查看日志是否有 `CONNECTED via Socket.IO`。

FastAPI 侧云台 readback 为 null 属正常（dry-run 模式下 `get_status()` 返回 `{"mode":"dry_run","connected":false}`）。

### 12.4 `packet_count = 0`（DOA）
- DOA 发送端未启动
- 主机地址/端口不一致
- Windows 防火墙阻止 TCP 9999
- 发送内容格式无法解析

### 12.5 情绪模型不可用
模型失败不阻塞视频和感知基础功能；`emotieff.emotion` 会为 null。

### 12.6 PAGE 1 safety 显示 `BLOCK·rate_limit`
正常现象：observe-only SafetyLayer 以 5Hz 限速，与真实控制行为一致，用于展示安全门工作状态。

---

## 13. 安全原则

1. 默认 dry-run：FastAPI 和 main_phase3.py 默认不发真实控制指令。
2. 单控制平面：`apply_command` 唯一调用点为 `main_phase3.py`；FastAPI 永远不调用。
3. 观测 ≠ 控制：FastAPI 内 `_observer` 是只读 FSM 镜像，不等于第二控制平面。
4. 遥测来源唯一：FastAPI 云台遥测仅来自 `RecameraClient.get_status()`（硬件 readback），不使用任何控制 state mirror。
5. 首次真实控制前先做视频、DOA、API 验收。
6. 不把"FSM 状态已更新"误认为"云台已动作"——控制进程（main_phase3.py）需独立运行才驱动硬件。
7. 急停：`main_phase3.py` 在 atexit / SIGINT / SIGTERM 时自动发送 stop 指令。

---

## 14. 当前边界

已稳定打通：
- reCamera Wi-Fi 视频（SSCMA）
- FastAPI MJPEG / WebSocket
- 单人脸追踪（FaceTrackerV2）+ 人体检测（YOLO11 pose）
- 情绪与专注状态
- TCP DOA 接收 + FSM observe 镜像
- LLM 对话与日记
- 单 FSM 控制平面（main_phase3.py）
- 两页前端：控制台（只读）+ Demo（mock）

尚未纳入标准链路：
- 远程音频流传输
- 说话人分离（diarization，DOA 方向区分已接入）
- DOA 安装偏移校准配置
- Wake Word 唤醒词

---

## 15. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 4.3 | 2026-06-26 | 情绪监测/专注计时/DOA 实时接通真实后端（`isFilePreview=false`）；新增 6 个 POST 路由（tracking_mode / single_track / multi_track / meeting/summarize）；修复 `/api/chat` 重复路由 bug，接入 DeepSeek + LLMReflect 降级；`/api/reflect` diary 模式增强（user_text / duration_min / valence）；日记写入后注入聊天回应；周趋势图读 localStorage 实际数据；会议记录功能（faster-whisper + DeepSeek 摘要 → 独立 `xinyu_meeting_notes`）；新增 `audio/transcriber.py` |
| 4.2 | 2026-06-26 | 重写 §6 启动流程（三模式 M1/M2/M3）；新增 §9 前端页面说明与验证手册（PAGE 1 面板详解 + 端到端验证清单 + PAGE 2 说明）；章节重编号 §9→§15 |
| 4.1 | 2026-06-26 | 更新设备 IP → 192.168.106.85；密码更新为 recamera0526_；新增 §7 ReSpeaker XVF3800 完整连接方案（方式 A usbipd WSL 直连 + 方式 B Windows TCP 转发），含验证命令、持久化 attach、环境变量、格式说明 |
| 4.0 | 2026-06-26 | 重写：对齐清理后架构；删除 GimbalController/影子控制链路相关说明；更新模块表、API 表、端口表；新增 FSM observe-only 说明；删除已不存在的页面/路由/API |
| 3.0 | 2026-06-19 | 旧版（已过期） |
