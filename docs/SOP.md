# reCamera Multimodal SOP 6.2

> 架构、部署、操作、验收与排障手册
> 版本：6.2
> 更新日期：2026-07-10
> 本文档以当前仓库代码为准；架构原理见 `docs/ARCHITECTURE.md`。
> 官方 reCamera Gimbal 快速入门参考：<https://wiki.seeedstudio.com/cn/recamera_gimbal_getting_started/>

## 目录与接手指引

首次接手项目时，建议按下面顺序阅读和操作：

1. **硬件连接与官方入口**：先读 1.1，确认 reCamera IP、官方 dashboard、Node-RED workspace 和端口含义。
2. **ReSpeaker 接入**：需要 DOA、会议录音或 LED 时读 1.2。
3. **Node-RED bridge**：真实云台控制前必须读 1.3，确认 bridge 是本项目自写 flow，但调用官方 reCamera 节点。
4. **启动系统**：按 2.1 同时启动 FastAPI 和 `main_phase3.py`。
5. **控制边界**：读第 3 章，确认 FastAPI 只有事件和遥测职责，真实云台出口只有 `main_phase3.py -> RecameraClient -> Node-RED bridge`。
6. **页面操作**：使用 `/home` 或 `/control` 前读第 5 章。
7. **当前主要问题**：视频延迟和 ReSpeaker 录音清晰度先读 8.7，再决定是否进入性能调参或硬件复测。
8. **验收与排障**：功能交接先跑第 8 章，异常优先查第 9 章。

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

**官方 dashboard、Node-RED 与本项目页面的关系：**

Seeed 官方快速入门中，reCamera Gimbal 的官方 dashboard 通过设备 IP 访问。它是设备自带的预览和手动控制页面，用于确认硬件、Wi-Fi、模型和官方节点是否正常；本项目的 `/home` 与 `/control` 是运行在 PC/WSL 主机上的产品页和工程调试台，不替代设备初始化页面。

| 入口 | 地址 | 用途 | 本项目中的定位 |
|---|---|---|---|
| 官方预览 dashboard | `http://<RECAMERA_IP>/#/dashboard` 或 `http://<RECAMERA_IP>` | 查看 reCamera 画面、体验官方摇杆/滑块/自动跟踪/快捷按钮 | 硬件和官方 demo 验证入口，不作为本项目主 UI |
| 官方主页 | `http://<RECAMERA_IP>/#/init` | 设备初始化入口 | 首次设置或恢复后使用 |
| 官方网络配置 | `http://<RECAMERA_IP>/#/network` | 配置 Wi-Fi、查看设备 IP | 获取 `<RECAMERA_IP>` |
| 官方 Node-RED workspace | `http://<RECAMERA_IP>/#/workspace` | 查看/编辑设备侧 Node-RED flow | 可进入官方 flow，也可导入本项目 bridge |
| 原始 Node-RED | `http://<RECAMERA_IP>:1880` | Node-RED 编辑器和本项目 bridge HTTP 端口 | 导入 `deploy/node_red/*.json`，暴露控制 API |
| 本项目产品页 | `http://localhost:8001/home` | 心屿产品形态、会议、聊天、健康提醒 | 最终演示/产品入口 |
| 本项目控制台 | `http://localhost:8001/control` | 工程调试、状态遥测、功能启停 | 本项目调试和验收入口 |

官方 dashboard 中的 Sleep、Standby、Calibrate 和 Emergency Stop 只用于说明设备原生语义。本项目不会把官方 dashboard 当作控制主链路，而是把这些语义收敛到自己的 EventBus、session/lease、SafetyLayer 和 Node-RED bridge 中。

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

本项目 bridge 是**自写 Node-RED flow**，不是直接使用官方 demo dashboard flow；但它会调用 Seeed 官方 `node-red-contrib-seeed-recamera` 节点和设备侧命令来完成电机读写、校准和停止。这样做的目的，是把官方 demo 的手动 UI/示例逻辑替换成稳定的 HTTP contract，让 `main_phase3.py` 成为唯一控制运行时。

**安装步骤：**

1. 先打开官方 dashboard：`http://<RECAMERA_IP>/#/dashboard`，确认画面、电机和校准可用。
2. 打开 Node-RED workspace：`http://<RECAMERA_IP>/#/workspace`，或直接打开原始 Node-RED：`http://<RECAMERA_IP>:1880`。
3. 在 Palette Manager 中确认或安装 `node-red-contrib-seeed-recamera`。
4. 将 `deploy/node_red/recamera_control_bridge.json` 导入新 Flow 并点击 **Deploy**。
5. 如需 reCamera 扬声器闭环，再导入 `deploy/node_red/recamera_audio_bridge_supplement.json` 并点击 **Deploy**。

**Bridge 暴露的端点：**

| 端点 | 方法 | 作用 |
|---|---|---|
| `/recamera-control/v1/status` | GET | 电机 readback 和连接状态 |
| `/recamera-control/v1/command` | POST | 双轴绝对/相对运动命令 |
| `/recamera-control/v1/stop` | POST | 紧急停止 |
| `/recamera-control/v1/calibrate` | POST | 执行 `gimbal cali`（撤销 lease） |
| `/recamera-control/v1/audio/play` | POST | 可选语音闭环：保存 WAV 并用 `aplay -D <device>` 播放 |
| `/recamera-control/v1/audio/status` | GET | 可选语音闭环：返回 `idle/playing/done/error/stopped` |
| `/recamera-control/v1/audio/stop` | POST | 可选语音闭环：停止当前 `aplay` |

**状态验证（双轴电机就绪后才返回 200，否则 503）：**

访问 reCamera 局域网地址时必须绕过桌面代理，避免 Clash/系统代理截获请求后等待超时。不要依赖 `no_proxy=192.168.*` 这类通配符；`curl` 和 Python/urllib 对通配符支持不一致。现场验证优先使用 `--noproxy "*"`，启动 Python 进程前则设置精确 IP 的 `no_proxy/NO_PROXY`。`hardware/recamera_client.py` 已使用 `ProxyHandler({})` 对 gimbal bridge 请求禁用代理，但 shell 命令、浏览器和其他工具仍需显式绕过。

```bash
curl -q --noproxy "*" -sS -i --max-time 3 \
  "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/status"
```

