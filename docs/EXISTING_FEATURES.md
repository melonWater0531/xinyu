# reCamera Multimodal 现有功能整合说明

> 本文档梳理当前代码库中已经具备的功能、模块边界和主要运行链路。内容基于 `recamera_fastapi.py`、`main_phase3.py`、`core/`、`vision/`、`audio/`、`hardware/`、`dashboard/` 以及现有 `docs/` 文档整理。

## 1. 系统定位

本项目是一个基于 reCamera 与 ReSpeaker XVF3800 的多模态情绪陪伴与智能跟随系统。系统同时处理视觉、音频、云台控制和 LLM 对话能力，面向两类核心场景：

| 场景 | 目标 | 主要能力 |
|---|---|---|
| 单人学习 / 工作 | 观察用户状态并提供陪伴反馈 | 人脸追踪、情绪识别、专注评分、眼部指标、日记反思、健康建议 |
| 多人会议 / 讨论 | 根据声源方向辅助跟随并生成会议记录 | 声源定位、人体/人数感知、会议录音、语音转写、会议摘要、DOA yaw 跟随 |

整体架构可以理解为三层：

```text
硬件层
  reCamera 摄像头 / 云台
  ReSpeaker XVF3800 麦克风阵列

服务层
  recamera_fastapi.py：视频、感知、音频、Web API、前端页面、状态推送
  main_phase3.py：唯一控制平面，负责 FSM、决策、安全门控、云台硬件调用
  EventBus：连接 FastAPI 事件输入与 main_phase3 控制运行时

前端层
  /control 或 /v2：开发调试控制台
  /home：心屿产品界面
```

## 2. 核心运行边界

当前系统的一个关键设计是“单控制平面”：

- `main_phase3.py` 是唯一真实云台控制进程。
- `RecameraClient.apply_command()` 只应由 `main_phase3.py` 控制链路调用。
- `recamera_fastapi.py` 负责感知、录音、页面和状态展示，不直接控制硬件云台。
- Dashboard 发起的是 UI Event，最终需要经过 EventBus、FSM、Orchestrator 和 SafetyLayer 后才会变成硬件命令。
- 每个控制功能由 `session_id + 2.5s lease` 维护控制权，页面切换、浏览器失联或租约过期都会停止控制。

控制闭环：

```text
Dashboard UI
  -> FastAPI UI Event
  -> EventBus
  -> main_phase3.py
  -> Orchestrator + FSM
  -> SafetyLayer
  -> RecameraClient
  -> Node-RED HTTP bridge
  -> reCamera CAN yaw/pitch motors
```

## 3. 视觉感知功能

| 功能 | 当前实现 |
|---|---|
| reCamera 视频接入 | `SSCMAVideoClient` 连接 `ws://<device>:8090/`，读取 JPEG 帧和 SSCMA 检测数据 |
| MJPEG 推流 | `GET /video_feed` 将最新帧编码为 MJPEG，供前端 `<img>` 直接展示 |
| 检测框解析 | 支持 SSCMA 输出的 `[cx, cy, w, h, conf, cls]` 格式 |
| 人脸追踪 | `vision/face_tracker_v2.py` 使用 SCRFD、Kalman、ByteTrack、ArcFace 做多脸检测与跨帧跟踪 |
| 人体姿态 | `vision/pose_estimator.py` 使用 YOLO11 pose，输出人体框与 17 个 COCO 关键点 |
| 面部关键点 | `vision/mediapipe_face.py` 支持 MediaPipe Face Landmarker 468 点 |
| 人脸裁剪 | `vision/face_crop.py` 为情绪识别提取人脸区域 |
| 视觉连续性 | 使用 `vision_lost_frames` 和 debounce，避免短时遮挡导致控制抖动 |
| Mock 数据 | `vision/mock_data_generator.py` 和 `vision/data_source.py` 提供演示/测试数据源 |

## 4. 情绪、专注与健康指标

| 功能 | 当前实现 |
|---|---|
| 情绪识别 | `vision/emotieff_adapter.py` 封装 EmotiEffLib，输出 8 类情绪、置信度和概率分布 |
| 情绪前端展示 | WebSocket 状态快照包含 `emotieff`，前端显示中文情绪和置信度 |
| 专注评分 | `vision/attention_engine.py` 基于头部姿态、稳定性、个人基线和 EMA 输出 0-100 分 |
| 眼部指标 | `vision/eye_metrics.py` 输出 EAR、PERCLOS、眨眼率等指标 |
| 专注时长 | 前端在检测到有效状态后累计展示本次专注持续时间 |
| 健康建议 | `/api/chat` 和 `/api/reflect` 可结合情绪、专注、日记上下文生成建议或回复 |

