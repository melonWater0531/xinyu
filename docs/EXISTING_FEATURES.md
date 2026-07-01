# 心屿功能总览与使用指南

> 基准：`origin/main` / `e607758`，2026-06-30。本文是新旧功能统一的权威清单。这里的“部署”表示代码已接入主链路，不等同于真实设备、浏览器或模型效果已经验收。

## 1. 系统定位与边界

心屿是基于 reCamera、ReSpeaker XVF3800、FastAPI 和浏览器 PWA 的多模态健康陪伴系统，覆盖单人情绪/专注观察、多人声源跟随、会议记录、陪伴交互与云台控制。

系统坚持单控制平面：`main_phase3.py` 是唯一真实云台控制进程；`recamera_fastapi.py` 只负责感知、录音、状态聚合、页面和 UI Event，不直接调用 `RecameraClient.apply_command()`。手势、注视和主动情绪干预只产生状态或前端动作，不进入云台控制链。

```text
reCamera / ReSpeaker
  -> FastAPI 感知循环
     -> face / pose / emotion / eye / gaze / gesture
     -> EmotionInterventionPolicy
     -> /api/state + /ws
        -> /home 陪伴交互与 PWA 本地提醒

Dashboard UI Event
  -> EventBus
  -> main_phase3.py
  -> FSM + Orchestrator + SafetyLayer
  -> RecameraClient
  -> Node-RED bridge
  -> reCamera yaw / pitch
```

## 2. 部署状态定义

| 状态 | 含义 |
|---|---|
| 已部署 | 代码已接入当前主链路；是否通过真实环境验收另行记录 |
| 已部署（资源待补） | 主链路和降级逻辑已接入，但缺模型、密钥或外部资源 |
| 部分部署 | 已有模块、页面或接口，但尚未形成完整主链路 |
| 未部署 | 当前仓库没有可用主链路实现 |
| 未部署（暂不计划） | 已明确不在当前阶段范围内 |

## 3. 当前总体架构

| 层级 | 主要组件 | 职责 |
|---|---|---|
| 硬件 | reCamera、云台、ReSpeaker XVF3800 | 视频、检测数据、DOA/VAD、音频和机械运动 |
| 感知 | `vision/`、`audio/` | 人脸/人体、情绪、眼部、注视、手势、录音和转写 |
| 策略 | `EmotionInterventionPolicy`、`AttentionEngine` | 滑动窗口干预、注视辅助融合、健康状态判断 |
| 控制 | EventBus、FSM、Orchestrator、SafetyLayer | 控制权、决策、租约、限幅和故障关闭 |
| 服务 | `recamera_fastapi.py`、`main_phase3.py` | Web/API/状态推送与唯一硬件控制运行时 |
| 产品 | `/home`、`/control`、PWA | 陪伴界面、调试台、本地提醒和离线壳 |

## 4. 已部署功能

### 4.1 视觉感知

| 功能 | 实现与入口 | 状态 | 使用要点 |
|---|---|---|---|
| reCamera 视频接入 | `SSCMAVideoClient` 连接 `ws://<IP>:8090/` | 已部署 | 配置设备 IP 后查看 `/video_feed` |
| MJPEG 推流 | `GET /video_feed` | 已部署 | 浏览器 `<img>` 可直接显示 |
| SSCMA 检测框解析 | `[cx, cy, w, h, conf, cls]` | 已部署 | 用于目标候选和叠层 |
| 人脸检测与跨帧跟踪 | `vision/face_tracker_v2.py` | 已部署 | SCRFD/跟踪依赖缺失时可降级 |
| 人体姿态与人数 | `vision/pose_estimator.py`，YOLO11 pose | 已部署 | 输出人体框与 COCO-17 关键点 |
| 面部关键点 | `vision/mediapipe_face.py` | 已部署 | 眼部、注视和面部指标的输入 |
| 情绪识别 | `vision/emotieff_adapter.py` | 已部署 | 8 类情绪、置信度、概率分布 |
| 眼部指标 | `vision/eye_metrics.py` | 已部署 | EAR、PERCLOS、眨眼率、眼部专注分 |
| 专注评分 | `vision/attention_engine.py` | 已部署 | 头姿 40%、眼部 30%、稳定性 15%、注视 15% |
| 注视方向估计 | `vision/gaze_estimator.py` | 已部署 | 仅为“视线趋势”，不是精确眼动追踪 |
| 手势识别 A 版 | `vision/gesture_detector.py` | 已部署（资源待补） | 缺 `models/gesture_recognizer.task` 时返回 `model_missing` |
| 视频/跟踪叠层 | `/control`、`tracking_overlay.js` | 已部署 | 展示框、状态、控制遥测和决策轨迹 |
| 当前帧快照 | `GET /api/snapshot` | 已部署 | 手动获取 JPEG；事件自动截图仍未完成 |

