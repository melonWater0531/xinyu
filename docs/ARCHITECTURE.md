# reCamera Multimodal — 系统架构与已实现功能

## 0. 功能场景架构概览

系统使用一个权威控制会话，模式为 `inactive`、`single_face_analysis`、
`multi_sound_yaw`、`meeting_recording`、`meeting_sound_yaw` 或
`manual_gimbal_debug`。Dashboard 页面只能发出 Event；会话由
`main_phase3.py` 确认并以 `session_id + 2.5s lease` 维护。

| 页面 | 输入硬件 | 输出硬件 | 控制行为 |
|---|---|---|---|
| 人脸追踪与分析 | reCamera SSCMA 摄像头 | reCamera yaw/pitch CAN 电机 | 人脸对准后并行展示情绪、专注、EAR/PERCLOS |
| 声源 yaw 跟随 | ReSpeaker XVF3800 四麦阵列；reCamera 仅展示视频 | reCamera yaw 电机；ReSpeaker 12 LED 灯环 | USB DOA Event 驱动 yaw，pitch 始终为空；实体 LED 使用硬件 DOA 灯效 |
| 会议录音 | ReSpeaker USB Audio + USB DOA | ReSpeaker LED；可选 reCamera yaw | 默认只录音；显式开启会议跟随后才驱动 yaw |
| 手势交互 | reCamera SSCMA 摄像头 + MediaPipe Gesture Recognizer | Dashboard intent/toast/local feedback | Open Palm、Closed Fist、Thumb Up、Thumb Down、Victory 只映射陪伴 intent，不进入云台控制 |
| 健康/PWA | Dashboard localStorage + 实时 emotion/attention/eye/gaze state | 本地 PWA Notification | 护眼、久坐、喝水、疲劳、低专注、情绪关心通知在前端本地治理 |
| LLM/日记 | DeepSeek 可选；本地 fallback | Dashboard 日记、反思、会议摘要 | 无 API key 时返回本地温和建议，不丢失日记 |
| 手动云台 | Dashboard UI Event | reCamera yaw/pitch CAN 电机 | 有效 manual session 才接受 D-Pad/home |

`/control` 是所有已部署功能的集合面板。每个功能卡都有独立启动和终止
按钮；页面中的 Sleep、Standby、Stop、Calibrate 均通过 FastAPI 发出 UI
Event，再由 `main_phase3.py`、Orchestrator、SafetyLayer 和
`RecameraClient` 进入 Node-RED bridge。FastAPI 不直接打开硬件控制客户端。

官方 reCamera Gimbal 面板语义在本系统中固定为：

| 操作 | 控制语义 |
|---|---|
| Standby | `yaw=180, pitch=90, speed=360` |
| Sleep | `yaw=180, pitch=175, speed=360` |
| Calibrate | Node-RED bridge `/recamera-control/v1/calibrate` 执行 `gimbal cali`，并撤销当前 device lease |

ReSpeaker USB control 以 10 Hz 读取 DOA/VAD，并与 LED 写入共用 USB 锁；
USB Audio Class 由 `sounddevice` 独立录音。TCP `9999` 仅是 DOA 备用输入，
该模式不能控制主机上的实体 ReSpeaker LED，telemetry 会报告
`led.hardware=false`。

FastAPI 的视频、分析、录音和 Dashboard 状态通过 `/ws` 与 `/api/state`
发布；权威 FSM、会话、命令、安全结果和云台 readback 均来自
`main_phase3.py` 的 EventBus runtime snapshot，不存在 observe-only FSM 镜像。

### 控制层（独立于 FastAPI）

```
main_phase3.py（唯一硬件出口）
  → EventBus Event → Orchestrator → FSM
  → SafetyLayer hard gate
  → RecameraClient.apply_command()
  → Node-RED HTTP bridge → CAN yaw/pitch

FastAPI = UI Event emitter + perception/recording + runtime telemetry viewer
  NEVER 调用 RecameraClient/apply_command
```

---

> 版本：2.0
> 日期：2026-06-28
> 基于：全功能硬件闭环审计

