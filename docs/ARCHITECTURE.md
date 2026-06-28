# reCamera Multimodal — 系统架构与已实现功能

## 0. 功能场景架构概览

> 系统支持两种独立场景，共用同一套感知基础设施。两个场景由前端 feature toggle 激活，后端通过 `_single_track_active` / `_multi_track_active` 两个全局标志区分。

---

### 场景 A：日常场景（单人学习/工作）

```
触发：/api/single_track/start → _single_track_active = True

Vision 链路
─────────────────────────────────────────────────────────────
  SSCMA 摄像头 (ws://device:8090/)
    → SSCMAVideoClient  (JPEG + YOLO boxes, 5Hz)
    → FaceTrackerV2     (SCRFD 人脸检测 + ByteTrack 追踪)
    → AttentionEngine   (head pose via solvePnP → 专注分 0-100)
    → EmotiEffLib       (8类情绪 + valence + 置信度)
    → MediaPipe FaceMesh (468点网格 → EAR / blink rate / PERCLOS)
    → build_state_snapshot(): .emotion / .attention / .eye_metrics

Audio 链路（文字输入，无麦克风采集）
─────────────────────────────────────────────────────────────
  用户文字 → POST /api/chat (DeepSeek)   → 情绪陪伴回复
  日记写入 → POST /api/reflect (DeepSeek) → {diary, reply}

输出（home.html page-home）
─────────────────────────────────────────────────────────────
  情绪监测卡 · 专注记录卡 · 情绪日记 · 周趋势 · LLM 对话
```

---

### 场景 B：工作场景（多人会议/讨论）

```
触发：/api/multi_track/start → _multi_track_active = True

Vision 链路
─────────────────────────────────────────────────────────────
  SSCMA 摄像头 (ws://device:8090/)
    → SSCMAVideoClient  (JPEG + YOLO boxes)
    → PoseEstimator     (YOLO11n-pose → 17 COCO 关键点)
    → _latest_pose_persons (人数 + bbox + wrist/elbow/shoulder)
    → build_state_snapshot(): .pose.count / .pose.persons

Audio 链路（麦克风实时采集）
─────────────────────────────────────────────────────────────
  ReSpeaker XVF3800
    → NetworkDOA (TCP 0.0.0.0:9999) → doa_deg + has_speech
    → ConversationRecorder
         _audio_callback()   ← sounddevice 16kHz mono float32
         _segment_loop()     ← RMS VAD + DOA 触发分段
         _finalize_segment() → WAV + ConversationTurn → timeline.jsonl
    → POST /api/meeting/summarize
         → audio/transcriber.py (faster-whisper tiny)
         → DeepSeek LLM → {diary, summary}

输出（home.html page-home 多人场景卡）
─────────────────────────────────────────────────────────────
  DOA 方向 · 人数统计 · 声源状态 · 会议记录写入
```

---

### 共用感知基础设施

```
┌─────────────────────────────────────────────────────────┐
│                  state_push_loop (5Hz)                   │
│  SSCMAVideoClient ─→ Pose/Face/Attention/Emotion 推理    │
│  NetworkDOA       ─→ DOA 状态更新                        │
│  _observe_control_step ─→ FSM 决策链（只读镜像）          │
│                   ↓                                      │
│         build_state_snapshot()                          │
│                   ↓                                      │
│      /ws WebSocket broadcast (每帧)                      │
│      /api/state  GET（按需轮询）                          │
└─────────────────────────────────────────────────────────┘
```

### 控制层（独立于 FastAPI）

```
main_phase3.py（唯一硬件出口）
  → Orchestrator FSM (core/orchestrator.py)
    → RecameraClient.apply_command() (hardware/recamera_client.py)
      → Socket.IO / HTTP → reCamera 硬件

FastAPI 内 _observer = Orchestrator(observe_only)
  NEVER 调用 apply_command（设计不变量）
  /api/gimbal/home|move 例外：仅在非 dry-run 且 main_phase3.py 未运行时使用
```

---

> 版本：1.0  
> 日期：2026-06-26  
> 基于：深度控制路径审计（audit date 2026-06-26）

---

## 1. 控制路径审计结论

| 项目 | 结论 |
|---|---|
| 是否存在 hidden bypass | **NO** |
| 是否 production safe | **YES** |
| apply_command 调用点数 | **2**（均在 `main_phase3.py`，atexit + 主循环） |
| FastAPI 是否调用 apply_command | **NEVER**（注释确认，代码无调用） |
| 是否有第二 FSM | **NO**（FastAPI 内的 `_observer.fsm` 是 observe-only 镜像，不持有控制权） |
| 孤立 PD 模块 | `core/control_filter.py`（有比例增益代码，但**未被任何生产代码 import**，属于孤立遗留模块） |

---

## 2. 单控制平面架构（SINGLE CONTROL PLANE）