期望响应包含 `connected=true`、真实 `yaw/pitch`、双轴 speed 和 `source=motor_readback`。

**可选冒烟测试（确认电机响应后立即 stop）：**

```bash
curl -q --noproxy "*" -sS -i --max-time 3 \
  -X POST "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/command" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"absolute","yaw":180,"pitch":90,"yaw_speed":180,"pitch_speed":180}'

curl -q --noproxy "*" -sS -i --max-time 3 \
  -X POST "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/stop" \
  -H 'Content-Type: application/json' -d '{"stop":true}'
```

Bridge 不可达时真实控制 fail closed，不会静默降级为 dry-run。

**与官方 demo flow 的边界：**

- 官方 dashboard flow 主要面向人在浏览器里操作：摇杆、角度滑块、速度滑块、自动跟踪、Sleep、Standby、Calibrate、Emergency Stop。
- 本项目 bridge 面向程序控制：只暴露版本化 HTTP endpoint，不承载产品 UI，也不直接决定“追谁、何时转向、是否安全”。
- 官方节点负责底层硬件能力；本项目负责上层 session、lease、SafetyLayer、EventBus 和业务场景。
- `status` 返回 readback，不等价于“上一条命令一定完成”；判断硬件完成仍要结合 `verified`、`last_error`、`target`、`readback` 和 `/api/control/runtime`。

**可选扬声器验证（语音闭环）：**

Seeed 官方硬件说明确认 reCamera Gimbal 2002 系列有 Mic/Speaker，WAV 播放命令为 `sudo aplay -D hw:1,0 /home/recamera/test.wav`，默认 16 bit / 16 kHz。Node-RED audio bridge 部署后用同样的 `--noproxy` 规则验证：

```bash
curl -q --noproxy "*" -sS -i --max-time 3 \
  "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/audio/status"
```

FastAPI 语音播放的闭环标准是：TTS 生成成功 → 音频传到 reCamera → 设备端 `aplay` 启动成功 → `/audio/status` 返回 `playing/done/error` → `/api/voice/state.playback` 可见最后一次播放状态。`bridge_accepted=true` 只表示 Node-RED audio bridge 已接收音频；必须再看 `device_playback_state`、`playback_confirmed`、`last_error` 和 bridge 返回的 `exit_code` 才能确认设备端是否真的播放完成。

---

### 1.4 环境变量速查

**必填变量（按需设置）：**

| 变量 | 示例值 | 必填时机 |
|---|---|---|
| `RECAMERA_DEVICE_IP` | `192.168.x.x` | **始终必填**，两个终端都要设置 |
| `RECAMERA_DOA_SOURCE` | `usb` | ReSpeaker USB 直连时（生产环境） |
| `RECAMERA_AUDIO_DEVICE` | `2` | 会议录音时；值来自 1.2 第 5 步 |
| `DEEPSEEK_API_KEY` | `sk-xxx` | 启用首选 LLM provider（对话、日记自动回复；会议整理接口仍属实验链路） |
| `ZHIPU_API_KEY` | `sk-xxx` | 启用智谱 GLM-4-Flash 兜底和 GLM-ASR 云端转写 |
| `ENABLE_WAKE_WORD` | `true` | 可选启用 wake word；默认关闭，缺少 openWakeWord 时不影响 FastAPI |
| `ENABLE_TTS_VOICE` | `false` | 可选关闭浏览器 TTS voice event；默认开启，不需要云端 TTS key |
| `VOICE_PLAYBACK_TARGET` | `recamera_speaker` | 语音交互播放目标；可设 `browser` 回退验收 |

**可选覆盖（有合理默认值，通常无需设置）：**

`DEEPSEEK_API_URL` / `DEEPSEEK_MODEL` / `DEEPSEEK_MAX_TOKENS` / `ASR_PROVIDER` / `ENABLE_WAKE_WORD` / `ENABLE_TTS_VOICE` / `ZHIPU_TTS_URL` / `ZHIPU_TTS_MODEL` / `ZHIPU_TTS_VOICE` / `ZHIPU_TTS_SPEED` / `ZHIPU_TTS_VOLUME` / `ZHIPU_TTS_FORMAT` / `VOICE_PLAYBACK_TARGET` / `RECAMERA_WHISPER_MODEL` / `RECAMERA_DOA_HOST` / `RECAMERA_DOA_PORT` / `RECAMERA_DOA_SPEECH_HOLD`

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
export no_proxy="127.0.0.1,localhost,$RECAMERA_DEVICE_IP"
export NO_PROXY="$no_proxy"
export RECAMERA_DOA_SOURCE=usb
export RECAMERA_AUDIO_DEVICE=<AUDIO_DEVICE_INDEX>
export DEEPSEEK_API_KEY=sk-xxx          # 可选；LLM 首选
export ZHIPU_API_KEY=sk-xxx             # 可选；LLM 兜底 + 云端 ASR
export ASR_PROVIDER=zhipu               # 可选；zhipu(默认) 或 local
export ENABLE_WAKE_WORD=false           # 可选；默认 false，true 时尝试加载 openWakeWord
export ENABLE_TTS_VOICE=true            # 可选；默认 true，浏览器端 Web Speech 朗读

python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
```

**终端 2 — 控制运行时（云台 FSM、Orchestrator、SafetyLayer）：**

```bash
cd ~/recamera_multimodal
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
export no_proxy="127.0.0.1,localhost,$RECAMERA_DEVICE_IP"
export NO_PROXY="$no_proxy"

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

HTTP 200 或 EventBus `accepted=true` 只表示事件已被控制运行时接收，不等同于硬件已完成动作。命令仍可能被 SafetyLayer 拦截或因硬件连接失败而未执行。判断真实云台动作应同时查看 `/api/control/runtime` 中的 `hardware_ready`、`last_apply_ok`、`last_hardware_command_error`、`gimbal_bridge_status`、`hardware_io.command_state` 和设备 readback。

### 3.4 官方 demo 能力复用边界

本项目复用的是官方 reCamera Gimbal 的设备能力，不复用官方 dashboard 作为最终控制面：

