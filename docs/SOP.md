# reCamera Multimodal SOP 6.0

> 架构、部署、操作、验收与排障手册
> 版本：6.0
> 更新日期：2026-07-02
> 本文档以当前仓库代码为准；架构原理见 `docs/ARCHITECTURE.md`。

---

## 1. 硬件连接与部署前置

> 在启动任何程序之前，先完成本章的硬件连接、Node-RED 部署和环境变量配置。

### 1.1 reCamera 连接与地址获取

reCamera 有两个网络接口：

| 接口 | 地址 | 用途 |
|---|---|---|
| USB（CDC-ECM） | `192.168.42.1`（固定） | 初始化、SSH 查询 wlan0 地址 |
| 无线（wlan0） | DHCP 分配 | 正常运行时的设备地址 |

**获取无线地址：**

```bash
ssh recamera@192.168.42.1
ip addr show wlan0
```

记下 `wlan0` 的 IPv4 地址作为 `<RECAMERA_IP>`。DHCP 重新分配或网络变化后需重新查询。

**端口连通性验证：**

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>

ping -c 3 "$RECAMERA_DEVICE_IP"
nc -zv "$RECAMERA_DEVICE_IP" 8090   # SSCMA 视频流（WebSocket）
nc -zv "$RECAMERA_DEVICE_IP" 1880   # Node-RED 云台控制
nc -zv "$RECAMERA_DEVICE_IP" 22     # SSH 维护入口
```

8090 端口需在设备 Web 页面（`http://<RECAMERA_IP>:80`）启动模型部署后才可达。

---

### 1.2 ReSpeaker USB 连接（WSL）

ReSpeaker XVF3800 通过 usbipd 转发到 WSL，提供两路功能：

- **USB HID（control interface）**：DOA 方位角、VAD 语音标志、WS2812 LED 灯效
- **USB Audio Class**：多声道录音，由 `sounddevice` 独立读取

**步骤 1 — 查询 BUSID（Windows）：**

```bash
usbipd.exe list
```

找到 VID 为 `2886`、PID 为 `001a` 的条目，记录其 `<BUSID>`（格式如 `1-4`）。

**步骤 2 — 绑定（管理员 PowerShell）：**

```powershell
usbipd bind --busid <BUSID>
```

`Shared` 状态后只需执行一次，重启 Windows 后才需重做。

**步骤 3 — Attach 到 WSL：**

```bash
usbipd.exe attach --busid <BUSID> --wsl
```

**步骤 4 — 验证 USB 识别：**

```bash
lsusb | grep 2886
```

应显示 `Seeed Technology Co., Ltd` 或 `XVF3800`。

**步骤 5 — 查询音频设备索引：**

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

找到名称包含 `ReSpeaker`、`XVF3800` 或 `USB Audio`，且 `max_input_channels > 0` 的条目，记录其数字索引 `<AUDIO_DEVICE_INDEX>`。

确认所选设备：

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices(<AUDIO_DEVICE_INDEX>))"
```

> **注意**：`<AUDIO_DEVICE_INDEX>` 是 WSL 中 `sounddevice` 枚举的设备索引，不是 VID/PID，也不是 `usbipd` 的 BUSID。USB 重新 attach、WSL 重启或音频设备增减后索引可能变化，每次部署时应重新查询。

**会话结束后归还给 Windows：**

```bash
usbipd.exe detach --busid <BUSID>
```

---

### 1.3 Node-RED Bridge 部署与验证

云台控制必须通过 Node-RED bridge，`main_phase3.py` 启动前必须完成此步骤。

**安装步骤：**

1. 打开 `http://<RECAMERA_IP>:1880`
2. 在 Palette Manager 中确认或安装 `node-red-contrib-seeed-recamera`
3. 将 `deploy/node_red/recamera_control_bridge.json` 导入新 Flow 并点击 **Deploy**

**Bridge 暴露的端点：**

| 端点 | 方法 | 作用 |
|---|---|---|
| `/recamera-control/v1/status` | GET | 电机 readback 和连接状态 |
| `/recamera-control/v1/command` | POST | 双轴绝对/相对运动命令 |
| `/recamera-control/v1/stop` | POST | 紧急停止 |
| `/recamera-control/v1/calibrate` | POST | 执行 `gimbal cali`（撤销 lease） |

**状态验证（双轴电机就绪后才返回 200，否则 503）：**

```bash
curl "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/status"
```

期望响应包含 `connected=true`、真实 `yaw/pitch`、双轴 speed 和 `source=motor_readback`。

**可选冒烟测试（确认电机响应后立即 stop）：**

```bash
curl -X POST "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/command" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"absolute","yaw":180,"pitch":90,"yaw_speed":180,"pitch_speed":180}'

curl -X POST "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/stop" \
  -H 'Content-Type: application/json' -d '{"stop":true}'
```

Bridge 不可达时真实控制 fail closed，不会静默降级为 dry-run。

---

### 1.4 环境变量速查

**必填变量（按需设置）：**

| 变量 | 示例值 | 必填时机 |
|---|---|---|
| `RECAMERA_DEVICE_IP` | `192.168.x.x` | **始终必填**，两个终端都要设置 |
| `RECAMERA_DOA_SOURCE` | `usb` | ReSpeaker USB 直连时（生产环境） |
| `RECAMERA_AUDIO_DEVICE` | `2` | 会议录音时；值来自 1.2 第 5 步 |
| `DEEPSEEK_API_KEY` | `sk-xxx` | 启用 LLM 对话、日记自动回复、会议摘要 |