---

## 1. 控制路径审计结论

| 项目 | 结论 |
|---|---|
| 是否存在 hidden bypass | **NO** |
| Production safety | **CONDITIONAL** - requires the matching lease/watchdog bridge |
| apply_command 调用点 | **仅 `main_phase3.py` control runtime** |
| FastAPI 是否调用 apply_command | **NEVER**（注释确认，代码无调用） |
| 是否有第二 FSM | **NO**（FastAPI 只查询 main runtime snapshot） |
| 孤立 PD 模块 | `core/control_filter.py`（有比例增益代码，但**未被任何生产代码 import**，属于孤立遗留模块） |

控制闭环、session 租约与设备看门狗的整改细节见 [CONTROL_CLOSURE.md](CONTROL_CLOSURE.md)。

---

## 2. 单控制平面架构（SINGLE CONTROL PLANE）

```
真实控制平面（main_phase3.py，唯一硬件出口）
═══════════════════════════════════════════════
Event 输入
  ├─ main_phase3 Vision adapter
  ├─ FastAPI ReSpeaker audio adapter
  └─ FastAPI Dashboard UI emitter
        │
        ▼
  Orchestrator.handle_event(event)
        │   session/lease/mode gate → FSM.transition()
        │   FSM: core/fsm.py（SystemState: IDLE/AUDIO_SEARCH/VISION_TRACK/FUSED_TRACK/LOST）
        │
        ▼
  ControlCommand("orchestrator", yaw=..., pitch=..., reason=...)
        │
        ▼
  Phase3Runner._apply(command)              ← 唯一调用 apply_command 的生产路径
        │
        ▼
  RecameraClient.apply_command(command)    ← 唯一硬件出口
        │
        └─ HTTP: /recamera-control/v1/{command,stop,status} → reCamera Node-RED :1880


FastAPI（recamera_fastapi.py）— Event emitter + telemetry viewer，零云台控制权
═══════════════════════════════════════════════════
感知输入（同一套）
  └─ SSCMAVideoClient + ReSpeaker USB DOA/Audio/LED
        │
        ▼
  EventBusClient.emit(Event)
        │   feature session / heartbeat / audio / manual UI Event
        │
        ▼
  runtime_snapshot_request
        │   main_phase3 返回 session/FSM/command/safety/gimbal readback
        │
        ▼
  build_state_snapshot() → /ws WebSocket push（每 200ms）
        │
        ├─ PAGE 1 /control /v2  → recamera_v2_live.html（Event 控制 + 实时遥测）
        └─ PAGE 2 /home         → home.html（纯 mock，isFilePreview=true）

云台遥测（main_phase3 侧）
  RecameraClient.get_status() → runtime snapshot → EventBus → FastAPI /ws
```

---

## 3. 控制路径风险点列表

| 风险 ID | 位置 | 描述 | 级别 | 结论 |
|---|---|---|---|---|
| R01 | `core/control_filter.py` | 含比例增益/EMA 控制数学，有 `ControlFilter` 类 | WARNING | **孤立模块**：无任何生产代码 import，不在控制链路中 |
| R02 | `hardware/recamera_client.py` | 旧 Socket.IO `widget-change` shadow transport | DELETED | 已由版本化 Node-RED HTTP bridge 替代 |
| R03 | `tools/run_orchestrator_mvp.py` | 含 `MockGimbal.apply_command()` 调用 | OK | 开发工具，不被任何生产入口 import |
| R04 | `recamera_fastapi.py` runtime cache | FastAPI 展示控制状态 | OK | 状态来自 main runtime snapshot，无 Orchestrator 实例 |
| R05 | `core/orchestrator.py:144` | `err_x = target.vision_cx - 0.5`（比例控制） | OK | 合法控制平面内部计算，在授权路径上 |
| R06 | 旧 widget-change / `_pd_step` | 不完整 yaw-only shadow transport | DELETED | 已由版本化 Node-RED bridge 替代 |

---

## 4. 模块职责表

### 4.1 控制核心（`core/`）