### 4.2 情绪、健康与陪伴

| 功能 | 实现与入口 | 状态 | 使用要点 |
|---|---|---|---|
| 主动情绪干预 | `core/emotion_intervention.py` | 已部署 | 负面情绪窗口或疲劳/低专注组合触发，30 分钟冷却 |
| 情绪日记 | `/home`、`POST /api/reflect` | 已部署 | 本地保存日记，LLM 可生成温和回应 |
| 专注记录 | `/home`、`attention` | 已部署 | 开启专注记录后累计时长和趋势 |
| 周趋势 | `/home` 趋势页 | 已部署 | 汇总本地情绪与专注记录 |
| 护眼计时 | `/home` 健康页 | 已部署 | 手动开启 20 分钟循环 |
| 久坐计时 | `/home` 健康页 | 已部署 | 手动开启 45 分钟循环 |
| 饮水与步数记录 | `/home` 健康页 | 已部署 | 数据保存在 localStorage |
| 呼吸与拉伸 | `/home` 健康页 | 已部署 | 4-7-8 呼吸和快速拉伸引导 |
| 健康建议 | `/api/chat` + 本地模板 | 已部署 | DeepSeek 未配置时降级为本地建议 |

### 4.3 音频与会议

| 功能 | 实现与入口 | 状态 | 使用要点 |
|---|---|---|---|
| ReSpeaker DOA/VAD | `audio/respeaker_doa.py` | 已部署 | USB 控制读取声源角度与语音状态 |
| TCP DOA 备用输入 | `audio/network_doa.py`，端口 9999 | 已部署 | 无 USB 时可注入角度/JSON；不控制实体 LED |
| ReSpeaker LED | ReSpeaker 适配器 | 已部署 | 多人/会议功能期间显示 DOA 灯效 |
| 会议录音与 VAD 分段 | `audio/conversation_recorder.py` | 已部署 | 16 kHz 单声道；音频设备需现场确认 |
| 本地语音转写 | `audio/transcriber.py` | 已部署（资源待补） | 需额外安装 `faster-whisper` 并下载模型 |
| 会议摘要/写入日记 | `POST /api/meeting/summarize` | 已部署 | 依赖转写结果和可选 DeepSeek |
| 唤醒词模块 | `audio/wake_word.py` | 部分部署 | 文件存在，未接入常驻主链路 |

### 4.4 控制与安全

| 功能 | 实现与入口 | 状态 | 使用要点 |
|---|---|---|---|
| 单控制平面 | `main_phase3.py` | 已部署 | 唯一允许调用硬件控制客户端的进程 |
| 五态 FSM | `core/fsm.py` | 已部署 | IDLE、AUDIO_SEARCH、VISION_TRACK、FUSED_TRACK、LOST |
| 单人视觉跟踪 | `/api/single_track/start` | 已部署 | 视觉目标驱动 yaw/pitch |
| 多人声源 yaw 跟随 | `/api/multi_track/start` | 已部署 | DOA 只驱动 yaw，避免音频误控 pitch |
| 会议 yaw 跟随 | `/api/meeting/yaw/start` | 已部署 | 会议场景独立功能租约 |
| 手动云台调试 | `/control` | 已部署 | 需有效 session 和 heartbeat |
| 控制会话与 2.5 秒租约 | `core/control_session.py` | 已部署 | 页面离开/失联/租约过期会停机 |
| SafetyLayer | `core/safety_layer.py` | 已部署 | 限频、限步长、限范围、速度门控 |
| EventBus | `core/event_bus.py`，端口 8765 | 已部署 | FastAPI 与控制进程之间传递事件/快照 |
| Node-RED bridge | `deploy/node_red/recamera_control_bridge.json` | 已部署 | 设备侧部署后提供 command/stop/status |
| 安全退出与 fail closed | 主控制运行时 | 已部署 | 退出、桥不可达或租约失效时发送 stop |

### 4.5 服务、前端与 LLM