**可选覆盖（有合理默认值，通常无需设置）：**

`DEEPSEEK_API_URL` / `DEEPSEEK_MODEL` / `DEEPSEEK_MAX_TOKENS` / `RECAMERA_WHISPER_MODEL` / `RECAMERA_DOA_HOST` / `RECAMERA_DOA_PORT` / `RECAMERA_DOA_SPEECH_HOLD`

完整变量说明见第四章 4.4 节。

**HTTPS / PWA（非 localhost 访问时需要）：**

```bash
./tools/make_pwa_cert.sh <PC_LAN_IP>
# 生成 certs/xinyu-key.pem 和 certs/xinyu-cert.pem
# 启动 FastAPI 时追加：--ssl-keyfile certs/xinyu-key.pem --ssl-certfile certs/xinyu-cert.pem
```

---

## 2. 启动系统

完成第一章所有步骤后执行。

### 2.1 完整系统（两个终端）

**终端 1 — FastAPI（视频、感知、录音、Dashboard）：**

```bash
cd ~/recamera_multimodal
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
export RECAMERA_DOA_SOURCE=usb
export RECAMERA_AUDIO_DEVICE=<AUDIO_DEVICE_INDEX>
export DEEPSEEK_API_KEY=sk-xxx          # 可选

python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
```

**终端 2 — 控制运行时（云台 FSM、Orchestrator、SafetyLayer）：**

```bash
cd ~/recamera_multimodal
export RECAMERA_DEVICE_IP=<RECAMERA_IP>

python3 main_phase3.py \
  --enable-control \
  --gimbal-ip "$RECAMERA_DEVICE_IP" \
  --manual-control \
  --fps 10
```

**打开 Dashboard：**

```text
http://localhost:8001/home      # 产品页（/home 重定向）
http://localhost:8001/control   # 控制调试台
```

关键参数说明：

- `--enable-control`：连接真实 SSCMA 和云台；缺少设备地址时立即退出。
- `--manual-control`：在 `127.0.0.1:8765` 启动 EventBus，使 Dashboard 云台 UI Event 能进入控制运行时；省略此参数则 Dashboard 无法建立 feature session。
- `--gimbal-ip` 后必须传入展开后的变量值，不要把环境变量名称本身当作设备地址。

> Dashboard 中输入设备地址只覆盖当前 FastAPI 进程的内存配置，不写入 shell 环境变量，不修改已运行的 `main_phase3.py`，重启后失效。详见第五章 5.3 节。

### 2.2 无设备地址模式

FastAPI 可在没有设备地址时启动：

```bash
cd ~/recamera_multimodal
python3 recamera_fastapi.py
```

页面和非视频 API 可用，视频状态显示"未配置"。进入 `/control` 后，在顶部"设备地址"输入框填写 `<RECAMERA_IP>` 并点击"保存并重连视频"。

FastAPI 重启后若希望自动恢复地址，使用 `RECAMERA_DEVICE_IP` 或 `--device-ip`。

**Mock 控制运行时（不连接真实设备）：**

```bash
python3 main_phase3.py --mock --max-cycles 30 --log-level DEBUG
```

### 2.3 停止

在两个终端分别按 `Ctrl+C`。`main_phase3.py` 在正常退出时发送 stop；仍应目视确认设备静止。

紧急情况：

1. 先停止 `main_phase3.py`。
2. 确认 EventBus 端口不再监听：`ss -lntp | grep 8765`
3. 必要时断开设备电源或网络。

---

## 3. 架构与控制边界

### 3.1 唯一控制链

```text
Dashboard UI
  -> FastAPI UI Event emitter
  -> EventBus (TCP 127.0.0.1:8765, newline-delimited JSON)
  -> main_phase3.py control runtime
  -> FSM state transition
  -> Orchestrator decision
  -> ControlCommand
  -> SafetyLayer hard gate
  -> RecameraClient.apply_command()
  -> reCamera gimbal (via Node-RED bridge :1880)
```

视觉控制事件在 `main_phase3.py` 内由 SSCMA 输入转换为统一 Event，再进入同一个 FSM、Orchestrator、SafetyLayer 和硬件出口。

### 3.2 模块职责

| 模块 | 当前职责 | 禁止事项 |
|---|---|---|
| `recamera_fastapi.py` | 页面、视频、感知、录音、telemetry、UI Event emitter | 不调用 `RecameraClient`，不直接控制硬件 |
| `core/event_bus.py` | 传输统一 Event | 不做状态转移或控制决策 |
| `main_phase3.py` | 唯一 control runtime | 不允许第二硬件控制平面 |
| `core/fsm.py` | 纯状态机 | 不保存 yaw/pitch intent，不生成命令 |
| `core/orchestrator.py` | 唯一 `ControlCommand` 决策源 | 不访问 FastAPI 或感知模块内部状态 |
| `core/safety_layer.py` | 对最终命令 allow/block | 不改写命令，不生成替代命令 |
| `hardware/recamera_client.py` | 唯一硬件出口 | 不决定业务意图 |