| 官方能力 | 官方 demo 中的表现 | 本项目如何使用 |
|---|---|---|
| 设备 IP dashboard | `http://<RECAMERA_IP>/#/dashboard` 预览画面、手动控制、自动跟踪 | 只作为硬件验证和故障定位入口 |
| Node-RED workspace | `http://<RECAMERA_IP>/#/workspace` 或 `:1880` 查看默认 dashboard flow | 导入本项目自写 bridge flow，保留官方节点能力 |
| SSCMA 视频/检测 | 设备侧模型输出画面和检测框 | FastAPI 连接 `ws://<RECAMERA_IP>:8090/`，生成 `/video_feed`、`/ws` 和感知事件 |
| 官方 reCamera 节点 | 电机角度、速度、目标跟踪、快捷按钮等节点/命令 | bridge 调用官方节点/命令完成 yaw/pitch、readback、stop、`gimbal cali` |
| 快捷按钮语义 | Sleep、Standby、Calibrate、Emergency Stop | 本项目通过 UI Event -> `main_phase3.py` -> bridge 统一执行；Sleep 使用 `yaw=180, pitch=175`，比官方向下位置略保守 |

因此，答辩或交接时可以这样说明：官方 demo 证明设备、模型和官方节点可用；本项目在它之上增加了唯一控制平面、租约、安全门、ReSpeaker DOA、语音闭环和产品页面。bridge 可以独立部署和用 curl 验证，但它不是完全脱离 reCamera 的独立硬件驱动，因为当前实现依赖设备侧 Node-RED 和官方 reCamera 节点。

---

## 4. 安装依赖与模型

### 4.1 Python 依赖

```bash
cd ~/recamera_multimodal
python3 -m pip install -r requirements.txt --break-system-packages

# FaceTrackerV2 推荐依赖
python3 -m pip install insightface --break-system-packages

# 本地会议转写 fallback；使用 ZHIPU_API_KEY + ASR_PROVIDER=zhipu 时可不安装
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

# 本地 ASR tiny 模型预热；模型会进入 Hugging Face 本地缓存
python3 - <<'PY'
from faster_whisper import WhisperModel
WhisperModel("Systran/faster-whisper-tiny", device="cpu", compute_type="int8")
print("faster-whisper tiny ready")
PY
```

ASR 默认优先使用智谱 GLM-ASR（需要 `ZHIPU_API_KEY`）；云端不可用、未配置 key 或 `ASR_PROVIDER=local` 时回退本地 `faster-whisper`。可通过 `RECAMERA_WHISPER_MODEL` 覆盖本地 ASR 模型；默认值为 `Systran/faster-whisper-tiny`。

### 4.3 统一控制面板验收

打开 `http://localhost:8001/control`，逐个页面验证：

| 页面 | 启动 | 终止 | 关键验收 |
|---|---|---|---|
| 人脸追踪与分析 | 启动功能 | 终止功能 | 情绪、专注、EAR/PERCLOS 更新；云台命令来自 main runtime |
| 声源 yaw 跟随 | 启动功能 | 终止功能 | DOA/VAD 更新，yaw-only 控制，pitch 不自动跟随 |
| 多人声源演示 | 启动声源演示 | 终止声源演示 | `/control` 默认 `save_audio:false`，只验证 DOA yaw 跟随、人脸关联和状态遥测；不提交 ASR/LLM 纪要 |
| 手势识别 | 启动功能 | 终止功能 | `gesture.available=true`，只展示类别、置信度与稳定帧，不叠加交互语义 |
| 健康与 PWA | 启动功能 | 终止功能 | 护眼/久坐/喝水/疲劳/低专注/情绪关心状态可观察 |
| LLM 与日记 | 启动功能 | 终止功能 | DeepSeek 优先，智谱兜底；云端不可用时端点本地 fallback |
| 手动云台 | 启动功能 | 终止功能 | D-Pad 只在当前 manual session 有效 |

每个页面的 `Standby`、`Sleep`、`Stop`、`Calibrate`：

- `Standby`：`yaw=180, pitch=90, speed=360`
- `Sleep`：`yaw=180, pitch=175, speed=360`
- `Calibrate`：通过 Node-RED bridge 执行 `gimbal cali`，并撤销当前设备租约

启动类 API 成功后返回 `session_id`。前端必须保存该 session，并在 stop、heartbeat、页面切换和 `beforeunload` 中继续携带；缺少 `session_id` 的 stop 请求应返回 `ok=false` 和 `reason=session_id_required`。

后端 `lease_ms=5000`（5 秒）；前端每 **1000ms** 发送一次心跳（`POST /api/control/heartbeat {session_id}`）以保持租约有效，设备桥另有 2 秒 watchdog。

### 4.4 完整环境变量参考

| 变量 | 默认值 | 用途 |
|---|---|---|
| `RECAMERA_DEVICE_IP` | 空 | reCamera 地址，推荐配置 |
| `RECAMERA_BASE_URL` | 空 | 兼容性的 HTTP base URL fallback |
| `DEEPSEEK_API_KEY` | 空 | 首选 LLM provider：对话、日记；会议整理接口仍属实验链路 |
| `DEEPSEEK_API_URL` | DeepSeek API | OpenAI-compatible API 地址 |
| `DEEPSEEK_MODEL` | 项目默认模型 | 模型名称 |
| `DEEPSEEK_MAX_TOKENS` | `600` | 单次输出上限 |
| `ZHIPU_API_KEY` | 空 | 智谱 GLM-4-Flash LLM 兜底；GLM-ASR 云端转写 |
| `ZHIPU_TTS_URL` | `https://open.bigmodel.cn/api/paas/v4/audio/speech` | 智谱/OpenAI-compatible TTS endpoint；不可用时回退浏览器文字/音频 |
| `ZHIPU_TTS_MODEL` | `glm-tts` | TTS 模型名 |
| `ZHIPU_TTS_VOICE` | 空 | 最终声线由实测后确定；payload 可临时覆盖 |
| `ZHIPU_TTS_SPEED` | 空 | TTS 语速，可由 payload/preset 覆盖 |
| `ZHIPU_TTS_VOLUME` | 空 | TTS 音量，可由 payload/preset 覆盖 |
| `ZHIPU_TTS_FORMAT` | `wav` | 第一版使用 WAV，匹配 reCamera `aplay` |
| `VOICE_PLAYBACK_TARGET` | `recamera_speaker` | `recamera_speaker` 优先；失败回退浏览器；也可直接设 `browser` |
| `RECAMERA_AUDIO_BRIDGE_URL` | 空 | 可选，覆盖语音播放 Node-RED bridge 地址；未设置时使用 `RECAMERA_DEVICE_IP` / `RECAMERA_BASE_URL` 的 1880 端口 |
| `RECAMERA_APLAY_DEVICE` / `VOICE_APLAY_DEVICE` | `auto` | reCamera 设备端 `aplay -D` 设备名；`auto` 时由 Node-RED bridge 运行 `aplay -l` 自动选择 |
| `RECAMERA_AUDIO_BRIDGE_RETRIES` | `5` | FastAPI 到 audio bridge 的最大重试次数，指数退避 |
| `ASR_PROVIDER` | `zhipu` | `zhipu` 优先云端 ASR；`local` 强制本地 whisper |
| `ENABLE_WAKE_WORD` | `false` | `true` 时启动可选 openWakeWord 服务；缺依赖或模型时 state 显示 unavailable |
| `ENABLE_TTS_VOICE` | `true` | 后端是否广播 voice event；前端仍可本地静音 |
| `RECAMERA_DOA_SOURCE` | `usb` | 生产环境使用 `usb`；`tcp` 为无 USB attach 时的备用 |
| `RECAMERA_DOA_HOST` | `0.0.0.0` | TCP DOA 监听地址 |
| `RECAMERA_DOA_PORT` | `9999` | TCP DOA 监听端口 |
| `RECAMERA_DOA_SPEECH_HOLD` | `0.8` | speech hold 秒数 |
| `RECAMERA_AUDIO_DEVICE` | 系统默认 | 会议录音设备索引（来自 1.2 第 5 步） |
| `RECAMERA_WHISPER_MODEL` | `Systran/faster-whisper-tiny` | 本地 whisper fallback 模型 |