| 文件 | 生产状态 | 职责 |
|---|---|---|
| `core/fsm.py` | **ACTIVE** | 单一 FSM：SystemState × Event → SystemState；含 debounce |
| `core/orchestrator.py` | **ACTIVE** | 唯一决策引擎；持有 FSM 实例；输出 ControlCommand |
| `core/event.py` | **ACTIVE** | 数据类：Event、BBox、ControlCommand（均为 frozen dataclass） |
| `core/safety_layer.py` | **ACTIVE** | ControlCommand hard gate；rate/step/range/speed 仅 allow/block |
| `core/control_filter.py` | **ORPHAN** | 遗留比例控制平滑模块；未被生产代码 import，可删除 |

### 4.2 硬件（`hardware/`）

| 文件 | 职责 |
|---|---|
| `hardware/recamera_client.py` | 唯一云台硬件出口；Node-RED HTTP bridge 双轴命令、stop 和真实 readback |

### 4.3 视觉感知（`vision/`）

| 文件 | 职责 |
|---|---|
| `vision/video_stream.py` | SSCMAVideoClient：`ws://<device>:8090/` JPEG + SSCMA 检测框 |
| `vision/face_tracker_v2.py` | InsightFace/SCRFD + Kalman/ByteTrack + ArcFace 多脸追踪 |
| `vision/pose_estimator.py` | YOLO11 pose：人体框 + 17 关键点 |
| `vision/mediapipe_face.py` | Face Landmarker：精细面部 468 点 |
| `vision/emotieff_adapter.py` | EmotiEffLib 情绪推理（8 类情绪 + 置信度） |
| `vision/attention_engine.py` | EMA 专注度评分（0–100）+ 基线自适应 |
| `vision/eye_metrics.py` | EAR、眨眼率、PERCLOS 眼部指标 |
| `vision/face_crop.py` | 人脸区域裁剪工具 |
| `vision/llm_reflect.py` | 本地轻量日记反思 fallback（无 DeepSeek 时） |
| `vision/data_source.py` | VisionDataSource 抽象 + Mock 实现（供 main_phase3.py 使用） |
| `vision/mock_data_generator.py` | Mock 检测框生成器 |

### 4.4 音频（`audio/`）

| 文件 | 职责 |
|---|---|
| `audio/network_doa.py` | TCP `0.0.0.0:9999` 接收器；解析 DOA 文本/JSON；维护 age、has_speech |
| `audio/doa.py` | DOA 文本解析器 + 可插拔 source 基础实现 |
| `audio/respeaker_doa.py` | USB HID DOA（旧路径，可选 fallback） |
| `audio/conversation_recorder.py` | 可选录音会话管理（`save_audio=true` 时启用） |
| `audio/wake_word.py` | 唤醒词检测（可选） |

### 4.5 主入口

| 文件 | 用途 |
|---|---|
| `recamera_fastapi.py` | **主生产入口**：视频+感知+遥测+页面，无控制权 |
| `main_phase3.py` | **控制进程**：Phase3Runner → Orchestrator → RecameraClient；单 FSM 控制平面 |
| `recamera_demo.py` | 轻量演示入口（非主流程） |

### 4.6 前端（`dashboard/`）

| 文件 | 路由 | 用途 |
|---|---|---|
| `recamera_v2_live.html` | `/control` `/v2` | PAGE 1：实时控制台（只读遥测 + FSM 可观测） |
| `home.html` | `/home` `/`（重定向） | PAGE 2：产品 Demo（纯 mock，isFilePreview=true） |
| `manifest.webmanifest` | `/manifest.webmanifest` | PWA 清单 |
| `sw.js` | `/sw.js` | Service Worker |

---

## 5. FSM 状态转移图