### 3.3 统一 Event

所有控制输入应使用以下 envelope：

```json
{
  "type": "vision|audio|ui|system",
  "name": "event_name",
  "payload": {},
  "timestamp": 1750000000000,
  "source": "source_name"
}
```

HTTP 200 或 EventBus `accepted=true` 只表示事件已被控制运行时接收，不等同于硬件已完成动作。命令仍可能被 SafetyLayer 拦截或因硬件连接失败而未执行。

---

## 4. 安装依赖与模型

### 4.1 Python 依赖

```bash
cd ~/recamera_multimodal
python3 -m pip install -r requirements.txt --break-system-packages

# FaceTrackerV2 推荐依赖
python3 -m pip install insightface --break-system-packages

# 会议转写
python3 -m pip install faster-whisper --break-system-packages

# 可选：会议降噪与 WebRTC VAD；缺失时系统自动回退 RMS 分段
python3 -m pip install noisereduce webrtcvad-wheels --break-system-packages
```

### 4.2 模型资源

```bash
cd ~/recamera_multimodal

# 手势识别模型
curl -L --fail \
  -o models/gesture_recognizer.task \
  https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task

# ASR tiny 模型预热；模型会进入 Hugging Face 本地缓存
python3 - <<'PY'
from faster_whisper import WhisperModel
WhisperModel("Systran/faster-whisper-tiny", device="cpu", compute_type="int8")
print("faster-whisper tiny ready")
PY
```

可通过 `RECAMERA_WHISPER_MODEL` 覆盖默认 ASR 模型；默认值为 `Systran/faster-whisper-tiny`。

### 4.3 统一控制面板验收

打开 `http://localhost:8001/control`，逐个页面验证：

| 页面 | 启动 | 终止 | 关键验收 |
|---|---|---|---|
| 人脸追踪与分析 | 启动功能 | 终止功能 | 情绪、专注、EAR/PERCLOS 更新；云台命令来自 main runtime |
| 声源 yaw 跟随 | 启动功能 | 终止功能 | DOA/VAD 更新，yaw-only 控制，pitch 不自动跟随 |
| 会议录音 | 启动功能 | 终止并保存 | 录音状态、VAD 分段、音频处理状态、可选 yaw 跟随和摘要接口可用 |
| 手势交互 | 启动功能 | 终止功能 | `gesture.available=true`，五类 intent 只更新 UI，不控制云台 |
| 健康与 PWA | 启动功能 | 终止功能 | 护眼/久坐/喝水/疲劳/低专注/情绪关心状态可观察 |
| LLM 与日记 | 启动功能 | 终止功能 | DeepSeek 有 key 时在线回复，无 key 时本地 fallback |
| 手动云台 | 启动功能 | 终止功能 | D-Pad 只在当前 manual session 有效 |

每个页面的 `Standby`、`Sleep`、`Stop`、`Calibrate`：

- `Standby`：`yaw=180, pitch=90, speed=360`
- `Sleep`：`yaw=180, pitch=175, speed=360`
- `Calibrate`：通过 Node-RED bridge 执行 `gimbal cali`，并撤销当前设备租约

启动类 API 成功后返回 `session_id`。前端必须保存该 session，并在 stop、heartbeat、页面切换和 `beforeunload` 中继续携带；缺少 `session_id` 的 stop 请求应返回 `ok=false` 和 `reason=session_id_required`。

后端 `lease_ms=1500`（1.5 秒）；前端每 **750ms** 发送一次心跳（`POST /api/control/heartbeat {session_id}`）以保持租约有效。

### 4.4 完整环境变量参考

| 变量 | 默认值 | 用途 |
|---|---|---|
| `RECAMERA_DEVICE_IP` | 空 | reCamera 地址，推荐配置 |
| `RECAMERA_BASE_URL` | 空 | 兼容性的 HTTP base URL fallback |
| `DEEPSEEK_API_KEY` | 空 | LLM 对话、日记和会议摘要 |
| `DEEPSEEK_API_URL` | DeepSeek API | OpenAI-compatible API 地址 |
| `DEEPSEEK_MODEL` | 项目默认模型 | 模型名称 |
| `DEEPSEEK_MAX_TOKENS` | `600` | 单次输出上限 |
| `RECAMERA_DOA_SOURCE` | `usb` | 生产环境使用 `usb`；`tcp` 为无 USB attach 时的备用 |
| `RECAMERA_DOA_HOST` | `0.0.0.0` | TCP DOA 监听地址 |
| `RECAMERA_DOA_PORT` | `9999` | TCP DOA 监听端口 |
| `RECAMERA_DOA_SPEECH_HOLD` | `0.8` | speech hold 秒数 |
| `RECAMERA_AUDIO_DEVICE` | 系统默认 | 会议录音设备索引（来自 1.2 第 5 步） |
| `RECAMERA_WHISPER_MODEL` | `Systran/faster-whisper-tiny` | ASR 模型 |

LLM 未配置时，相关接口回退到本地轻量逻辑，不影响视频和基础感知。

---

## 5. Dashboard 操作

### 5.1 页面入口