LLM 路由顺序为 DeepSeek → 智谱 GLM-4-Flash → 端点本地 fallback。云端 LLM 未配置或调用失败时，相关接口回退到本地轻量逻辑，不影响视频和基础感知。ASR 路由顺序为智谱 GLM-ASR → 本地 `faster-whisper`；全部失败时 `/api/meeting/summarize` 返回 `asr_empty`。Wake word 默认关闭；即使 `ENABLE_WAKE_WORD=true` 且 openWakeWord 缺失，FastAPI 仍应启动，`/api/wake_word/state` 返回 unavailable。`/api/voice/chat` 第一版走智谱 ASR → LLM → TTS；TTS 或 reCamera 扬声器不可用时回退浏览器播放/文字反馈。

---

## 5. Dashboard 操作

### 5.1 页面入口

| 路由 | 页面 | 数据 |
|---|---|---|
| `/control`、`/v2` | Control Dashboard | FastAPI 真实视频、感知、录音状态和 UI Event 请求 |
| `/home` | 五页产品页 | `/ws` 实时状态，失败时降级 `/api/state` polling；聊天、日记、周报、会议和语音接后端接口 |
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
- 启动成功后，前端保存返回的 `session_id`；stop、heartbeat（1000ms）、页面切换和 `beforeunload` 都必须携带该 session。
- `/home` 优先使用 `/ws` 接收真实状态；失败后降级到 `/api/state` polling。
- `/home` 的会议运行态以后端 `control.active_feature`、`session_id` 和 `conversation` 为准；localStorage 只保存用户偏好和当前会议 session。
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

FastAPI 随后启用单人分析，包括摄像头、检测结果、情绪、专注、EAR（`eye_metrics.ear_avg`）、PERCLOS（`eye_metrics.perclos`）和眨眼率（`eye_metrics.blink_rate`）。成功后前端以 **1000ms** 间隔调用 `POST /api/control/heartbeat {session_id}` 保持 5s 租约有效。

### 5.5 声源 yaw 跟随

点击"启动功能"后，Dashboard 调用：

```text
POST /api/single_track/stop
POST /api/multi_track/start {"save_audio":false}
POST /api/tracking_mode {"mode":"multi"}
```

页面展示 ReSpeaker DOA、实体 WS2812 灯环状态和 reCamera yaw readback。DOA Event 由 Orchestrator 转换为 yaw-only command；pitch 始终为空。

### 5.6 会议录音

正式会议闭环在 `/home` 会议页。点击"开始会议记录"后，Dashboard 调用：

```text
POST /api/conversation/start {"control_session":true, "save_audio":true}
```

返回 `session_id` 保存到前端；录音设备由 `RECAMERA_AUDIO_DEVICE` 决定。
启动会议录音会重置本轮说话人映射，并暂停 wake word；停止会议录音后恢复 wake word。说话人标注只读取 DOA、`face_lock`、`pose.persons` 和 `gimbal.pitch` 等状态，不驱动云台。无法匹配或 provider 异常时，turn 会保存为 `未知说话人`，不阻塞 WAV 保存、ASR 或摘要。

结束会议时 `/home` 调用 `POST /api/meeting/complete {session_id}`。该接口是异步 job 提交接口：HTTP 返回 `submitted=true, processing=true` 时只表示后台已开始停止录音和整理，不表示纪要已经生成。前端继续通过 `/api/conversation/state` 或 `/api/state.conversation.report.status` 等待 `ready/error`；`ready` 后才把报告写入会议历史并清空本地会议 session。

`/control` 的多人页是声源定位调试面板，启动 `/api/multi_track/start {"save_audio":false}`，终止 `/api/multi_track/stop {"finalize":false}`。它不会录音，也不会调用 `/api/meeting/complete` 生成纪要；完整录音、转写和会议历史以 `/home` 为准。

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

### 6.3 DOA 方向校准

当前公式以 DOA `0°` 为摄像头正前方、`90°` 为摄像头右侧：

```text
yaw = 180 + signed(DOA + offset) * doa_direction
```

校准顺序：

1. 正对摄像头说话，点击 `/home` 会议区"对准我"，或调用 `POST /api/control/doa_calibrate`，写入 `doa_offset_deg`。
2. 站在摄像头右侧说话，确认 yaw readback 是否向右侧目标转动。
3. 如果实机表现反向，写入 `doa_direction=-1`：

```bash
curl -X POST http://localhost:8001/api/control/doa_direction \
  -H 'Content-Type: application/json' \
  -d '{"doa_direction":-1}'
```