```
                    audio/speech_detected
         ┌──────────────────────────────────────┐
         │                                      ▼
       IDLE ──vision/target_detected──> VISION_TRACK
         │                                  │        ▲
         │                   vision/target_lost(x30)  │ audio/speech_detected
         │                                  ▼        │
         │              LOST ◄───────── audio/timeout─┤
         │               │                            │
         │    audio/speech_detected              FUSED_TRACK
         │               └──────────────────────────► │
         │                                            │ vision/target_lost(x30)
         └──────────────────────────────► AUDIO_SEARCH ◄─┘
                                              │
                                    audio/timeout → LOST
                               vision/target_detected(x3) → FUSED_TRACK

Debounce:
  IDLE→VISION_TRACK          : 3 frames
  AUDIO_SEARCH→FUSED_TRACK   : 3 frames
  VISION_TRACK→LOST          : 30 frames
  FUSED_TRACK→LOST/AUDIO     : 30 frames (vision_lost)
  LOST→VISION_TRACK          : 3 frames
  LOST→IDLE (timeout)        : 10 frames
```

---

## 6. WebSocket 状态快照结构（`/ws`，每 200ms）

```json
{
  "type": "state_snapshot",
  "data": {
    "video":    { "connected", "fps", "width", "height", "detections" },
    "pose":     { "persons": [...], "count" },
    "gimbal":   { "connected", "yaw", "pitch", "yaw_speed", "pitch_speed", "source", "age_ms" },
    "respeaker":{ "connected", "doa_deg", "has_speech", "audio_device", "led" },
    "attention":{ "has_face", "score", "state", "blink_count" },
    "emotieff": { "emotion", "confidence", "probabilities" },
    "eye_metrics": { "ear", "perclos", "blink_rate" },
    "mp_face":  { "available", "landmarks_count" },
    "conversation": { "mode", "active", "state" },
    "control":  {
      "feature": "inactive|single_face_analysis|multi_sound_yaw|meeting_recording|meeting_sound_yaw|manual_gimbal_debug",
      "session_id": "...",
      "lease_remaining_ms": 2500,
      "fsm_state": "IDLE|AUDIO_SEARCH|VISION_TRACK|FUSED_TRACK|LOST",
      "authority": "idle|audio|vision|fusion|lost",
      "last_event": { "type", "name", "source" },
      "command": { "reason", "yaw", "pitch", "speed", "stop", "source" },
      "safety": { "ok": bool, "reason": str },
      "vision_lost_frames": int
    },
    "trace": [
      { "ts", "from", "state", "event", "cmd" }
    ],
    "health": {
      "video_fps", "ws_clients", "doa_age", "gimbal_latency_ms", "gimbal_connected"
    }
  }
}
```

---

## 7. 已实现功能清单

### 7.1 视觉感知
- [x] SSCMA WebSocket 视频接入（`ws://<device>:8090/`）→ MJPEG `/video_feed`
- [x] SSCMA 检测框解析（`[cx,cy,w,h,conf,cls]` 格式）
- [x] FaceTrackerV2：SCRFD 检脸 + Kalman/ByteTrack 时序跟踪 + ArcFace 特征
- [x] YOLO11 pose：人体检测 + 17 关键点（含肩部估计）
- [x] MediaPipe Face Landmarker：精细面部 468 点（可选，提升精度）
- [x] 视觉丢帧计数（`vision_lost_frames`）+ debounce 保护

### 7.2 情绪与专注
- [x] EmotiEffLib 情绪分类（8 类：Happy/Sad/Angry/Fear/Surprise/Disgust/Contempt/Neutral）
- [x] 置信度 + 概率分布输出
- [x] AttentionEngine：EMA 平滑专注评分（0–100）+ 个人基线自适应
- [x] 眼部指标：EAR（Eye Aspect Ratio）、PERCLOS、眨眼率

### 7.3 音频 / DOA
- [x] TCP DOA 接收（`0.0.0.0:9999`）：支持纯角度/JSON/xvf_host 文本格式
- [x] speech hold 机制（默认 0.8s，每个有效包刷新）
- [x] DOA age 管理（过期数据不驱动控制）
- [x] ReSpeaker XVF3800 USB control（生产默认）；TCP 9999 仅备用
- [x] 可选录音会话（`save_audio=true`）