| 功能 | 入口 | 状态 | 使用要点 |
|---|---|---|---|
| REST API 与健康检查 | `/api/*`、`/api/health` | 已部署 | FastAPI 默认端口 8001 |
| WebSocket 状态推送 | `/ws` | 已部署 | 约每 200 ms 推送状态快照 |
| 当前状态查询 | `/api/state` | 已部署 | 与 `/ws` 使用同一状态结构 |
| 设备地址配置 | `/api/device/config` | 已部署 | 只重连视频/感知，不创建硬件控制客户端 |
| 产品首页 | `/home` | 已部署 | 情绪、专注、日记、趋势、健康和手势陪伴 |
| 调试控制台 | `/control`、`/v2` | 已部署 | 面向开发与硬件联调 |
| Page 2 预览 | `dashboard/page2_preview/` | 部分部署 | 独立静态预览，尚未替换 `/home` 主页面 |
| PWA 壳与缓存 | manifest + `sw.js` | 已部署 | HTTPS 或 localhost 下注册 Service Worker |
| DeepSeek 陪伴对话 | `POST /api/chat` | 已部署（资源待补） | 需 `DEEPSEEK_API_KEY`；否则本地 fallback |
| LLM 日记反思 | `POST /api/reflect` | 已部署（资源待补） | 未配置 LLM 时保留本地日记能力 |

## 5. 四项健康陪伴新功能

### 5.1 #4 主动情绪干预

数据流：`emotieff + attention + eye_metrics + gaze -> EmotionInterventionPolicy -> proactive_intervention -> /home toast/PWA`。

默认策略：维护 180 秒滑动窗口；至少收集 20 个样本；高置信样本比例足够后，负面情绪样本比例不低于 0.6 且平均置信度不低于 0.65时触发。负面范围为 Sadness、Anger、Fear、Disgust、Contempt。另一条疲劳路径为平均专注低于 45，且 PERCLOS 或视线向下比例达到阈值。触发后 1800 秒冷却。

状态结构：

```json
{"active":false,"type":"","reason":"collecting","message":"","cooldown_remaining_sec":0}
```

使用：打开 `/home` 并保持情绪/专注数据输入。触发时页面显示陪伴式提示；开启本地通知后可发送“情绪关心”。无人脸、置信度不足或样本不足时不触发，不进行心理诊断。

### 5.2 #8 手势识别陪伴交互 A 版

识别框架是 MediaPipe Gesture Recognizer，不使用 YOLO-pose 手势规则。默认置信度阈值 0.6、连续稳定 4 帧、同 intent 冷却 3 秒；每 3 个感知循环执行一次。识别器只返回状态，绝不发送 `gimbal_*`、`feature_*` 或 `dpad_*` 事件。

| 手势 | intent | 当前动作 |
|---|---|---|
| Open Palm / 张手 | `summon_xinyu` | 显示“我在听”并在聊天区加入陪伴回应 |
| Closed Fist / 握拳 | `pause_or_mute` | 收起当前提醒；当前版本没有 TTS 可静音 |
| Thumb Up / 点赞 | `feedback_positive` | 在 localStorage 记录“有帮助”反馈 |
| Thumb Down / 点踩 | `feedback_negative` | 在 localStorage 记录“无帮助”反馈 |
| Victory / 剪刀手 | `capture_positive_moment` | 生成积极瞬间日记草稿，等待用户确认 |

部署资源：从 MediaPipe 官方模型页下载 Gesture Recognizer task 文件，放到 `models/gesture_recognizer.task`，重启 FastAPI。模型缺失时 `gesture.available=false` 且 `reason=model_missing:models/gesture_recognizer.task`，其他功能继续运行。

参考：[MediaPipe Gesture Recognizer for Python](https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer/python)。

### 5.3 #9 注视方向估计

`GazeEstimator` 使用 MediaPipe Face Landmarker 的虹膜点（需要至少 477 个点）计算虹膜中心相对眼角中心的粗略偏移，输出 `center`、`left`、`right`、`down`、`away`、`unknown`。它以 15% 权重作为 attention 的辅助证据，不替代头姿、眼部或稳定性评分。

```json
{"available":true,"state":"center","x_offset":0.02,"y_offset":0.05,"confidence":0.96}
```

使用：在 `/home` 的“手势陪伴”卡片查看“视线趋势”，或请求 `/api/state` 检查 `gaze`。无人脸、关键点不足或推理异常时返回 `available=false`，原 attention 仍可运行。

参考：[MediaPipe Face Landmarker for Python](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python)。

### 5.4 #10 PWA 本地提醒

通知调度位于 `/home` 前端，使用 Notification API、Service Worker 和 localStorage，不新增后端推送服务，也不接入 ntfy、Telegram 或 MQTT。