| 路由 | 页面 | 数据 |
|---|---|---|
| `/control`、`/v2` | Control Dashboard | FastAPI 真实视频、感知、录音状态和 UI Event 请求 |
| `/home` | 产品 Demo | `/ws` 实时状态，失败时降级 `/api/state` polling |
| `/` | 重定向 | 跳转 `/home` |

Dashboard 左侧导航：

```text
单人场景
  - 人脸追踪与分析

多人场景
  - 声源 yaw 跟随
  - 会议录音

设备调试
  - 手动云台调试
```

### 5.2 页面生命周期

- 打开或切换到页面后只显示信息，不会自动启动该页功能。
- 必须点击当前页"启动功能"按钮。
- 启动成功后，前端保存返回的 `session_id`；stop、heartbeat（750ms）、页面切换和 `beforeunload` 都必须携带该 session。
- `/home` 优先使用 `/ws` 接收真实状态；WebSocket 断线后每 3s 重连，超过 10 次后停止重连并降级到 `/api/state` 1s polling；页面重新变为可见时自动复位重连计数并重连。
- `/home` 运行态以后端 `control.active_feature`、`session_id` 和 `conversation` 为准；localStorage 只保存用户偏好，不伪造"运行中"。
- 切换页面前，前端对旧页面发送对应 stop/deactivate 请求；缺少 `session_id` 的 stop 应被后端拒绝。
- 页面隐藏或关闭时 best-effort 发送带 session 的 stop（陪伴用 `sendBeacon` + `/api/single_track/stop`，会议用 `sendBeacon` + `/api/conversation/stop`）。
- 网络断开或进程强制终止时 best-effort 请求不保证送达；租约 1.5s 后自动到期。

### 5.3 设备地址输入

1. 在顶部输入 `<RECAMERA_IP>`。
2. 点击"保存并重连视频"。
3. 确认状态从"未配置"变为已配置。
4. 等待 `video_connected=true` 和摄像头画面恢复。
5. 若要真实控制云台，另行用同一地址启动或重启 `main_phase3.py`。

**地址配置优先级：**

1. CLI 显式参数（`--device-ip` 或 `--gimbal-ip`）
2. `RECAMERA_DEVICE_IP`
3. `RECAMERA_BASE_URL`（兼容 fallback）
4. Dashboard 输入（仅覆盖当前 FastAPI 进程内存，重启后失效）

FastAPI 和控制运行时是两个独立进程，Dashboard 更新地址后控制运行时不会自动同步。

**Dashboard 重连验证：**

```bash
curl http://localhost:8001/api/device/config

curl -X POST http://localhost:8001/api/device/config \
  -H 'Content-Type: application/json' \
  -d '{"device_ip":"<RECAMERA_IP>"}'
```

### 5.4 人脸追踪与分析

点击"启动功能"后，Dashboard 调用：

```text
POST /api/multi_track/stop
POST /api/single_track/start
POST /api/tracking_mode {"mode":"single"}
```

FastAPI 随后启用单人分析，包括摄像头、检测结果、情绪、专注、EAR（`eye_metrics.ear_avg`）、PERCLOS（`eye_metrics.perclos`）和眨眼率（`eye_metrics.blink_rate`）。成功后前端以 **750ms** 间隔调用 `POST /api/control/heartbeat {session_id}` 保持 1.5s 租约有效。

### 5.5 声源 yaw 跟随

点击"启动功能"后，Dashboard 调用：

```text
POST /api/single_track/stop
POST /api/multi_track/start {"save_audio":false}
POST /api/tracking_mode {"mode":"multi"}
```

页面展示 ReSpeaker DOA、实体 WS2812 灯环状态和 reCamera yaw readback。DOA Event 由 Orchestrator 转换为 yaw-only command；pitch 始终为空。

### 5.6 会议录音

点击"启动功能"后，Dashboard 调用：

```text
POST /api/conversation/start {"control_session":true, "save_audio":true}
```

返回 `session_id` 保存到前端；录音设备由 `RECAMERA_AUDIO_DEVICE` 决定。

离开页面或点击结束时调用：

```text
POST /api/conversation/stop {"session_id":"...", "finalize":true}
```

`/api/state.audio_processing` 报告录音预处理状态：

- `noise_suppression.enabled=true`：`noisereduce` 可用
- `vad_mode=webrtcvad`：WebRTC VAD 可用
- `vad_mode=rms`：依赖缺失，系统回退 RMS 分段

### 5.7 手动云台调试

1. 确认 `main_phase3.py` 使用 `--enable-control --manual-control` 运行。
2. 进入"手动云台调试"。
3. 点击"启动功能"解锁按钮。
4. 方向键调用 `/api/gimbal/move`，回中调用 `/api/gimbal/home`。

Orchestrator 将 D-Pad delta 限制到每轴最大 `2.5` 度，SafetyLayer 再对最终命令执行 hard-gate 校验。

---

## 6. ReSpeaker DOA 进阶

> USB 连接步骤见 **1.2 节**。本章只涵盖 TCP 备用模式和验证方法。

### 6.1 TCP DOA 备用

FastAPI 默认监听 `0.0.0.0:9999`（仅当 `RECAMERA_DOA_SOURCE=tcp` 时启用）。

查询 WSL 地址：

```bash
hostname -I
```

Windows 发送端：

```cmd
python tools\send_doa_tcp.py --host <WSL_IP> --mock-angle 35
```

WSL 本机测试：

