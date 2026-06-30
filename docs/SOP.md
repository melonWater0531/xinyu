# reCamera Multimodal SOP 5.1

> 架构、部署、操作、验收与排障手册
> 版本：5.1
> 更新日期：2026-06-28
> 本文档以当前仓库代码为准；架构原理见 `docs/ARCHITECTURE.md`。

---

## 1. 快速启动

### 1.1 完整系统需要两个终端

先取得 reCamera 当前无线地址，并用实际地址替换 `<RECAMERA_IP>`。环境变量只在设置它的终端及子进程中生效，因此两个终端都应显式设置。

**终端 1：FastAPI、视频、感知、录音和 Dashboard**

```bash
cd ~/recamera_multimodal
export RECAMERA_DEVICE_IP=<RECAMERA_IP>

# 可选：LLM 对话、日记和会议摘要
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 可选：USB 直连 ReSpeaker
export RECAMERA_DOA_SOURCE=usb
# export RECAMERA_AUDIO_DEVICE=<AUDIO_DEVICE_INDEX>

python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
```

**终端 2：唯一硬件控制运行时**

```bash
cd ~/recamera_multimodal
export RECAMERA_DEVICE_IP=<RECAMERA_IP>

python3 main_phase3.py \
  --enable-control \
  --gimbal-ip "$RECAMERA_DEVICE_IP" \
  --manual-control \
  --fps 10
```

打开控制台：

```text
http://localhost:8001/control
```

关键参数：

- `--enable-control`：允许 `main_phase3.py` 连接真实 SSCMA 和云台。
- `--manual-control`：在 `127.0.0.1:8765` 启动 EventBus，使 Dashboard 的云台 UI Event 能进入控制运行时。
- `--gimbal-ip` 后必须传入展开后的变量值；不要把环境变量名称本身当作设备地址。
- FastAPI 已移除旧的硬件模式切换参数，设备连接仅由地址配置决定。

### 1.2 无设备地址启动 FastAPI

FastAPI 可在没有设备地址时启动：

```bash
cd ~/recamera_multimodal
python3 recamera_fastapi.py
```

此时页面和非视频 API 可用，视频状态显示“未配置”。进入 `/control` 后，在顶部“设备地址”输入框填写 `<RECAMERA_IP>`，点击“保存并重连视频”。

Dashboard 输入只重连当前 FastAPI 进程的 SSCMA 视频/感知来源。它不会：

- 写入 shell 环境变量或持久化配置文件；
- 修改已经运行的 `main_phase3.py`；
- 创建硬件控制客户端；
- 绕过 EventBus、FSM、Orchestrator 或 SafetyLayer。

FastAPI 重启后若希望自动恢复地址，应使用 `RECAMERA_DEVICE_IP` 或 `--device-ip`。

---

## 2. 架构与控制边界

### 2.1 唯一控制链

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
  -> reCamera gimbal
```

视觉控制事件在 `main_phase3.py` 内由 SSCMA 输入转换为统一 Event，再进入同一个 FSM、Orchestrator、SafetyLayer 和硬件出口。

### 2.2 模块职责

| 模块 | 当前职责 | 禁止事项 |
|---|---|---|
| `recamera_fastapi.py` | 页面、视频、感知、录音、telemetry、UI Event emitter | 不调用 `RecameraClient`，不直接控制硬件 |
| `core/event_bus.py` | 传输统一 Event | 不做状态转移或控制决策 |
| `main_phase3.py` | 唯一 control runtime | 不允许第二硬件控制平面 |
| `core/fsm.py` | 纯状态机 | 不保存 yaw/pitch intent，不生成命令 |
| `core/orchestrator.py` | 唯一 `ControlCommand` 决策源 | 不访问 FastAPI 或感知模块内部状态 |
| `core/safety_layer.py` | 对最终命令 allow/block | 不改写命令，不生成替代命令 |
| `hardware/recamera_client.py` | 唯一硬件出口 | 不决定业务意图 |

### 2.3 统一 Event

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

Dashboard 手动移动示例：

```text
POST /api/gimbal/move
  -> Event(type=ui, name=dpad_move, payload={pan, tilt}, source=fastapi)
  -> EventBus
  -> main_phase3.py
  -> Orchestrator
  -> SafetyLayer
  -> hardware