## 5. 音频与声源定位

| 功能 | 当前实现 |
|---|---|
| ReSpeaker DOA/VAD | 生产路径使用 ReSpeaker XVF3800 USB control 读取 DOA/VAD，最高约 10Hz |
| TCP DOA 备用输入 | `audio/network_doa.py` 监听 `0.0.0.0:9999`，支持纯角度、JSON、xvf_host 文本 |
| DOA 解析抽象 | `audio/doa.py` 提供 DOA line parser 和可插拔 DOA source |
| speech hold | 有效 DOA 包会刷新语音保持状态，避免短瞬静音造成状态闪烁 |
| DOA 过期保护 | 维护 DOA age，过期数据不会继续驱动控制 |
| ReSpeaker LED | 音频功能可设置 ReSpeaker 12 LED DOA 灯效；TCP 备用模式不控制本机实体 LED |
| 会议录音 | `audio/conversation_recorder.py` 通过 USB Audio 录制 16kHz 单声道音频，并按 VAD 分段 |
| 语音转写 | `audio/transcriber.py` 封装 faster-whisper，可对 WAV 片段做本地转写 |
| 唤醒词 | `audio/wake_word.py` 存在可选唤醒词检测模块，当前不是主链路核心能力 |

## 6. 云台控制与安全

控制进程位于 `main_phase3.py`，核心模块在 `core/` 与 `hardware/`。

| 模块 | 职责 |
|---|---|
| `core/event.py` | 定义 `Event`、`BBox`、`ControlCommand` 等数据结构 |
| `core/fsm.py` | 5 状态 FSM：`IDLE`、`AUDIO_SEARCH`、`VISION_TRACK`、`FUSED_TRACK`、`LOST` |
| `core/orchestrator.py` | 多源融合决策，按视觉、音频、融合策略生成控制命令 |
| `core/control_session.py` | 控制模式和会话租约管理 |
| `core/safety_layer.py` | 对命令做频率、步长、范围、速度等硬安全门控 |
| `core/event_bus.py` | FastAPI 与 main 控制运行时之间的事件和快照通信 |
| `hardware/recamera_client.py` | 唯一云台硬件出口，调用 Node-RED HTTP bridge 的 command、stop、status |

已实现控制能力：

- 单人视觉追踪：检测到人脸/目标后，云台 yaw/pitch 对准画面中心。
- 多人声源 yaw 跟随：ReSpeaker DOA 驱动 yaw 方向搜索或跟随，pitch 不由音频控制。
- 视觉 + 音频融合：同时存在视觉和语音信号时，Orchestrator 进入融合策略。
- 目标丢失处理：使用丢帧计数和 LOST 状态，避免短时丢失立即停止或乱扫。
- 手动云台调试：Dashboard D-Pad/home 通过 session 授权后发出手动事件。
- 安全退出：进程退出时通过 atexit 发送 stop，避免云台持续运动。
- fail closed：Node-RED bridge 不可达或租约失效时，控制链路不继续放行。

## 7. 后端接口能力

主要服务入口是 `recamera_fastapi.py`，默认 FastAPI 端口为 `8001`。

| 接口 | 功能 |
|---|---|
| `GET /` | 重定向或返回产品首页 |
| `GET /home` | 心屿产品界面 |
| `GET /control`、`GET /v2` | 实时调试控制台 |
| `GET /video_feed` | MJPEG 视频流 |
| `WebSocket /ws` | 每约 200ms 推送完整状态快照 |
| `GET /api/state` | 当前状态快照 |
| `GET /api/device/config` | 读取设备地址配置 |
| `POST /api/device/config` | 更新视频/感知设备地址 |
| `GET /api/gimbal/state` | 云台遥测状态 |
| `POST /api/gimbal/move` | 手动相对移动云台 |
| `POST /api/gimbal/home` | 云台回中/home |
| `POST /api/single_track/start`、`/stop` | 单人追踪功能启动/停止 |
| `POST /api/multi_track/start`、`/stop` | 多人声源跟随功能启动/停止 |
| `POST /api/meeting/yaw/start`、`/stop` | 会议场景 yaw 跟随启动/停止 |
| `POST /api/control/manual/start`、`/stop` | 手动控制会话启动/停止 |
| `POST /api/control/heartbeat` | 控制会话租约续期 |
| `GET /api/control/runtime` | 查询 main 控制运行时快照 |
| `POST /api/control/config` | 更新控制相关配置 |
| `GET /api/respeaker/state` | ReSpeaker 状态 |
| `GET /api/conversation/state` | 会议录音状态 |
| `GET /api/conversation/debug` | 会议录音调试状态 |
| `POST /api/conversation/start`、`/stop`、`/save` | 录音会话开始、停止、保存 |
| `POST /api/meeting/summarize` | 会议转写与 LLM 摘要生成 |
| `POST /api/reflect` | 情绪日记反思生成 |
| `POST /api/chat` | LLM 陪伴对话 |
| `GET /api/chat/status` | LLM/DeepSeek 配置状态 |
| `GET /api/snapshot` | 当前帧 JPEG 快照 |
| `GET /api/debug/video` | 视频调试信息 |
| `GET /api/health` | 健康检查 |
| `GET /manifest.webmanifest`、`GET /sw.js` | PWA 资源 |