```bash
python3 tools/send_doa_tcp.py --host 127.0.0.1 --mock-angle 35
```

推荐 JSON 格式：

```json
{"azimuth_deg":35,"speech":true}
```

TCP 模式下 `respeaker.led.hardware=false`（实体 LED 不可用）。

### 6.2 DOA 与 LED 验证

```bash
curl http://localhost:8001/api/state | python3 -m json.tool
```

重点检查：

```text
doa.available = true
doa.packet_count > 0
doa.doa_deg = 35
doa.has_speech = true
doa.age < 1
respeaker.connected = true
respeaker.led.hardware = true   # USB 模式才有
```

完整闭环还应同时确认 `control.active_feature=multi_sound_yaw`、`gimbal.source=motor_readback` 和 yaw 数值变化。

---

## 7. API 与 EventBus 速查

### 7.1 状态和视频

```bash
curl http://localhost:8001/api/health
curl http://localhost:8001/api/state
curl http://localhost:8001/api/device/config
curl http://localhost:8001/api/debug/video
curl http://localhost:8001/api/snapshot --output snapshot.jpg
```

### 7.2 场景状态

```bash
curl -X POST http://localhost:8001/api/tracking_mode \
  -H 'Content-Type: application/json' -d '{"mode":"single"}'

curl -X POST http://localhost:8001/api/single_track/start
curl -X POST http://localhost:8001/api/single_track/stop

curl -X POST http://localhost:8001/api/multi_track/start \
  -H 'Content-Type: application/json' -d '{"save_audio":false}'

curl -X POST http://localhost:8001/api/multi_track/stop \
  -H 'Content-Type: application/json' -d '{"finalize":false}'
```

### 7.3 云台 UI Event

```bash
curl -X POST http://localhost:8001/api/gimbal/move \
  -H 'Content-Type: application/json' \
  -d '{"pan":5,"tilt":0}'

curl -X POST http://localhost:8001/api/gimbal/home
```

EventBus 未启动时响应包含 `accepted=false`、`authority=unreachable`；可达时包含 `accepted=true`、`authority=main_phase3`。

### 7.4 录音和会议摘要

```bash
curl -X POST http://localhost:8001/api/conversation/start \
  -H 'Content-Type: application/json' -d '{"save_audio":true}'

curl http://localhost:8001/api/conversation/state
curl http://localhost:8001/api/conversation/debug

curl -X POST http://localhost:8001/api/conversation/stop \
  -H 'Content-Type: application/json' -d '{"finalize":true}'

curl -X POST http://localhost:8001/api/meeting/summarize \
  -H 'Content-Type: application/json' -d '{}'
```

`/api/meeting/summarize` 失败时返回结构化错误码：

| 错误码 | 场景 | 操作提示 |
|---|---|---|
| `recording_not_started` | 未启动会议录音 | 先启动会议录音 |
| `no_segments` | 已启动但没有有效语音片段 | 先录到语音片段 |
| `asr_empty` | ASR 返回空文本或依赖不可用 | 检查语音时长、`faster-whisper` 和模型缓存 |

### 7.5 EventBus 端口

```bash
ss -lntp | grep 8765
nc -zv 127.0.0.1 8765
```

EventBus 只接受统一 Event JSON，每条消息以换行结束。

---

## 8. 分层验收

### 8.1 FastAPI 无设备模式

```bash
python3 recamera_fastapi.py
curl http://localhost:8001/api/health
curl http://localhost:8001/api/device/config
```

验收：`/control` 返回 200；`configured=false`；服务不因缺少设备地址退出。

### 8.2 视频重连

在 Dashboard 输入设备地址，或调用 `POST /api/device/config`。

验收：`configured=true`；`sscma_url` 使用输入地址；SSCMA 正常时 `video_connected=true`；`/video_feed` 显示实时画面。

### 8.3 EventBus

先只启动 FastAPI，调用 `/api/gimbal/home`，应得到 unreachable。再启动：

```bash
python3 main_phase3.py \
  --enable-control \
  --gimbal-ip "$RECAMERA_DEVICE_IP" \
  --manual-control
```

再次调用 `/api/gimbal/home`。

验收：EventBus 监听 `127.0.0.1:8765`；API 返回 `accepted=true`；authority 为 `main_phase3`；控制运行时日志出现对应事件处理。

### 8.4 Dashboard 生命周期

1. 进入任一页面，功能按钮保持锁定或空闲。
2. 点击"启动功能"后才调用对应 start API。
3. 启动响应中的 `session_id` 被保存；heartbeat、stop 和页面卸载都携带该 session。
4. 新页面不自动启动。
5. 切换页面或点击停止时，旧功能发送带 session 的 stop。
6. 缺少 `session_id` 的 stop 请求返回 `ok=false`，不会提前清空硬件 lease。
7. 隐藏并恢复页面后，UI 以后端 active state 为准。

### 8.5 `/home` 回归验收

> **前提**：确认 `recamera_fastapi.py` 的 `HOME_FILE` 已指向 `dashboard/home.html`。

基础连接：

1. 打开 `/home`，DevTools Network 看到 WebSocket 连接到 `/ws`。
2. 手动断开 WS 后，确认降级到 `/api/state` 1s polling；重新联网后恢复实时状态。
3. 模拟 WS 超过 10 次重连失败：应停止重连保持 polling；切换到后台再回来应复位计数并重连。