4. 再次右侧说话确认 yaw 方向。不要硬改全局公式；镜像安装只改 `doa_direction`。

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

### 7.4 LLM 与开放词汇情绪推理

```bash
curl -X POST http://localhost:8001/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"我有点累","context":"","user_name":"测试"}'

curl -X POST http://localhost:8001/api/reflect \
  -H 'Content-Type: application/json' \
  -d '{"mode":"diary","emotion":"Happiness","attention":75}'

curl -X POST http://localhost:8001/api/emotion/infer
```

`/api/emotion/infer` 是低频语义接口，不进入 `/ws` 的 200ms 实时状态流；建议手动触发或 30 秒以上间隔调用。它保留 EmotiEffLib 8 类实时分类作为底层信号，额外输出开放中文情绪标签、`1-10` 强度和一句解释。无人脸时返回 `label="暂未观察到"`、`intensity=0`、`provider="local"`；云端 LLM 不可用或 JSON 解析失败时使用本地 EmotiEff 映射 fallback。

### 7.5 会议录音和 ASR 实验链路

```bash
curl -X POST http://localhost:8001/api/conversation/start \
  -H 'Content-Type: application/json' -d '{"save_audio":true}'

curl http://localhost:8001/api/conversation/state
curl http://localhost:8001/api/conversation/debug
curl http://localhost:8001/api/meeting/speakers

curl -X POST http://localhost:8001/api/conversation/stop \
  -H 'Content-Type: application/json' -d '{"finalize":true}'

curl -X POST http://localhost:8001/api/meeting/summarize \
  -H 'Content-Type: application/json' -d '{}'
```

`/api/meeting/complete` 是 `/home` 使用的会议收口接口：先停止当前 control session 和录音，再在后台调用 summarize 实验链路。调用方应把首次响应视为“任务已提交”，再轮询 `/api/conversation/state` 的 `report.status`。`ready` 只表示当前实验链路返回了文本结果；由于 ReSpeaker 实时录音音质未稳定验收，会议纪要不能作为已完成功能交付。

`/api/meeting/summarize` 失败时返回结构化错误码：

| 错误码 | 场景 | 操作提示 |
|---|---|---|
| `recording_not_started` | 未启动会议录音 | 先启动会议录音 |
| `no_segments` | 已启动但没有有效语音片段 | 先录到语音片段 |
| `asr_empty` | 智谱 ASR 和本地 ASR 均未返回文本 | 检查语音时长、`ZHIPU_API_KEY`、`ASR_PROVIDER`、`faster-whisper` 和模型缓存 |

成功返回时 transcript 会带 `[说话人A]` 或 `[未知说话人]` 前缀；说话人识别失败不改变 `/api/meeting/summarize` 的请求/响应字段。当前已验证腾讯会议等外部录音可进入转写链路，但 ReSpeaker 真实会议录音质量不稳定，会议纪要未实现为可交付能力。

### 7.6 Wake Word 与说话人状态

```bash
curl http://localhost:8001/api/wake_word/state
curl http://localhost:8001/api/meeting/speakers
```

`/api/wake_word/state` 返回 `{enabled, available, listening, paused, error}`。默认 `enabled=false`；`ENABLE_WAKE_WORD=true` 但缺少 openWakeWord 或音频输入不可用时返回 unavailable/error，不阻塞 FastAPI。检测到唤醒词后，WebSocket 广播 `{"type":"wake_word_detected","name":str,"score":float,"time":float}`。

`/api/meeting/speakers` 返回当前会议 session 内通过 DOA zone 注册的说话人列表。当前实现只做非阻塞辅助标注，不执行完整搜索、唇动验证或重识别。

### 7.7 语音交互与 reCamera 扬声器闭环

```bash
curl http://localhost:8001/api/voice/state

curl -X POST http://localhost:8001/api/voice/say \
  -H 'Content-Type: application/json' \
  -d '{"text":"小屿语音测试。","reason":"manual","source":"curl","interrupt":true}'

curl -X POST http://localhost:8001/api/voice/stop \
  -H 'Content-Type: application/json' -d '{"reason":"curl"}'
```

`/api/voice/say` 仍是轻量 voice event，主要用于会议开始/停止、摘要完成/失败和 wake word detected 等短提示。完整语音交互使用以下新接口：

```bash
# 上传一段短音频：raw body，Content-Type 可为 audio/webm 或 audio/wav
curl -X POST "http://localhost:8001/api/voice/chat?user_name=lintong" \
  -H 'Content-Type: audio/wav' \
  --data-binary @/path/to/short.wav

# 仅合成 TTS，可选择 play=true 立即播放
curl -X POST http://localhost:8001/api/voice/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"小屿语音测试。","preset":"neutral_natural","play":true}'

# 不依赖智谱 key 的本地测试音，用来单独验 reCamera 扬声器
curl -X POST http://localhost:8001/api/voice/test_tone \
  -H 'Content-Type: application/json' \
  -d '{"target":"recamera_speaker","play":true}'

# 主动刷新 Node-RED audio bridge 状态
curl http://localhost:8001/api/voice/playback/status

# 播放已缓存音频到 reCamera 扬声器或浏览器
curl -X POST http://localhost:8001/api/voice/play \
  -H 'Content-Type: application/json' \
  -d '{"audio_id":"reply_xxx","target":"recamera_speaker"}'
```

`/api/voice/chat` 返回 `{transcript, reply, audio_url, playback_target, providers}`。默认 `VOICE_PLAYBACK_TARGET=recamera_speaker`：FastAPI 生成 WAV 后通过 Node-RED `/recamera-control/v1/audio/play` 传到设备，由 reCamera 执行 `aplay -D <device>`。设备名优先使用 `RECAMERA_APLAY_DEVICE` / `VOICE_APLAY_DEVICE`，默认 `auto` 时从设备端 `aplay -l` 选择第一块声卡。失败时 `/api/voice/state.playback` 会记录原因，前端回退浏览器音频/文字反馈。

语音播放有两个状态层级：`bridge_accepted` 表示 FastAPI 已把音频交给 Node-RED；`device_playback_state` 和 `playback_confirmed` 来自 `/recamera-control/v1/audio/status`，用于确认 `aplay` 是否 `playing/done/error`。排障时优先看 `last_error`、`aplay_device`、`exit_code` 和 `bridge_attempts`。