```

HTTP 200 或 EventBus `accepted=true` 只表示事件已被控制运行时接收，不等同于硬件已完成动作。命令仍可能被 SafetyLayer 拦截或因硬件连接失败而未执行。

---

## 3. 设备地址配置

### 3.1 获取无线 IP

reCamera 的 USB 管理地址固定为 `192.168.42.1`，它只用于初始化和查询无线地址：

```bash
ssh recamera@192.168.42.1
ip addr show wlan0
```

记下 `wlan0` 的 IPv4 地址作为 `<RECAMERA_IP>`。无线地址可能被 DHCP 重新分配，网络变化后应重新查询。

### 3.2 地址输入方式和优先级

推荐使用纯主机地址：

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
```

代码同时接受主机名、`host:port` 和带 scheme 的 URL，并统一规范化。配置优先级为：

1. 命令行显式参数：FastAPI 的 `--device-ip` 或控制运行时的 `--gimbal-ip`。
2. `RECAMERA_DEVICE_IP`。
3. `RECAMERA_BASE_URL`，仅作为兼容 fallback。
4. Dashboard 输入，仅覆盖当前 FastAPI 进程的内存配置。

FastAPI 和控制运行时是两个独立进程。Dashboard 中更新地址后，控制运行时不会自动同步；需要用相同地址重新启动 `main_phase3.py`。

### 3.3 连接检查

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>

ping -c 3 "$RECAMERA_DEVICE_IP"
nc -zv "$RECAMERA_DEVICE_IP" 8090   # SSCMA 视频
nc -zv "$RECAMERA_DEVICE_IP" 1880   # Node-RED / 云台控制
nc -zv "$RECAMERA_DEVICE_IP" 80     # 设备 Web 页面
nc -zv "$RECAMERA_DEVICE_IP" 22     # SSH
```

### 3.4 Dashboard 重连验证

```bash
curl http://localhost:8001/api/device/config

curl -X POST http://localhost:8001/api/device/config \
  -H 'Content-Type: application/json' \
  -d '{"device_ip":"<RECAMERA_IP>"}'
```

成功响应应包含：

```json
{
  "ok": true,
  "device": {
    "ip": "<RECAMERA_IP>",
    "configured": true,
    "sscma_url": "ws://<RECAMERA_IP>:8090/",
    "video_connected": false
  }
}
```

`video_connected` 可能在重连初期暂时为 `false`，随后通过 `/api/device/config` 或 `/api/state` 再次确认。

### 3.5 部署云台 Node-RED Bridge

真实控制不再依赖 Dashboard widget ID。打开 `http://<RECAMERA_IP>:1880`，安装官方 `node-red-contrib-seeed-recamera` palette，导入并部署：

```text
deploy/node_red/recamera_control_bridge.json
```

启动 `main_phase3.py --enable-control` 前必须验证：

```bash
curl "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/status"
```

响应应包含 `connected=true`、真实 `yaw/pitch`、双轴 speed 和 `source=motor_readback`。Bridge 不可达时真实控制 fail closed，不会静默降级成 dry-run。

---

## 4. 安装与环境变量

### 4.1 Python 依赖

```bash
cd ~/recamera_multimodal
python3 -m pip install -r requirements.txt --break-system-packages

# FaceTrackerV2 推荐依赖
python3 -m pip install insightface --break-system-packages

# 会议转写可选依赖
python3 -m pip install faster-whisper --break-system-packages
```

### 4.2 环境变量

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
| `RECAMERA_AUDIO_DEVICE` | 系统默认 | 会议录音设备索引 |

LLM 未配置时，相关接口回退到本地轻量逻辑，不影响视频和基础感知。

查询录音设备：

```bash
python3 -c "import sounddevice; print(sounddevice.query_devices())"
export RECAMERA_AUDIO_DEVICE=<AUDIO_DEVICE_INDEX>
```

---

## 5. 运行方式

### 5.1 FastAPI：页面、视频、感知和录音