状态与感知：

4. `/api/state` 包含 `face_lock`、`sound_follow`、`doa`、`control.active_feature`（非 `control.feature`）和 `emotieff.valence`。
5. 专注评分回归：只对 fused 分数做平滑，不把同一分数当作 orientation/stability 二次加权。
6. 多人场景中 `pose.stable_count` 优先显示；短暂 `1,1,2,1,1` 抖动时不应跳变。

Session 和心跳：

7. 进入陪伴 Tab → Network 每 750ms 出现 `POST /api/control/heartbeat {session_id}`。
8. 离开陪伴 Tab → 心跳停止；`beforeunload` 时出现 `sendBeacon` 到 `/api/single_track/stop`。
9. 启动会议 → `POST /api/conversation/start {control_session:true, save_audio:true}`；结束 → `POST /api/conversation/stop {session_id, finalize:true}`。

日记与 LLM：

10. 保存日记 → emotion 字段使用用户选择而非无条件 `Neutral`；10s 内出现小屿 LLM 回复气泡，`conversation[0]` 有内容。
11. 日记详情页发送追加消息 → 出现用户气泡 + 小屿回复气泡 → 关闭再打开，对话内容仍在。
12. 修改昵称 → 发起聊天 → payload 包含 `user_name`；`emotion` 字段为中文（如"快乐"）。
13. DevTools 模拟限速超过 10s → 应出现降级提示，不出现未捕获异常。

周报：

14. 点击"让小屿写周报" → 请求 `/api/chat`；文本更新到页面；`xinyu_weekly_reports` 有新条目。
15. 切换到其他标签再回来 → 周报文本从 localStorage 重新渲染（非空白或旧文案）。

存储：

16. 三种情况调用 `/api/meeting/summarize`：确认分别得到 `recording_not_started`、`no_segments`、`asr_empty`。
17. 会议录音启动后，`audio_processing.vad_mode` 应为 `webrtcvad` 或 `rms`。
18. `navigator.storage.estimate()` 报告 >85% → 出现存储配额警告 toast。

### 8.6 真实硬件动作

只在周围无障碍物时测试：

1. 启动完整系统。
2. 在手动云台页点击"启动功能"。
3. 发送一次小幅 yaw delta。
4. 观察 EventBus 响应、控制运行时日志和设备实际动作。
5. 调用 home，再停止控制运行时。

不要仅根据 Dashboard 状态标签判定硬件动作成功。

---

## 9. 故障排查与安全停机

### 9.1 地址未配置或变量未展开

```bash
printf '%s\n' "$RECAMERA_DEVICE_IP"
```

为空时重新 export。日志若显示尝试连接名为 `RECAMERA_DEVICE_IP` 的主机，说明命令漏写了 `$` 和引号。

正确写法：`--gimbal-ip "$RECAMERA_DEVICE_IP"`

### 9.2 设备可达但视频断开

```bash
nc -zv "$RECAMERA_DEVICE_IP" 8090
curl http://localhost:8001/api/device/config
curl http://localhost:8001/api/debug/video
```

- `Connection refused`：设备在线，但 SSCMA 服务或模型未运行。在设备 Web 页面启动模型后再检查 8090。
- `Timed out` / `No route to host`：地址错误、路由或网络问题。

### 9.3 Dashboard 控制请求 unreachable

```bash
ss -lntp | grep 8765
```

确认 `main_phase3.py` 带 `--manual-control`，且 FastAPI 与控制运行时使用同一主机的 `127.0.0.1:8765`。若修改端口，当前 FastAPI EventBusClient 默认仍使用 8765，需同步修改。

### 9.4 控制事件 accepted 但云台不动

依次检查：

1. `main_phase3.py` 是否带 `--enable-control`。
2. 设备地址和 1880 端口是否可达（`nc -zv "$RECAMERA_DEVICE_IP" 1880`）。
3. Node-RED bridge 是否已部署并返回 `connected=true`（见 1.3 节验证命令）。
4. SafetyLayer 是否因 rate limit、范围或 safe mode 拦截。
5. 控制运行时日志是否出现命令应用失败。

### 9.5 DOA 没有数据

```bash
lsusb | grep -i '2886:001a'
python3 -c "from audio.respeaker_doa import ReSpeakerDOAReader; r=ReSpeakerDOAReader(); print(r.status())"
curl http://localhost:8001/api/state | python3 -m json.tool
```

USB 生产模式先确认 `RECAMERA_DOA_SOURCE=usb` 并检查 usbipd attach 步骤（见 **1.2 节**）。

TCP 备用模式（`RECAMERA_DOA_SOURCE=tcp`）才检查 `ss -lntp | grep 9999`，此时 `respeaker.led.hardware=false` 是预期行为。