`/control` 的 **Voice / Speaker Loop** 卡片可以完成集中验收：先点"刷新 bridge"，再点"reCamera 测试音"确认设备出声；配置智谱 key 后点"智谱 TTS 播放"验证声线；最后用"录音上传"验证 ASR→Chat→TTS→播放闭环。

声线不写死，先用三组 preset 做实测：

| preset | 用途 |
|---|---|
| `gentle_female` | 温柔女声；陪伴、日记回应、情绪安抚 |
| `neutral_natural` | 中性自然声；默认对话 |
| `meeting_prompt` | 会议提示声；短促克制的状态提示 |

最终声线由实测后确定，落到 `ZHIPU_TTS_VOICE`、`ZHIPU_TTS_SPEED`、`ZHIPU_TTS_VOLUME` 或请求 payload 覆盖。

### 7.8 EventBus 端口

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

7. 进入陪伴 Tab → Network 每 1000ms 出现 `POST /api/control/heartbeat {session_id}`。
8. 离开陪伴 Tab → 心跳停止；`beforeunload` 时出现 `sendBeacon` 到 `/api/single_track/stop`。
9. 启动会议 → `POST /api/conversation/start {control_session:true, save_audio:true}`；结束整理 → `POST /api/meeting/complete {session_id}`，首次响应是后台提交；继续轮询 `/api/conversation/state`，直到 `report.status=ready/error`。`ready` 后会议进入本地历史，`error` 展示 `report.error`。

日记与 LLM：

10. 保存日记 → emotion 字段使用用户选择而非无条件 `Neutral`；10s 内出现小屿 LLM 回复气泡，`conversation[0]` 有内容。
11. 日记详情页发送追加消息 → 出现用户气泡 + 小屿回复气泡 → 关闭再打开，对话内容仍在。
12. 修改昵称 → 发起聊天 → payload 包含 `user_name`；`emotion` 字段为中文（如"快乐"）。
13. DevTools 模拟限速超过 10s → 应出现降级提示，不出现未捕获异常；无云端 key 时 `/api/chat` 返回 `source=template`。
14. 调用 `POST /api/emotion/infer`：无人脸时返回 `provider=local,label=暂未观察到,intensity=0`；有人脸且云端可用时返回开放词汇标签、强度和解释；云端不可用时仍返回本地 fallback。
15. `/home` 点击"语音"或做 Open Palm：录入短音频 → `/api/voice/chat` 返回 transcript/reply；有 TTS 时 `audio_url` 可播放。
16. Closed Fist 单次稳定握拳即调用 `/api/voice/stop` 并收起当前提醒；不再要求二次握拳确认。
17. `/control` 的 Voice / Speaker Loop 可验证：浏览器事件、智谱 TTS、reCamera 测试音、audio bridge status、录音上传 `/api/voice/chat`、停止播放；`/api/voice/state.playback` 可见 `bridge_accepted`、`device_playback_state`、`playback_confirmed` 和设备扬声器播放状态。

周报：

18. 点击"本周周报" → 请求 `/api/report/weekly`；失败时显示本地周报 fallback。
18. 切换到其他标签再回来 → 周报文本从 localStorage 重新渲染（非空白或旧文案）。

存储：

19. 三种情况调用 `/api/meeting/summarize`：确认分别得到 `recording_not_started`、`no_segments`、`asr_empty`；有 `ZHIPU_API_KEY` 时优先云端转写，无 key 或 `ASR_PROVIDER=local` 时走本地 whisper fallback。
20. 成功返回的 transcript 带 `[说话人A]` 或 `[未知说话人]`；说话人识别失败不影响接口字段，但会议纪要仍未作为可交付功能验收。
21. 会议录音启动后，`audio_processing.vad_mode` 应为 `webrtcvad` 或 `rms`，`GET /api/meeting/speakers` 返回 `{ok,speakers,total}`。
22. 默认无 `ENABLE_WAKE_WORD` 时，`GET /api/wake_word/state` 返回 `enabled=false`；启用但缺 openWakeWord 时服务仍能启动。
23. `navigator.storage.estimate()` 报告 >85% → 出现存储配额警告 toast。

### 8.6 真实硬件动作

只在周围无障碍物时测试：

1. 启动完整系统。
2. 在手动云台页点击"启动功能"。
3. 发送一次小幅 yaw delta。
4. 观察 EventBus 响应、控制运行时日志和设备实际动作。
5. 调用 home，再停止控制运行时。

不要仅根据 Dashboard 状态标签判定硬件动作成功。

### 8.7 当前主要未稳定问题复盘（2026-07-05 至 2026-07-10）

本节只记录最近五天在 `recamera_multimodal` 中能由日志、代码和文档直接支撑的事实。未找到连续真实硬件视频延迟日志，因此视频侧数值分为“代码阈值/设计频率”和“测试桩样例”，不能等同于现场实测延迟；ReSpeaker 侧有 2026-07-08 的三次录音自检日志，可作为当前最明确的问题证据。

#### 8.7.1 视频延迟与感知链路

当前现象：用户侧反馈视频存在明显延迟；仓库中未发现最近五天保存的真实连续 `frame_age_ms` / 端到端视频延迟采样日志。现有可确认数值如下：

| 指标 | 当前数值 | 来源与含义 |
|---|---:|---|
| 视频帧 stale 判定 | `last_frame_age_ms > 1000 ms` | FastAPI 超过 1 秒未拿到新帧即视为 stale，后续候选会被清空或阻断观察 |
| MJPEG `/video_feed` 发送节流 | `0.5 s` | 每次 yield 后 sleep 0.5 秒，理论展示上限约 `2 FPS`；这会直接影响浏览器 MJPEG 页面观感 |
| `/ws` 状态推送目标 | `200 ms` | WebSocket 状态约 `5 Hz`；观察字段中 `telemetry_hz/ui_push_hz` 目标为 `4.0 Hz` |
| 感知循环 sleep：单人跟踪 | `125 ms` | 单人跟踪 profile 约 `8 Hz` 主循环 |
| 感知循环 sleep：多人/陪伴 | `250 ms` | 多人和健康陪伴 profile 约 `4 Hz` 主循环 |
| 感知循环 sleep：idle | `750 ms` | 空闲模式约 `1.3 Hz` |
| 人脸阶段降级阈值 | `>350 ms` 降级，`<200 ms` 恢复 | 人脸检测耗时过高时提高检测间隔，保护主循环 |
| 检测输入最大宽度 | `960 px` | 解码后下采样上限，影响检测耗时与精度 |
| 控制循环默认 | `10 Hz` | `main_phase3.py --fps` 默认 10；性能预算目标为控制 `15-20 Hz`、人脸 `8-15 Hz`、telemetry `3-5 Hz` |
| 回归测试正常样例 | `fps=12.5`，`last_frame_age_ms=42 ms` | 测试桩契约，不代表真实设备现场 |
| 回归测试 stale 样例 | `fps=7.4`，`last_frame_age_ms=104000 ms` | 用于验证超时帧会清空候选和阻断观察 |