有设备地址：

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
```

无设备地址：

```bash
python3 recamera_fastapi.py
```

常用参数：

```text
--device-ip <host>     reCamera 地址；默认读取 RECAMERA_DEVICE_IP
--host 0.0.0.0         HTTP 监听地址
--port 8001            HTTP 监听端口
--log-level INFO       DEBUG / INFO / WARNING
--ssl-keyfile <path>   可选 HTTPS 私钥
--ssl-certfile <path>  可选 HTTPS 证书
```

FastAPI 不接受控制运行时专用参数，也不再提供旧的硬件模式切换参数。

### 5.2 Mock 控制运行时

不连接真实设备，用于检查事件循环、FSM 和命令生成：

```bash
python3 main_phase3.py --mock --max-cycles 30 --log-level DEBUG
```

未传 `--enable-control` 时，`main_phase3.py` 默认使用 mock vision 和 dry-run hardware client。

### 5.3 真实控制运行时

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>

python3 main_phase3.py \
  --enable-control \
  --gimbal-ip "$RECAMERA_DEVICE_IP" \
  --manual-control \
  --eventbus-host 127.0.0.1 \
  --eventbus-port 8765 \
  --fps 10
```

常用参数：

| 参数 | 含义 |
|---|---|
| `--enable-control` | 启用真实 SSCMA 和云台控制；缺少设备地址时立即退出 |
| `--gimbal-ip` | 设备地址；默认读取 `RECAMERA_DEVICE_IP` |
| `--manual-control` | 启动 localhost EventBus，允许 FastAPI UI Event 进入控制链 |
| `--eventbus-host` | EventBus 监听地址，默认 `127.0.0.1` |
| `--eventbus-port` | EventBus 端口，默认 `8765` |
| `--fps` | 控制循环频率，默认 `10` |
| `--max-cycles` | 最大循环数，`0` 表示持续运行 |
| `--face-conf` | 人脸置信度阈值，默认 `0.60` |
| `--person-conf` | 人体置信度阈值，默认 `0.42` |

若省略 `--manual-control`，Dashboard 无法建立 feature session，控制运行时保持 `inactive`，不会执行自动视觉、DOA 或手动控制。

### 5.4 停止

在两个终端分别按 `Ctrl+C`。`main_phase3.py` 在正常退出和进程退出钩子中发送 stop；仍应观察设备确认云台停止。

紧急情况下：

1. 先停止 `main_phase3.py`。
2. 确认 EventBus 端口不再监听。
3. 必要时断开设备电源或网络。

```bash
ss -lntp | grep 8765
```

---

## 6. Dashboard 操作

### 6.1 页面入口

| 路由 | 页面 | 数据 |
|---|---|---|
| `/control`、`/v2` | Control Dashboard | FastAPI 真实视频、感知、录音状态和 UI Event 请求 |
| `/home` | 产品 Demo | Mock 展示 |
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

### 6.2 页面生命周期

- 打开或切换到页面后只显示信息，不会自动启动该页功能。
- 必须点击当前页“启动功能”按钮。
- 切换页面前，前端会对旧页面发送对应 stop/deactivate 请求。
- 页面隐藏或关闭时会 best-effort 发送 stop。
- 页面重新可见后保持未启动状态，必须再次点击“启动功能”。
- 网络断开、浏览器崩溃或进程被强制终止时，best-effort 请求不保证送达。

### 6.3 设备地址输入

1. 在顶部输入 `<RECAMERA_IP>`。
2. 点击“保存并重连视频”。
3. 确认状态从“未配置”变为已配置。
4. 等待 `video_connected=true` 和摄像头画面恢复。
5. 若要真实控制云台，另行用同一地址启动或重启 `main_phase3.py`。

### 6.4 人脸追踪与分析

点击“启动功能”后，Dashboard 调用：

```text
POST /api/multi_track/stop
POST /api/single_track/start
POST /api/tracking_mode {"mode":"single"}
```

FastAPI 随后启用单人分析相关处理和展示，包括摄像头、检测结果、情绪、专注、EAR、PERCLOS 和眨眼率。点击暂停或离开页面时调用 `/api/single_track/stop`。

注意：这些接口当前只管理 FastAPI 的感知状态，不会启停独立 `main_phase3.py` 的视觉控制循环，详见第 11 节。

### 6.5 声源 yaw 跟随

点击“启动功能”后，Dashboard 调用：

```text
POST /api/single_track/stop
POST /api/multi_track/start {"save_audio":false}
POST /api/tracking_mode {"mode":"multi"}
```

页面同时展示 ReSpeaker DOA、实体 WS2812 灯环状态和 reCamera yaw readback。FastAPI 将 DOA 转成 audio Event；只有 runtime 处于 `multi_sound_yaw` 且租约有效时，Orchestrator 才生成 yaw-only command。