```
真实控制平面（main_phase3.py，唯一硬件出口）
═══════════════════════════════════════════════
感知输入
  └─ VisionDataSource.get_bboxes()
        │
        ▼
  Orchestrator.handle_vision(bboxes)
        │   内部：BBox → Event("vision", ...) → FSM.transition()
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
        ├─ Socket.IO: sio.emit("widget-change", ...) → reCamera Node-RED :1880
        └─ HTTP:  POST /gimbal/control | /api/gimbal | /motor/control → RECAMERA_DEVICE_IP


FastAPI（recamera_fastapi.py）— 纯显示层，零控制权
═══════════════════════════════════════════════════
感知输入（同一套）
  └─ SSCMAVideoClient boxes + NetworkDOA
        │
        ▼
  _observe_control_step()
        │   内部：同样走 _observer.handle() / _observer.handle_vision()
        │   _observer = Orchestrator(...)  ← 独立实例，NEVER 调用 apply_command
        │
        ▼
  _control_obs（FSM state / authority / last_event / command brief / safety）
  _decision_trace（deque，最近 40 条决策链）
        │
        ▼
  build_state_snapshot() → /ws WebSocket push（每 200ms）
        │
        ├─ PAGE 1 /control /v2  → recamera_v2_live.html（实时遥测，只读）
        └─ PAGE 2 /home         → home.html（纯 mock，isFilePreview=true）

云台遥测（FastAPI 侧）
  RecameraClient.get_status()  ← 每 500ms，只读 readback
        └─ _gimbal_tlm → /ws snapshot → PAGE 1 Gimbal Telemetry
```

---

## 3. 控制路径风险点列表

| 风险 ID | 位置 | 描述 | 级别 | 结论 |
|---|---|---|---|---|
| R01 | `core/control_filter.py` | 含比例增益/EMA 控制数学，有 `ControlFilter` 类 | WARNING | **孤立模块**：无任何生产代码 import，不在控制链路中 |
| R02 | `hardware/recamera_client.py:201` | `sio.emit("widget-change", ...)` | OK | 仅在 `apply_command()` 内调用，而 `apply_command` 只被 `main_phase3.py` 调用 |
| R03 | `tools/run_orchestrator_mvp.py` | 含 `MockGimbal.apply_command()` 调用 | OK | 开发工具，不被任何生产入口 import |
| R04 | `recamera_fastapi.py` `_observer` | Orchestrator 实例在 FastAPI 中运行 | OK | 永远不调用 `apply_command`，是 observe-only 遥测镜像 |
| R05 | `core/orchestrator.py:144` | `err_x = target.vision_cx - 0.5`（比例控制） | OK | 合法控制平面内部计算，在授权路径上 |
| R06 | 旧 `GimbalController` / `_pd_step` | Socket.IO shadow 控制 | DELETED | 已在 FINAL CLEANUP 阶段完全删除 |

---

## 4. 模块职责表

### 4.1 控制核心（`core/`）

| 文件 | 生产状态 | 职责 |
|---|---|---|
| `core/fsm.py` | **ACTIVE** | 单一 FSM：SystemState × Event → SystemState；含 debounce |
| `core/orchestrator.py` | **ACTIVE** | 唯一决策引擎；持有 FSM 实例；输出 ControlCommand |
| `core/event.py` | **ACTIVE** | 数据类：Event、BBox、ControlCommand（均为 frozen dataclass） |
| `core/safety_layer.py` | **ACTIVE** | ControlCommand 约束过滤器；rate limit / step / accel 约束 |
| `core/control_filter.py` | **ORPHAN** | 遗留比例控制平滑模块；未被生产代码 import，可删除 |

### 4.2 硬件（`hardware/`）

| 文件 | 职责 |
|---|---|
| `hardware/recamera_client.py` | 唯一硬件出口；`apply_command()` → Socket.IO 或 HTTP POST；`get_status()` 只读 readback |

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
    "gimbal":   { "connected", "yaw", "pitch", "speed", "mode" },
    "doa":      { "available", "source", "doa_deg", "has_speech", "age", "packet_count" },
    "attention":{ "has_face", "score", "state", "blink_count" },
    "emotieff": { "emotion", "confidence", "probabilities" },
    "eye_metrics": { "ear", "perclos", "blink_rate" },
    "mp_face":  { "available", "landmarks_count" },
    "conversation": { "mode", "active", "state" },
    "control":  {
      "observe_only": true,
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
- [x] ReSpeaker USB HID fallback（可选）
- [x] 可选录音会话（`save_audio=true`）

### 7.4 控制系统（main_phase3.py）
- [x] 单 FSM 控制平面（5 状态 × 事件表驱动 + debounce）
- [x] Orchestrator 多源融合（vision / audio / fused 策略）
- [x] SafetyLayer：rate limit（5Hz）/ 步长约束 / 加速度约束
- [x] RecameraClient：Socket.IO + HTTP POST 双传输；dry-run 安全模式
- [x] atexit 紧急停止（进程退出时自动发送 stop 指令）

### 7.5 FastAPI 服务（recamera_fastapi.py）
- [x] MJPEG 视频流 `/video_feed`（含 overlay：检测框/关键点）
- [x] WebSocket `/ws`：状态快照推送（200ms 间隔）+ `request_state` 拉取
- [x] Observe-only Orchestrator 镜像：感知事件 → FSM 计算 → `_control_obs`/`_decision_trace`（零硬件调用）
- [x] 云台遥测：`RecameraClient.get_status()` 每 500ms 轮询（硬件 readback，非控制 state mirror）
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
| `RECAMERA_DEVICE_IP:1880` | Node-RED Dashboard Socket.IO | `main_phase3.py` → 云台（RecameraClient） |
| `RECAMERA_DEVICE_IP:8090` | SSCMA WebSocket | reCamera → `recamera_fastapi.py`（视频接收） |
| `0.0.0.0:9999` | Network DOA TCP | ReSpeaker host → `recamera_fastapi.py` |
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

每个页面都有“启动功能”按钮。切换页面时前一页面会发送 stop/deactivate；新页面不会自动启动，必须点击“启动功能”后才开始发送对应请求。