| 类型 | 触发规则 | 页面 |
|---|---|---|
| 护眼 | 用户开启 20-20-20 计时，20 分钟到期 | 健康页 |
| 久坐 | 用户开启久坐计时，45 分钟到期 | 健康页 |
| 喝水 | 09:00-22:00，90 分钟未记录且未达 8 杯目标 | 健康页 |
| 疲劳 | PERCLOS/眨眼/视线向下任一异常且专注低于 60，持续 5 分钟 | 健康页 |
| 低专注 | 专注记录开启且 score < 50，持续 10 分钟 | 健康页 |
| 情绪关心 | `proactive_intervention.active=true` | 首页 |

默认同类冷却 30 分钟，情绪关心 60 分钟，安静时段 22:30-08:30。通知不包含截图、日记正文、会议原文或敏感内容。权限未授权或浏览器不支持时降级为站内 toast。

使用：通过 HTTPS 或 localhost 打开 `/home`，进入“建议/健康”页，点击“开启提醒”并授予浏览器权限，再用“测试提醒”验证。通知点击后由 `sw.js` 打开或聚焦 `/home#health` 或 `/home#home`。

参考：[MDN Notifications API](https://developer.mozilla.org/docs/Web/API/Notifications_API)、[MDN Service Worker API](https://developer.mozilla.org/docs/Web/API/Service_Worker_API)。

## 6. 状态接口

`GET /api/state` 和 `WebSocket /ws` 均包含：

```json
{
  "gaze": {"available": false, "state": "unknown", "x_offset": 0, "y_offset": 0, "confidence": 0},
  "gesture": {"available": false, "name": "", "confidence": 0, "handedness": "", "stable_frames": 0, "intent": "", "intent_ready": false, "updated_at": 0, "reason": ""},
  "proactive_intervention": {"active": false, "type": "", "reason": "", "message": "", "cooldown_remaining_sec": 0}
}
```

前端本地状态包括 `xinyu_notify_enabled`、`xinyu_notify_quiet_hours`、`xinyu_notify_cooldowns`、`xinyu_water_last_at`、`xinyu_water_goal`、`xinyu_notify_last_sent_by_type`、`xinyu_gesture_feedback`。

## 7. 未完成与暂不计划功能

| 功能 | 状态 | 当前决定 |
|---|---|---|
| TTS 语音输出 | 未部署 | 后续评估，不纳入本轮 |
| 说话人分离与声纹-人脸绑定 | 未部署 | 难度和隐私成本高，暂缓 |
| 跌倒/姿态异常检测 | 未部署（暂不计划） | 当前产品不走安防/告警路线 |
| VLM 场景理解 | 未部署 | 可做手动实验，不接实时控制 |
| 自定义唤醒词与常驻助手 | 部分部署 | 仅有基础模块，主链路暂缓 |
| 声音事件检测 | 未部署（暂不计划） | 当前不做异响/安防提醒 |
| 云台手势控制 | 未部署（暂不计划） | A 版明确只做陪伴交互 |
| ntfy / Telegram 推送 | 未部署（暂不计划） | 第一版只做 PWA 本地通知 |
| MQTT / Home Assistant | 未部署（暂不计划） | 当前系统计划不包含 MQTT |
| 停留时长专注指标 | 未部署 | 可作为后续低中难度增强 |
| 事件自动截图 | 部分部署 | 有 `/api/snapshot`，未接自动事件策略 |
| 跨帧人数统计 | 部分部署 | 有当前帧人数，未做专门滑动窗口统计 |
| 噪声抑制 | 未部署 | 可提升 VAD/ASR，尚未接入 |
| 事件录像、RTSP、热力图、区域入侵 | 未部署（暂不计划） | 与健康陪伴定位不符 |

## 8. 启动与使用

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
# 可选：export DEEPSEEK_API_KEY=<KEY>
python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
python3 main_phase3.py --enable-control --gimbal-ip "$RECAMERA_DEVICE_IP" --manual-control
```

访问：`http://<HOST>:8001/home` 使用产品功能，`http://<HOST>:8001/control` 联调控制，`http://<HOST>:8001/api/state` 检查感知状态。PWA 通知在非 localhost 访问时需要 HTTPS。

## 9. 已知限制

- 手势模型文件当前不在仓库，手势识别尚不能进行真实效果验收。
- 注视估计受分辨率、眼镜、眯眼、遮挡和光照影响，只能作为趋势。
- 主动情绪干预是陪伴策略，不是心理或医疗诊断。
- PWA 通知由打开的 `/home` 页面调度；第一版不是服务器离线推送。
- DeepSeek、faster-whisper、真实 reCamera、ReSpeaker 和 Node-RED 均需要各自外部资源或现场环境。