### 6.6 会议录音

点击“启动功能”后，Dashboard 调用 `/api/conversation/start`，录音对象由 `RECAMERA_AUDIO_DEVICE` 决定。页面同时显示 DOA/LED 示意，并允许请求多人模式。

离开页面会请求：

```text
POST /api/conversation/stop {"finalize":true}
POST /api/multi_track/stop {"finalize":false}
```

会议摘要需要有效 WAV 片段、`faster-whisper`，并建议配置 `DEEPSEEK_API_KEY`。

### 6.7 手动云台调试

1. 确认 `main_phase3.py` 使用 `--enable-control --manual-control` 运行。
2. 进入“手动云台调试”。
3. 点击“启动功能”解锁按钮。
4. 方向键调用 `/api/gimbal/move`，回中调用 `/api/gimbal/home`。

FastAPI 会把请求转换为 UI Event。Orchestrator 将 D-Pad delta 限制到每轴最大 `2.5` 度，SafetyLayer 再对最终命令执行 hard-gate 校验。

---

## 7. ReSpeaker、DOA 与会议录音

### 7.1 USB 直连 WSL

先查询实际 BUSID，不要依赖固定值：

```bash
usbipd.exe list
```

若状态为 `Not shared`，在管理员 PowerShell 中执行：

```powershell
usbipd bind --busid <BUSID>
```

在 WSL 中挂载并验证：

```bash
usbipd.exe attach --busid <BUSID> --wsl
lsusb | grep 2886
```

### 7.2 查询会议录音的音频设备索引

`RECAMERA_AUDIO_DEVICE` 是 **WSL 中 Python `sounddevice` 枚举出的录音输入设备索引**。
它不是 `usbipd` 的 `<BUSID>`、ReSpeaker 的 USB VID/PID，也不是 reCamera IP。

ReSpeaker attach 到 WSL 后查询音频设备：

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

找到名称包含 `ReSpeaker`、`XVF3800` 或 `USB Audio`，并且 `max_input_channels`
大于 `0` 的条目。例如：

```text
2 ReSpeaker XVF3800, ALSA (2 in, 2 out)
```

这里的音频设备索引是 `2`，应配置为：

```bash
export RECAMERA_AUDIO_DEVICE=2
python3 -c "import sounddevice as sd; print(sd.query_devices(int('$RECAMERA_AUDIO_DEVICE')))"
```

第二条命令用于确认所选设备名称和输入通道数。USB 重新 attach、WSL 重启或音频设备增减后，
索引可能变化；每次部署或录音设备异常时应重新查询，不要长期写死。

该索引只供 `sounddevice` 通过 ReSpeaker USB Audio Class 进行会议录音。
DOA/VAD 和实体 LED 使用同一 ReSpeaker 的 USB control interface，不使用音频设备索引。

启动 FastAPI：

```bash
export RECAMERA_DOA_SOURCE=usb
export RECAMERA_AUDIO_DEVICE=<AUDIO_DEVICE_INDEX>
python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
```

结束后可归还给 Windows：

```bash
usbipd.exe detach --busid <BUSID>
```

### 7.3 TCP DOA

FastAPI 默认监听 `0.0.0.0:9999`。查询 WSL 地址：

```bash
hostname -I
```

Windows 发送端使用查询到的 `<WSL_IP>`：

```cmd
python tools\send_doa_tcp.py --host <WSL_IP> --mock-angle 35
```

WSL 本机测试：

```bash
python3 tools/send_doa_tcp.py --host 127.0.0.1 --mock-angle 35
```

支持纯角度、带单位文本、xvf_host 输出和 JSON。推荐格式：

```json
{"azimuth_deg":35,"speech":true}
```

### 7.4 DOA 与 LED 验证

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
respeaker.led.hardware = true
```

USB 生产模式不要求监听 9999；只有 `RECAMERA_DOA_SOURCE=tcp` 时才使用
`ss -lntp | grep 9999`，且此时 `respeaker.led.hardware=false`。

这些字段证明 ReSpeaker 输入有效；最终硬件闭环还应同时确认 `control.active_feature=multi_sound_yaw`、`gimbal.source=motor_readback` 和 yaw 数值变化。

---

## 8. API 与 EventBus 速查

### 8.1 状态和视频

```bash
curl http://localhost:8001/api/health
curl http://localhost:8001/api/state
curl http://localhost:8001/api/device/config
curl http://localhost:8001/api/debug/video
curl http://localhost:8001/api/snapshot --output snapshot.jpg
```

### 8.2 场景状态

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

### 8.3 云台 UI Event

```bash
curl -X POST http://localhost:8001/api/gimbal/move \
  -H 'Content-Type: application/json' \
  -d '{"pan":5,"tilt":0}'