### 7.4 控制系统（main_phase3.py）
- [x] 单 FSM 控制平面（5 状态 × 事件表驱动 + debounce）
- [x] Orchestrator 多源融合（vision / audio / fused 策略）
- [x] SafetyLayer hard gate：rate/step/range/speed 只做 allow/block，绝不改写命令
- [x] RecameraClient：版本化 Node-RED HTTP bridge；双轴命令、stop、真实 CAN readback、fail closed
- [x] atexit 紧急停止（进程退出时自动发送 stop 指令）

### 7.5 FastAPI 服务（recamera_fastapi.py）
- [x] MJPEG 视频流 `/video_feed`（含 overlay：检测框/关键点）
- [x] WebSocket `/ws`：状态快照推送（200ms 间隔）+ `request_state` 拉取
- [x] Runtime snapshot：FastAPI 通过 EventBus 查询 `main_phase3.py` 的 FSM、session、command、safety 和 telemetry
- [x] 云台遥测：仅 `main_phase3.py` 调用 `RecameraClient.get_status()`，FastAPI 不创建硬件客户端
- [x] LLM 对话：DeepSeek API（`/api/chat`）+ 本地轻量 fallback
- [x] 对话会话管理：`/api/conversation/{start,stop,state,save,debug}`
- [x] LLM 反思：`/api/reflect`（情绪日记生成）
- [x] API 快照：`/api/snapshot`（当前帧 JPEG）
- [x] 健康检查：`/api/health`

### 7.6 前端
- [x] PAGE 1（`/control`，`/v2`）：实时控制台
  - FSM 状态可视化（5 节点高亮当前态）
  - Authority 显示（audio/vision/fusion/idle）
  - 决策链（last event → FSM → command → safety gate）
  - 决策 trace 滚动日志（最近 12 条）
  - Gimbal 遥测（yaw/pitch/speed，硬件 readback）
  - Perception 通道（DOA 方向、人体数、声音状态、视觉丢帧）
  - 单人分析（专注度/情绪/眼部指标）
  - 系统健康（FPS/DOA age/WS 客户端/云台 RTT）
  - 实时 MJPEG 视频（含检测框 overlay）
- [x] PAGE 2（`/home`）：产品 Demo
  - `isFilePreview=true`（所有网络调用短路）
  - `mockState()` 动画化模拟数据（情绪轮转、DOA/专注正弦波动）
  - 情绪监测、专注度、多人场景、日记、LLM 对话、健康建议 UI
  - PWA 支持（manifest + service worker）

---

## 8. 网络端口总览

| 地址/端口 | 服务 | 方向 |
|---|---|---|
| `RECAMERA_DEVICE_IP:1880` | Node-RED control/status HTTP bridge | `main_phase3.py` ↔ 云台（双轴命令 + CAN readback） |
| `RECAMERA_DEVICE_IP:8090` | SSCMA WebSocket | reCamera → `recamera_fastapi.py`（视频接收） |
| `0.0.0.0:9999` | Network DOA TCP（备用） | 远端 ReSpeaker host → `recamera_fastapi.py`；无实体 LED 控制 |
| `0.0.0.0:8001` | FastAPI HTTP/WS | 浏览器/客户端 ↔ `recamera_fastapi.py` |
| `192.168.42.1:22` | USB SSH | 初始化 |
| `RECAMERA_DEVICE_IP:22` | Wi-Fi SSH | 维护 |

**重要**：FastAPI 不直接连接 Node-RED/1880。仅 `main_phase3.py` 通过 `RecameraClient` 连接云台。

---

## 9. 孤立模块说明

| 模块 | 状态 | 说明 |
|---|---|---|
| `core/control_filter.py` | **ORPHAN** | 含 EMA + Kp 比例控制；未被任何生产文件 import；可在后续清理中删除 |
| `tools/run_orchestrator_mvp.py` | **DEV TOOL** | 含 `MockGimbal.apply_command()`；仅用于开发测试，不在生产链路 |
| `tools/build_function_arch_docx.py` | **DEV TOOL** | 文档生成工具；含已过时模块引用（`gimbal_mode_state.py`、`state_machine.py`），不影响运行 |