## 8. WebSocket 状态快照

`/ws` 推送的 `state_snapshot` 聚合了前端所需的大部分运行状态，典型内容包括：

```json
{
  "video": { "connected": true, "fps": 5.0, "width": 640, "height": 480 },
  "pose": { "count": 1, "persons": [] },
  "gimbal": { "connected": true, "yaw": 180.0, "pitch": 90.0 },
  "respeaker": { "connected": true, "doa_deg": 120.0, "has_speech": true },
  "attention": { "has_face": true, "score": 82 },
  "emotieff": { "emotion": "happy", "confidence": 0.91 },
  "eye_metrics": { "ear": 0.28, "perclos": 0.12, "blink_rate": 14 },
  "conversation": { "active": false, "state": "idle" },
  "control": {
    "feature": "single_face_analysis",
    "session_id": "...",
    "lease_remaining_ms": 2500,
    "fsm_state": "VISION_TRACK",
    "authority": "vision",
    "command": {},
    "safety": {}
  },
  "health": {}
}
```

## 9. 前端功能

### 9.1 调试控制台：`dashboard/recamera_v2_live.html`

路由：`/control`、`/v2`

已实现内容：

- 实时视频画面，叠加检测框和视觉辅助信息。
- FSM 状态可视化，显示当前状态和控制权来源。
- 决策链展示：最近事件、命令、安全门控结果。
- 最近 trace 日志，用于回看控制决策。
- 云台遥测：yaw、pitch、速度、延迟、连接状态。
- 感知通道：DOA、语音状态、人体数、视觉丢帧。
- 单人分析：情绪、专注分、眼部指标。
- 系统健康：视频 FPS、WebSocket 客户端、DOA 新鲜度、云台 RTT。
- 功能控制：单人追踪、多人声源跟随、会议 yaw、手动云台等。

### 9.2 产品界面：`dashboard/home.html`

路由：`/home`

已实现内容：

- 手机 App 形态的“心屿”产品界面。
- 首页功能卡：情绪监测、专注记录、多人场景、陪伴对话。
- 日记页：日历、日记内容、LLM 回复和历史记录组织。
- 周报页：7 天情绪/专注趋势。
- 健康页：眼部休息、坐姿提醒、呼吸引导、拉伸、饮水/步数打卡。
- 个人页：用户名和历史概览。
- PWA：`manifest.webmanifest` 与 `sw.js`。

### 9.3 Page 2 预览工程：`dashboard/page2_preview/`

该目录包含独立预览版页面、样式、脚本和情绪插画资源：

- `index.html`
- `app.js`
- `styles.css`
- `assets/moods/*.png`
- `preview-mobile.png`
- `preview-desktop.png`

当前 Git 状态显示该目录有改动和新增资源，整理文档时未修改这些文件。

## 10. LLM 与日记能力

| 功能 | 当前实现 |
|---|---|
| DeepSeek 对话 | `/api/chat` 调用 DeepSeek API，未配置时使用本地 fallback |
| 情绪日记反思 | `/api/reflect` 生成日记条目和陪伴式回复 |
| 会议摘要 | `/api/meeting/summarize` 汇总录音转写内容，生成会议摘要和日记条目 |
| 上下文输入 | 可结合当前情绪、专注状态、日记内容和前端传入消息 |
| 状态检查 | `/api/chat/status` 返回 LLM 配置状态 |