curl -X POST http://localhost:8001/api/gimbal/home
```

EventBus 未启动时，响应应包含 `accepted=false`、`authority=unreachable`。EventBus 可达时，响应应包含 `accepted=true`、`authority=main_phase3`。

### 8.4 录音和会议摘要

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

### 8.5 EventBus 端口

```bash
ss -lntp | grep 8765
nc -zv 127.0.0.1 8765
```

EventBus 只接受统一 Event JSON，每条消息以换行结束。通常应通过 FastAPI API 使用，不建议在生产操作中手写 socket 消息。

---

## 9. 分层验收

### 9.1 FastAPI 无设备模式

```bash
python3 recamera_fastapi.py
curl http://localhost:8001/api/health
curl http://localhost:8001/api/device/config
```

验收：

- `/control` 返回 200。
- `configured=false`。
- 服务不会因缺少设备地址退出。

### 9.2 视频重连

在 Dashboard 输入设备地址，或调用 `POST /api/device/config`。

验收：

- `configured=true`。
- `sscma_url` 使用输入地址。
- SSCMA 正常时 `video_connected=true`。
- `/video_feed` 显示实时画面。

### 9.3 EventBus

先只启动 FastAPI，调用 `/api/gimbal/home`，应得到 unreachable。再启动：

```bash
python3 main_phase3.py \
  --enable-control \
  --gimbal-ip "$RECAMERA_DEVICE_IP" \
  --manual-control
```

再次调用 `/api/gimbal/home`。

验收：

- EventBus 监听 `127.0.0.1:8765`。
- API 返回 `accepted=true`。
- 返回 authority 为 `main_phase3`。
- 控制运行时日志出现对应事件/命令处理。

### 9.4 Dashboard 生命周期

验收顺序：

1. 进入任一页面，功能按钮保持锁定或空闲。
2. 点击“启动功能”后才调用对应 start API。
3. 切换页面，旧页面发送 stop。
4. 新页面不自动启动。
5. 隐藏并恢复页面后，需要再次点击“启动功能”。

### 9.5 真实硬件动作

只在周围无障碍物时测试：

1. 启动完整系统。
2. 在手动云台页点击“启动功能”。
3. 发送一次小幅 yaw delta。
4. 观察 EventBus 响应、控制运行时日志和设备实际动作。
5. 调用 home，再停止控制运行时。

不要仅根据 Dashboard 中的 command、状态标签或 DOA 示意判定硬件动作成功。

---

## 10. 故障排查与安全停机

### 10.1 地址未配置或变量未展开

```bash
printf '%s\n' "$RECAMERA_DEVICE_IP"
```

为空时重新 export。日志若显示尝试连接名为 `RECAMERA_DEVICE_IP` 的主机，说明命令漏写了 `$` 和引号。

正确：

```bash
--gimbal-ip "$RECAMERA_DEVICE_IP"
```

### 10.2 设备可达但视频断开

```bash
nc -zv "$RECAMERA_DEVICE_IP" 8090
curl http://localhost:8001/api/device/config
curl http://localhost:8001/api/debug/video
```

- `Connection refused`：设备在线，但 SSCMA 服务或模型未运行。
- `Timed out` / `No route to host`：地址错误、路由或网络问题。
- 在设备 Web 页面启动模型部署后再次检查 8090。

### 10.3 Dashboard 控制请求 unreachable

```bash
ss -lntp | grep 8765
```

确认 `main_phase3.py` 带 `--manual-control`，且 FastAPI 与控制运行时使用同一主机上的 `127.0.0.1:8765`。若修改端口，当前 FastAPI EventBusClient 默认仍使用 8765，不能只改服务端参数。

### 10.4 控制事件 accepted 但云台不动

依次检查：

1. `main_phase3.py` 是否带 `--enable-control`。
2. 设备地址和 1880 端口是否可达。
3. SafetyLayer 是否因 rate limit、范围或 safe mode 拦截。
4. `RecameraClient` 是否已连接。
5. 控制运行时日志是否出现命令应用失败。

### 10.5 DOA 没有数据

```bash
lsusb | grep -i '2886:001a'
python3 -c "from audio.respeaker_doa import ReSpeakerDOAReader; r=ReSpeakerDOAReader(); print(r.status())"
curl http://localhost:8001/api/state | python3 -m json.tool
```

生产模式先确认 `RECAMERA_DOA_SOURCE=usb`，并检查 `usbipd` attach、USB 权限和
`respeaker.connected`。仅在 USB 无法直连、明确启用
`RECAMERA_DOA_SOURCE=tcp` 时，再检查 `ss -lntp | grep 9999`，并用
`python3 tools/send_doa_tcp.py --host 127.0.0.1 --mock-angle 35` 验证备用通道；
TCP 模式的 `respeaker.led.hardware=false` 是预期行为。

### 10.6 会议录音失败

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
python3 -c "import os, sounddevice as sd; i=int(os.environ['RECAMERA_AUDIO_DEVICE']); print(sd.query_devices(i)); print('input channels=', sd.query_devices(i)['max_input_channels'])"
curl http://localhost:8001/api/conversation/debug
```