### 9.6 会议录音失败

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
python3 -c "import os, sounddevice as sd; i=int(os.environ['RECAMERA_AUDIO_DEVICE']); print(sd.query_devices(i)); print('input channels=', sd.query_devices(i)['max_input_channels'])"
curl http://localhost:8001/api/conversation/debug
```

确认 `RECAMERA_AUDIO_DEVICE` 是 WSL 中 ReSpeaker 的 `sounddevice` 输入索引，且 `max_input_channels > 0`。索引错误时重新执行 **1.2 节**第 5 步。

`/api/state.audio_processing.fallback_reason` 显示 `noisereduce_unavailable` 或 `webrtcvad_unavailable` 时，录音仍使用 RMS 分段继续工作；需要增强链路时安装对应依赖后重启 FastAPI。

### 9.7 安全原则

1. FastAPI 不直接控制硬件。
2. 只有 `main_phase3.py` 能调用真实 `apply_command()`。
3. UI/manual 输入必须经过 EventBus 和 Orchestrator。
4. SafetyLayer 只允许或阻止最终命令，不修改命令。
5. 首次控制前清理云台运动范围内的障碍物。
6. 停止后目视确认硬件静止，不只依赖 HTTP 响应。

---

## 10. 健康陪伴功能操作与验收

### 10.1 前置检查

```bash
python3 -c "import cv2, mediapipe, numpy; print('vision dependencies ok')"
test -f models/face_landmarker.task && echo "face model ok"
test -f models/gesture_recognizer.task && echo "gesture model ok" || echo "gesture model missing"
```

手势模型缺失时系统降级，视频、情绪、专注、注视和控制功能不受影响。官方 Gesture Recognizer 模型放置为 `models/gesture_recognizer.task`。

### 10.2 启动

```bash
cd ~/recamera_multimodal
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
```

打开：

- 产品页：`http://<HOST>:8001/home`
- 状态：`http://<HOST>:8001/api/state`
- 调试台：`http://<HOST>:8001/control`

### 10.3 主动情绪干预验证

1. 保持单人面部进入画面，确认 `/api/state` 中 `emotieff`、`attention`、`eye_metrics` 和 `gaze` 有更新。
2. 观察 `proactive_intervention.reason`：初期通常为 `collecting`，置信不足为 `low_confidence`，未达阈值为 `below_threshold`。
3. 持续满足阈值约 3 分钟，确认 `active=true`、`message` 非空。
4. 触发后重复条件，确认 `cooldown_remaining_sec` 递减且不会重复激活（默认冷却 1800 秒）。

### 10.4 手势识别验证

```bash
ls -lh models/gesture_recognizer.task
curl -s http://127.0.0.1:8001/api/state | python3 -m json.tool
```

逐项验证 Open Palm、Closed Fist、Thumb Up、Thumb Down、Victory：

1. 手掌完整进入画面，保持光照稳定。
2. 同一手势连续保持至少 4 个识别帧。
3. 确认 `confidence >= 0.6`、`stable_frames >= 4`、intent 映射正确。
4. 首次稳定时 `intent_ready=true`，同 intent 3 秒内不会再次 ready。
5. 在 `/home` 确认对应动作：唤起、收起提醒、正负反馈、积极瞬间草稿。
6. 确认没有手势产生的云台或功能控制事件。

模型缺失时预期：`{"available":false,"intent_ready":false,"reason":"model_missing:models/gesture_recognizer.task"}`

### 10.5 注视方向估计验证

1. 正对镜头，确认 `gaze.available=true` 且 `state` 多数为 `center`。
2. 分别只用眼睛向左、向右和向下，观察方向趋势。
3. 离开画面，确认 `available=false`、`state=unknown`。
4. 查看 `attention.components.gaze` 与 `weights.gaze=0.15`。
5. 遮挡眼睛或制造关键点缺失，确认 attention 接口仍返回而不崩溃。

### 10.6 PWA 本地通知

Notification API 要求安全上下文（localhost HTTP 或 HTTPS）。

1. 通过 `https://<HOST>:<PORT>/home` 或 `http://localhost:8001/home` 打开页面。
2. 进入底部"建议"页，找到"本地提醒"。
3. 点击"开启提醒"，在浏览器权限框选择允许。
4. 点击"测试提醒"，确认系统通知出现。
5. 刷新页面，确认 `xinyu_notify_enabled` 和冷却记录仍保留。

localStorage 完整键清单（`home.html` 实际实现）：

| Key | 用途 |
|---|---|
| `xinyu_user_name` | 用户昵称 |
| `xinyu_diary_entries` | 日记数组（新格式：`{id, date, emotion, conversation[]}` ） |
| `xinyu_diary_calendar` | 旧日记格式（同步写入，向后兼容） |
| `xinyu_emotion_calendar` | 更旧格式（只在迁移时读取，不再写入） |
| `xinyu_weekly_reports` | 周报数组 |
| `xinyu_meeting_notes` | 会议整理历史（最近 20 条） |
| `xinyu_chat_YYYY-MM-DD` | 每日陪伴对话（最多 50 条/天） |
| `xinyu_notify_enabled` | 本地通知总开关（默认 `false`） |
| `xinyu_notify_last_sent` | 每类通知最近发送时间（防重，对象格式） |
| `xinyu_notify_style` | 通知节奏（`quiet` / `gentle` / `active`，默认 `gentle`） |
| `xinyu_control_session_id` | 陪伴 session（临时，`beforeunload` 时清除） |
| `xinyu_recording_session_id` | 会议 session（临时） |

> **注意**：`xinyu_water_last_at`、`xinyu_water_goal`、`xinyu_notify_quiet_hours`、`xinyu_notify_cooldowns`、`xinyu_notify_last_sent_by_type` 为规划阶段占位，**未在当前 `home.html` 中实现**。