可能原因按优先级排查：

1. **浏览器 MJPEG 展示被主动限速**：`/video_feed` yield 后固定 sleep 0.5 秒，单看视频页面会接近 2 FPS，即使后端拿帧更快也会显得卡顿。
2. **感知模型链路占用主循环**：SCRFD/ArcFace、YOLO pose、MediaPipe Face Landmarker、EmotiEff 和 Gesture Recognizer 按 profile 分频执行；如果 `stage_ms.face` 长期超过 350 ms，会进入人脸降级。
3. **设备侧 SSCMA 或网络输出不稳定**：FastAPI 依赖 `ws://<RECAMERA_IP>:8090/` 的 base64 JPEG + 检测框；如果 8090 输出低帧率或断续，`last_frame_age_ms` 会持续增长。
4. **状态推送和视频不是同一频率**：`/ws` 可约 4-5 Hz 刷状态，但 MJPEG 可能只有约 2 FPS；前端状态看似在动，视频仍会显得滞后。
5. **控制闭环和视频闭环并不同步**：云台控制默认 10 Hz，硬件状态查询存在 1.2 秒超时边界；视频延迟不一定说明控制 Event 延迟，需要分开记录。

下一次复测建议把以下字段同时保存 60 秒以上：`/api/debug/video` 的 `fps/last_frame_age_ms`、`/api/state.perception.stage_ms`、`face_period/pose_period/detail_period/face_degraded`、浏览器实际显示 FPS、设备 8090 WebSocket 收帧间隔、控制日志中的 `control_loop_ms`。只有这些数据齐全，才能把“视频显示慢”“模型推理慢”“设备吐帧慢”“控制慢”拆开定位。

#### 8.7.2 ReSpeaker 录音模糊与 ASR 为空/误识别

当前现象：ReSpeaker 实时录音内容模糊、不清晰，ASR 为空或明显误识别。2026-07-08 的 `logs/respeaker_test.log` 中三次 8 秒录音自检结果如下：

| 录音文件 | 期望录音时长 | 实际耗时 | `clock_ratio` | 估算有效采样率 | 音量/语音指标 | ASR 结果 |
|---|---:|---:|---:|---:|---|---|
| `respeaker_check_20260708_145431.wav` | `8.0 s` | `16.12 s` | `2.015` | `7940 Hz` | RMS `0.000823`，peak `0.014588`，VAD `0.0338` | ASR 未运行，报 `No module named 'audio'` |
| `respeaker_check_20260708_145501.wav` | `8.0 s` | `16.107 s` | `2.013` | `7947 Hz` | RMS `0.035209`，peak `0.707938`，VAD `0.3759` | 本地 ASR `4.438 s`，转写为空 |
| `respeaker_check_20260708_145552.wav` | `8.0 s` | `16.109 s` | `2.014` | `7946 Hz` | RMS `0.035707`，peak `0.707938`，VAD `0.7218` | 原始与修正 WAV 均误识别为“字幕by索兰娅” |

设备枚举显示 ReSpeaker 为 `reSpeaker XVF3800 4-Mic Array: USB Audio (hw:0,0)`，输入通道 `2`，默认采样率 `16000.0`。但三次测试都出现约 `2.01x` 的录音耗时膨胀，等效采样率只有约 `7.94-7.95 kHz`。脚本已把 `clock_ratio > 1.5` 标记为 `clock_slow_or_usb_audio_issue`，说明当前最可疑点不是单纯 ASR 模型，而是 USB Audio / WSL / 设备采样时钟链路异常。

可能原因按优先级排查：

1. **USB Audio 在 WSL 中时钟异常**：请求 16 kHz 录 8 秒，实际阻塞约 16.1 秒，等效只有约 7.95 kHz；这会让 WAV 头、真实语速和模型预期不一致，造成声音变慢、模糊或 ASR 误判。
2. **设备索引或通道选择仍需复核**：枚举里同时存在 `hw:0,0`、`sysdefault`、`spdif`、`default` 等输入；`RECAMERA_AUDIO_DEVICE` 必须指向 ReSpeaker 的真实输入设备，且建议固定记录 device index、channels 和 sample rate。
3. **录音音量/语音占比不稳定**：第一段 RMS 极低且 VAD 只有 `0.0338`，第二、三段音量较高但 ASR 仍空或误识别，说明同时存在采集链路和声学质量问题。
4. **ASR 链路本身不是唯一故障点**：项目交接记录显示腾讯会议等外部录音可以进入转写链路，因此 ASR 工程链路并非完全不可用；ReSpeaker 实时采集质量才是当前主要未验收项。
5. **本地依赖/运行路径也需修正**：第一段出现 `No module named 'audio'`，说明直接运行自检脚本时还需保证在仓库根目录或设置正确 `PYTHONPATH`，否则会把采集问题和脚本环境问题混在一起。

下一次复测建议分别做三组对照：原生 Linux 或 reCamera 侧录音、WSL USBIP 录音、同一环境下普通 USB 麦克风录音；每组记录 `clock_ratio`、有效采样率、RMS、peak、VAD、ASR 耗时和 transcript。若原生 Linux 正常而 WSL 异常，优先定位 USBIP/WSL 音频；若所有环境都约 8 kHz，优先定位 ReSpeaker 固件/驱动/采样率协商；若外置麦克风正常而 ReSpeaker 异常，优先替换 ReSpeaker 配置或硬件。

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