## 11. 设备与端口

| 地址/端口 | 用途 | 说明 |
|---|---|---|
| `RECAMERA_DEVICE_IP:8090` | SSCMA WebSocket | reCamera 视频和检测数据输入 |
| `RECAMERA_DEVICE_IP:1880` | Node-RED HTTP bridge | 云台 command、stop、status |
| `0.0.0.0:9999` | Network DOA TCP | 远端 DOA 备用输入 |
| `0.0.0.0:8001` | FastAPI | Web 页面、REST API、WebSocket |
| `RECAMERA_DEVICE_IP:22` | SSH | 设备维护 |

设备地址来源：

- 环境变量：`RECAMERA_DEVICE_IP`
- FastAPI 参数：`--device-ip`
- 控制进程参数：`--gimbal-ip`
- Dashboard 顶部设备地址输入框：只影响视频/感知重连，不直接创建云台控制客户端

典型启动：

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
python3 main_phase3.py --enable-control --gimbal-ip "$RECAMERA_DEVICE_IP" --manual-control
```

## 12. 依赖概览

`requirements.txt` 当前列出的核心依赖：

| 类别 | 依赖 |
|---|---|
| 基础 | `numpy`、`PyYAML` |
| 视频与硬件 | `websocket-client`、`pyusb` |
| Web 服务 | `fastapi`、`uvicorn[standard]`、`aiohttp>=3.9` |
| 视觉推理 | `opencv-python`、`onnx`、`onnxruntime`、`mediapipe` |
| 音频 | `sounddevice` |
| 可选人脸 | `insightface` |
| 可选转写 | `faster-whisper` |

## 13. 已知状态与注意事项

| 项目 | 状态 |
|---|---|
| 单控制平面 | 已收束到 `main_phase3.py` |
| FastAPI 云台控制权 | 不直接控制硬件，只发事件和展示遥测 |
| Node-RED bridge | 生产安全依赖 `deploy/node_red/recamera_control_bridge.json` |
| `core/control_filter.py` | 遗留孤立模块，当前不在生产链路 |
| `tools/run_orchestrator_mvp.py` | 开发测试工具，不属于生产入口 |
| `tools/build_function_arch_docx.py` | 文档生成工具，可能包含过时引用 |
| TCP DOA | 备用输入，不能控制本机 ReSpeaker 实体 LED |
| `/home` 产品页 | 已有完整 UI；部分数据可由 mock 或后端状态驱动 |
| 真实硬件功能 | 需要 reCamera、ReSpeaker、Node-RED bridge 和对应设备地址在线 |
| LLM 功能 | 需要 DeepSeek API Key；未配置时部分能力降级为本地模板 |

## 14. 功能清单总览

| 模块 | 功能 | 状态 |
|---|---|---|
| 视觉 | reCamera 视频接入 | 已实现 |
| 视觉 | MJPEG 实时推流 | 已实现 |
| 视觉 | SSCMA 检测框解析 | 已实现 |
| 视觉 | 人脸检测与追踪 | 已实现 |
| 视觉 | 人体姿态和人数统计 | 已实现 |
| 视觉 | MediaPipe 面部关键点 | 已实现 |
| 情绪 | EmotiEffLib 8 类情绪识别 | 已实现 |
| 专注 | 头部姿态专注评分 | 已实现 |
| 健康 | EAR、PERCLOS、眨眼率 | 已实现 |
| 音频 | ReSpeaker DOA/VAD | 已实现 |
| 音频 | TCP DOA 备用输入 | 已实现 |
| 音频 | 会议录音分段 | 已实现 |
| 音频 | faster-whisper 转写封装 | 已实现 |
| 控制 | 单 FSM 控制平面 | 已实现 |
| 控制 | 单人视觉追踪 | 已实现 |
| 控制 | 多人声源 yaw 跟随 | 已实现 |
| 控制 | 手动云台调试 | 已实现 |
| 控制 | SafetyLayer 安全门控 | 已实现 |
| 控制 | 会话租约与 heartbeat | 已实现 |
| 后端 | REST API | 已实现 |
| 后端 | WebSocket 状态推送 | 已实现 |
| 前端 | `/control` 调试控制台 | 已实现 |
| 前端 | `/home` 产品界面 | 已实现 |
| 前端 | PWA manifest/service worker | 已实现 |
| LLM | 陪伴对话 | 已实现 |
| LLM | 日记反思 | 已实现 |
| LLM | 会议摘要 | 已实现 |