确认 `RECAMERA_AUDIO_DEVICE` 是 WSL 中 ReSpeaker 的 `sounddevice` 输入索引，且
`max_input_channels > 0`。如果环境变量不存在、索引越界或指向输出设备，重新执行
7.2 节的枚举流程。摘要为空时确认存在有效 WAV、语音片段足够长，并已安装
`faster-whisper`。

### 10.7 安全原则

1. FastAPI 不直接控制硬件。
2. 只有 `main_phase3.py` 能调用真实 `apply_command()`。
3. UI/manual 输入必须经过 EventBus 和 Orchestrator。
4. SafetyLayer 只允许或阻止最终命令，不修改命令。
5. 首次控制前清理云台运动范围内的障碍物。
6. 停止后目视确认硬件静止，不只依赖 HTTP 响应。

---

## 11. 闭环状态与变更记录

### 11.1 当前闭环条件

1. 页面 start/stop/heartbeat 均转换为 Event，`main_phase3.py` 以 session token 和 2.5 秒租约维护唯一控制权。
2. 浏览器异常退出时，租约到期会自动生成 stop；旧标签页不能停止后来接管的新 session。
3. ReSpeaker USB control interface 提供 DOA/VAD 和实体 WS2812 DOA 灯效；USB Audio Class 提供会议录音。
4. DOA 经 EventBus 进入 Orchestrator，多人与会议跟随模式只生成 yaw，不修改 pitch。
5. 云台双轴命令和真实 angle/speed readback 通过配套 Node-RED Flow；FastAPI 只读取 runtime snapshot。
6. Dashboard 地址仍只保存在当前 FastAPI 进程内存中，服务重启后需通过环境变量、CLI 或页面重新配置。

真实硬件运行前必须在 reCamera Node-RED 导入 `deploy/node_red/recamera_control_bridge.json`。导入和验证步骤见同目录 `README.md`。

### 11.2 当前已确认架构状态

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

### 11.3 5.1 变更记录

- 将设备地址配置和可执行 quick start 移到文档开头。
- 修复环境变量展开示例，统一使用 `"$RECAMERA_DEVICE_IP"`。
- 删除已不存在的 FastAPI 旧硬件模式切换流程。
- 对齐 FastAPI 和 `main_phase3.py` 当前 CLI 参数。
- 补充 Dashboard 地址输入的作用域、非持久化行为和双进程地址同步要求。
- 对齐左侧导航、每页“启动功能”和切页 stop 行为。
- 恢复 EventBus、FSM、Orchestrator、SafetyLayer 的真实边界说明。
- 删除 FastAPI 硬件 readback 和 observe-only 控制镜像的过期描述。
- 将固定 WSL IP、ReSpeaker BUSID 改为运行时查询占位符。
- 新增 feature session、2.5 秒租约、旧会话隔离和失联自动 stop。
- 接通 ReSpeaker USB DOA、会议录音和实体 WS2812 DOA 灯效。
- 接通 DOA audio Event 到 yaw-only Orchestrator 命令。
- 新增 Node-RED 双轴 control/status bridge 和真实 CAN motor readback。