如果视频未断开但明显延迟，先按 8.7.1 采集 `last_frame_age_ms`、`fps`、`stage_ms` 和浏览器显示 FPS；不要只凭页面观感判断是网络、模型还是前端展示问题。

### 9.3 Dashboard 控制请求 unreachable

```bash
ss -lntp | grep 8765
```

确认 `main_phase3.py` 带 `--manual-control`，且 FastAPI 与控制运行时使用同一主机的 `127.0.0.1:8765`。若修改端口，当前 FastAPI EventBusClient 默认仍使用 8765，需同步修改。

### 9.4 局域网请求被代理截获

如果 `curl -v` 输出里出现以下信号，说明请求去了本机代理而不是 reCamera：

```text
Uses proxy env variable http_proxy == 'http://127.0.0.1:7897'
Trying 127.0.0.1:7897...
```

处理方式：

```bash
export no_proxy="127.0.0.1,localhost,$RECAMERA_DEVICE_IP"
export NO_PROXY="$no_proxy"

curl -q --noproxy "*" -sS -i --max-time 3 \
  "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/status"
```

不要只写 `no_proxy=192.168.*`；不同工具对 `*` 通配符支持不一致。`hardware/recamera_client.py` 的 gimbal bridge 请求已在代码层禁用代理，但启动环境仍建议设置精确 IP，方便 curl、Node-RED 验证和其他工具保持一致。

### 9.5 控制事件 accepted 但云台不动

依次检查：

1. `main_phase3.py` 是否带 `--enable-control`。
2. 设备地址和 1880 端口是否可达（`nc -zv "$RECAMERA_DEVICE_IP" 1880`）。
3. Node-RED bridge 是否已部署并返回 `connected=true`（优先使用 1.3 节带 `--noproxy` 的验证命令）。
4. SafetyLayer 是否因 rate limit、范围或 safe mode 拦截。
5. 控制运行时日志是否出现命令应用失败。

`accepted=true` 但 `hardware_ready=false` 时，说明控制会话存在但硬件出口不可用；这是降级状态，不是前端已完成硬件动作。若 `hardware_io.command_state=accepted/executing` 长时间不变，再查 Node-RED bridge、CAN readback 和设备 lease。

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

如果 `/home` 点击结束后页面显示“整理已提交”但历史没有出现，先看 `/api/conversation/state` 的 `report.status`：`stopping/summarizing` 表示后台仍在跑，`ready` 才应入历史，`error` 则看 `report.error`、`asr_status` 和 `last_asr_error`。不要用 `/control` 的多人声源演示来验证会议录音；该页默认 `save_audio:false`。

如果能录音但声音模糊、语速异常、ASR 为空或误识别，按 8.7.2 记录 `clock_ratio`、有效采样率、RMS、peak、VAD 和 transcript。当前 2026-07-08 自检已出现 `clock_ratio≈2.01`、有效采样率约 `7.95 kHz` 的异常，应优先排查 USB Audio/WSL 时钟链路。

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
3. 确认 `name`、`confidence`、`handedness`、`stable_frames` 正常更新。
4. 确认 `intent=""`、`intent_ready=false`，没有语义动作或冷却状态。
5. 确认没有手势产生的云台或功能控制事件。

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

1. 页面 start/stop/heartbeat 均转换为 Event，`main_phase3.py` 以 session token 和 5 秒租约维护唯一控制权。
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
| `/home` 完整会议录音/转写/纪要闭环 | **PARTIAL，`/api/meeting/complete` 异步提交与外部录音 ASR 链路可用；ReSpeaker 实时录音质量未稳定验收，会议纪要暂不可按可交付闭环判定** |
| `/control` 多人页默认录音/纪要 | **NO，默认是 `save_audio:false` 的声源定位演示** |
| reCamera 扬声器播放确认 | **PARTIAL，bridge accepted 与设备端 `aplay` 状态分层暴露** |

### A.3 6.2 变更记录

- 新增 8.7，汇总 2026-07-05 至 2026-07-10 的两项主要未稳定问题：视频延迟与 ReSpeaker 录音模糊。
- 明确视频侧目前缺少连续真实硬件延迟日志，只能先记录 stale 阈值、推送频率、MJPEG 展示节流、感知 profile 和回归测试样例。
- 明确 ReSpeaker 2026-07-08 三次 8 秒自检均出现 `clock_ratio≈2.01`、等效采样率约 `7.95 kHz`，ASR 为空或误识别。
- 将 `/home` 会议录音/转写/纪要闭环状态从 YES 调整为 PARTIAL，避免交接时误判为可交付闭环。

### A.4 6.0 变更记录

- **结构重组**：新增第一章，将 reCamera 连接（原 3.1/3.3）、ReSpeaker USB 连接与音频索引查询（原 7.1/7.2）、Node-RED Bridge 部署与验证（原 3.5）、环境变量速查（原 4.4 精简）统一提至文档最前。
- 启动命令（原第 1 章 + 第 5 章）合并为第二章，去除重复解释，保留可直接复制的命令块。
- 原第 7 章 ReSpeaker 仅保留 TCP 备用和 DOA/LED 验证，重编号为第六章。
- 故障排查 DOA 小节改为"见 1.2 节"，避免重复。
- 原第 11 章变更记录移至附录 A。
- 所有章节重编号：原 2→3、3→5（部分）、4→4、6→5、8→7、9→8、10→9、12→10。
- SOP 版本：5.3 → 6.0。

### A.5 历史变更（5.x）

- **5.3**：修正 `eye_metrics` 字段（`ear`→`ear_avg`）、`active_feature`（非 `feature`）、1.5s lease（非 2.5s）；更新会议 API（`/api/conversation/*`）；修正 localStorage 键清单，删除 5 个未实现幽灵键；扩展 9.5 回归验收清单（A-E 功能）。
- **5.2**：对齐 FastAPI 和 `main_phase3.py` 当前 CLI 参数；新增 feature session、1.5 秒租约、旧会话隔离；接通 ReSpeaker USB DOA、会议录音和实体 WS2812 DOA 灯效；接通 DOA audio Event 到 yaw-only Orchestrator 命令；新增 Node-RED 双轴 control/status bridge 和真实 CAN motor readback。
- **5.1**：将设备地址配置和 quick start 移到文档开头；删除已不存在的旧硬件模式切换流程；新增 Dashboard 地址输入作用域说明。