---

## 10. 设备地址配置与 Dashboard 收束

reCamera 设备地址不再写死。运行时统一通过 `RECAMERA_DEVICE_IP`、FastAPI 的 `--device-ip`、`main_phase3.py` 的 `--gimbal-ip`，以及 dashboard 顶部“设备地址”输入框配置。

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
python3 main_phase3.py --enable-control --gimbal-ip "$RECAMERA_DEVICE_IP" --manual-control
```

Dashboard 输入框只负责重连 FastAPI 的视频/感知来源（`SSCMAVideoClient` / `/video_feed`），不创建硬件控制客户端，不修改 FSM，不绕过 EventBus。真实云台控制地址仍由 `main_phase3.py` 启动参数或环境变量传入。

最终控制链路唯一：

```text
Dashboard UI -> FastAPI UI Event -> EventBus -> main_phase3.py -> FSM -> Orchestrator -> SafetyLayer -> RecameraClient -> gimbal hardware
```

当前 dashboard 页面结构：

- 单人场景：人脸追踪与分析（找人脸、云台对准、情绪/人脸/专注/眼部指标）
- 多人场景：声源 yaw 跟随
- 多人场景：会议录音（Respeaker 调度，集成声源/LED 示意）
- 设备调试：手动云台

每个页面都有“启动功能”按钮。启动后由 `session_id + 2.5s lease` 维护唯一控制权；切换页面会发送 stop，新页面不会自动启动。浏览器失联时租约到期自动停止，新会话接管后旧 session 的 heartbeat/stop 无效。

---

## 11. 全功能硬件闭环（2026-06-28）

```text
ReSpeaker XVF3800 USB control ── DOA/VAD Event ──> FastAPI Event adapter
ReSpeaker XVF3800 USB Audio   ── 16kHz mono ─────> ConversationRecorder
ReSpeaker XVF3800 WS2812      <── authoritative feature mode

reCamera SSCMA :8090 ───────── vision Event ─────> main_phase3
Dashboard ── UI Event ──> EventBus :8765 ───────> main_phase3
main_phase3 ── ControlCommand ──> SafetyLayer ──> RecameraClient
RecameraClient <── HTTP :1880 ──> Node-RED official motor nodes ──> CAN yaw/pitch
main_phase3 runtime snapshot ──> EventBus ──> FastAPI ──> Dashboard
```

硬件职责：

- **ReSpeaker XVF3800**：四麦克风 DOA/VAD、会议 USB Audio、12 颗 WS2812 实体 DOA 灯环。
- **reCamera Gimbal 2002w**：SSCMA 摄像头、Node-RED bridge、CAN yaw/pitch 电机。
- **Windows/WSL 主机**：FastAPI 感知和录音、EventBus、唯一 control runtime。
- reCamera 自带麦克风、扬声器和补光灯不参与当前业务闭环。

配套设备 Flow 位于 `deploy/node_red/recamera_control_bridge.json`，暴露 command、stop、status 三个版本化 endpoint。Bridge 不可达时 `--enable-control` fail closed。

---

## 12. 健康陪伴感知与本地提醒架构（2026-06-30）

### 12.1 新增组件职责

| 组件 | 所在位置 | 输入 | 输出与边界 |
|---|---|---|---|
| `GazeEstimator` | `vision/gaze_estimator.py` | MediaPipe Face Landmarker 虹膜/眼角点 | 粗粒度 `gaze`；只辅助 attention，不控制云台 |
| `GestureDetector` | `vision/gesture_detector.py` | BGR 视频帧 + MediaPipe Gesture Recognizer | 稳定手势与陪伴 intent；不产生任何控制事件 |
| `EmotionInterventionPolicy` | `core/emotion_intervention.py` | `emotieff`、`attention`、`eye_metrics`、`gaze` | `proactive_intervention`；只产生陪伴状态和文案 |
| 本地通知调度器 | `dashboard/home.html` | 前端计时、实时状态、localStorage | Notification/Service Worker 或站内 toast |

### 12.2 感知循环数据流

```text
SSCMA JPEG
  -> FaceTrackerV2 / YOLO pose
  -> MediaPipe Face Landmarker
       -> EyeMetricTracker
       -> GazeEstimator
  -> AttentionEngine
       orientation 40% + eye 30% + stability 15% + gaze 15%
  -> EmotiEff
  -> EmotionInterventionPolicy (180s window, confidence gate, cooldown)