六类提醒验收：

| 类型 | 触发条件 | 预期目标页 |
|---|---|---|
| 护眼 | 手动开启后 20 分钟到期 | `health` |
| 久坐 | 手动开启后 45 分钟到期 | `health` |
| 喝水 | 09:00-22:00，90 分钟未记录且未达目标 | `health` |
| 疲劳 | 异常眼部/向下 gaze + attention < 60，持续 5 分钟 | `health` |
| 低专注 | 专注记录开启，attention < 50，持续 10 分钟 | `health` |
| 情绪关心 | `proactive_intervention.active=true` | `home` |

### 10.7 降级与排障

| 现象 | 预期检查 | 处理 |
|---|---|---|
| `gesture.reason` 为 `model_missing` | 模型文件不存在 | 放置模型后重启 |
| 手势有名称但不触发 | 置信度、稳定帧或 3 秒冷却未满足 | 改善光照/距离，连续保持手势 |
| `gaze.available=false` | 无脸、关键点少于 477 或 MediaPipe 异常 | 检查 Face Landmarker、画面和日志 |
| 主动关心一直 `collecting` | 样本少于 20 或运行时间不足 | 保持有效输入并等待窗口积累 |
| 浏览器不弹通知 | 非安全上下文、权限拒绝、系统通知关闭 | 改用 HTTPS/localhost，重置站点权限 |
| 只有站内 toast | Notification 不支持或未授权 | 属于设计内降级；授权后再测试 |

### 10.8 安全确认

- 四项新功能不修改云台控制接口。
- FastAPI 不得直接调用 `RecameraClient.apply_command()`。
- 手势不会发出控制 Event。
- 通知不包含截图、日记正文或会议原文。

---

## 附录 A：闭环状态与变更记录

### A.1 当前闭环条件

1. 页面 start/stop/heartbeat 均转换为 Event，`main_phase3.py` 以 session token 和 1.5 秒租约维护唯一控制权。
2. 浏览器异常退出时，租约到期会自动生成 stop；旧标签页不能停止后来接管的新 session。
3. ReSpeaker USB control interface 提供 DOA/VAD 和实体 WS2812 DOA 灯效；USB Audio Class 提供会议录音。
4. DOA 经 EventBus 进入 Orchestrator，多人与会议跟随模式只生成 yaw，不修改 pitch。
5. 云台双轴命令和真实 angle/speed readback 通过配套 Node-RED Flow（见 **1.3 节**）。
6. Dashboard 地址仍只保存在当前 FastAPI 进程内存中，服务重启后需通过环境变量、CLI 或页面重新配置。

### A.2 当前已确认架构状态

| 检查项 | 结论 |
|---|---|
| Shadow hardware control path | **NO** |
| FastAPI 直接控制硬件 | **NO** |
| FSM 为纯状态机 | **YES** |
| Orchestrator 是唯一业务命令源 | **YES** |
| SafetyLayer hard gate only | **YES** |
| EventBus 统一承载 Dashboard 云台 UI Event | **YES** |
| Dashboard 场景启停与 control runtime 完整联动 | **YES，session + lease** |
| ReSpeaker DOA 到真实 yaw 控制闭环 | **YES** |
| ReSpeaker 实体 LED DOA 灯效 | **YES，USB 模式** |
| FastAPI 展示真实云台 readback | **YES，经 main_phase3 runtime snapshot** |

### A.3 6.0 变更记录

- **结构重组**：新增第一章，将 reCamera 连接（原 3.1/3.3）、ReSpeaker USB 连接与音频索引查询（原 7.1/7.2）、Node-RED Bridge 部署与验证（原 3.5）、环境变量速查（原 4.4 精简）统一提至文档最前。
- 启动命令（原第 1 章 + 第 5 章）合并为第二章，去除重复解释，保留可直接复制的命令块。
- 原第 7 章 ReSpeaker 仅保留 TCP 备用和 DOA/LED 验证，重编号为第六章。
- 故障排查 DOA 小节改为"见 1.2 节"，避免重复。
- 原第 11 章变更记录移至附录 A。
- 所有章节重编号：原 2→3、3→5（部分）、4→4、6→5、8→7、9→8、10→9、12→10。
- SOP 版本：5.3 → 6.0。

### A.4 历史变更（5.x）

- **5.3**：修正 `eye_metrics` 字段（`ear`→`ear_avg`）、`active_feature`（非 `feature`）、1.5s lease（非 2.5s）；更新会议 API（`/api/conversation/*`）；修正 localStorage 键清单，删除 5 个未实现幽灵键；扩展 9.5 回归验收清单（A-E 功能）。
- **5.2**：对齐 FastAPI 和 `main_phase3.py` 当前 CLI 参数；新增 feature session、1.5 秒租约、旧会话隔离；接通 ReSpeaker USB DOA、会议录音和实体 WS2812 DOA 灯效；接通 DOA audio Event 到 yaw-only Orchestrator 命令；新增 Node-RED 双轴 control/status bridge 和真实 CAN motor readback。
- **5.1**：将设备地址配置和 quick start 移到文档开头；删除已不存在的旧硬件模式切换流程；新增 Dashboard 地址输入作用域说明。