SSCMA JPEG (每 3 个感知循环)
  -> GestureDetector
  -> 置信度 >= 0.6 + 连续 4 帧 + intent 3s 冷却
  -> gesture state only

聚合状态
  -> build_state_snapshot()
  -> GET /api/state + WebSocket /ws
  -> /home renderCompanionSignals() + evaluateLocalNotifications()
```

当 Face Landmarker 无结果时，`gaze.available=false`；当手势模型缺失时，`gesture.available=false` 并提供 `reason`。这些降级不会阻断情绪、专注、视频或控制主链路。

### 12.3 Attention 的 gaze 融合

`AttentionEngine.update(..., gaze=...)` 保留原头姿、眼部和稳定性证据，并增加 gaze 辅助项：居中 100、左右 68、向下 45、偏离 50，再按 gaze confidence 与默认 70 插值。最终融合权重为 0.40 / 0.30 / 0.15 / 0.15。

该结果表达“视线趋势”，不是精确眼动轨迹，不应用于身份识别、医疗诊断或硬件强控制。

### 12.4 新增状态接口

`/api/state` 与 `/ws` 增加三个状态块：

```json
{
  "gaze": {
    "available": true,
    "state": "center",
    "x_offset": 0.02,
    "y_offset": 0.05,
    "confidence": 0.96
  },
  "gesture": {
    "available": false,
    "name": "",
    "confidence": 0.0,
    "handedness": "",
    "stable_frames": 0,
    "intent": "",
    "intent_ready": false,
    "updated_at": 0.0,
    "reason": "model_missing:models/gesture_recognizer.task"
  },
  "proactive_intervention": {
    "active": false,
    "type": "",
    "reason": "collecting",
    "message": "",
    "cooldown_remaining_sec": 0
  }
}
```

### 12.5 手势 intent 边界

| Gesture Recognizer 类别 | intent | 消费方 |
|---|---|---|
| `Open_Palm` | `summon_xinyu` | `/home` 聊天区与 toast |
| `Closed_Fist` | `pause_or_mute` | `/home` 当前提醒状态 |
| `Thumb_Up` | `feedback_positive` | localStorage 轻量反馈 |
| `Thumb_Down` | `feedback_negative` | localStorage 轻量反馈 |
| `Victory` | `capture_positive_moment` | `/home` 日记草稿 |

这五种 intent 均不进入 EventBus，不调用 Orchestrator，也不产生 `gimbal_*`、`feature_*` 或 `dpad_*`。

### 12.6 PWA 本地通知数据流

```text
前端健康计时（护眼/久坐/喝水）
后端实时状态（疲劳/低专注/情绪关心）
  -> evaluateLocalNotifications()
  -> enabled + quiet hours + per-type cooldown
  -> ServiceWorkerRegistration.showNotification()
     -> notificationclick
     -> /home#health 或 /home#home
```

配置和发送记录保存在 localStorage。默认安静时段 22:30-08:30；普通类型冷却 30 分钟，情绪关心 60 分钟。浏览器不支持或未授权时只显示站内提示。该架构不是服务器 Web Push：页面关闭或被移动系统冻结后，不保证继续调度。

### 12.7 安全与隐私边界

- 通知不包含截图、日记正文、会议原文或敏感内容。
- 主动情绪干预不做心理诊断，不自动外发数据。
- 手势只做低风险陪伴动作，不控制云台、不自动录音。
- gaze 只作为 attention 辅助证据，缺失时保持原评分链可用。
- ntfy、Telegram、MQTT、跌倒检测、声音事件检测均不在当前主链路。
