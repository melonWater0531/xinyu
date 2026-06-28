# 心屿 Xinyu — 多模态情绪陪伴系统：已实现功能概览

> **系统定位：** 基于 reCamera 智能摄像头的多模态情绪陪伴系统。通过实时视觉感知与音频处理，持续观察用户的情绪状态与专注程度；结合大语言模型（LLM），为用户提供情绪日记、健康洞察与陪伴对话。支持单人日常学习/工作场景，以及多人会议场景下的声源定向与会议记录自动生成。

---

## 一、系统整体架构

系统围绕两种使用场景展开，共用同一套摄像头感知基础设施，通过前端 Feature Toggle 在场景间切换。

---

### 场景 A：日常场景（单人学习 / 工作）

**使用场景：** 用户独自学习或工作时，系统持续感知其面部状态，判断情绪与专注程度，结合 LLM 生成情绪日记和健康建议，并支持用户随时发起陪伴对话。

```
硬件输入
  reCamera 摄像头（Wi-Fi 接入，MJPEG 流）
      │
      ▼
视觉感知链路
  SSCMA 视频解码（JPEG + YOLO 检测框，约 5Hz）
      → 人脸追踪（SCRFD 检测 + ByteTrack 时序追踪）
      → 专注度评估（头部姿态估计，0–100 分）
      → 情绪识别（8 类情绪分类 + 置信度 + 效价）
      → 眼部指标（眨眼率 / EAR / PERCLOS）
      │
      ▼
用户交互链路（文字输入）
  用户发送文字 → 后端调用 DeepSeek LLM
      → 生成情绪陪伴回复 / 情绪日记条目 / 健康建议
      │
      ▼
前端展示（心屿 App 首页）
  情绪监测卡 · 专注记录卡 · 情绪日记 · 周趋势图 · LLM 陪伴对话
```

---

### 场景 B：工作场景（多人会议 / 讨论）

**使用场景：** 多人开会时，系统同时感知房间内的人数与声音来源方向，驱动摄像头自动转向说话人，并实时将对话分段录制、转写，会后由 LLM 整理为会议摘要，自动写入用户的情绪日记系统。

```
硬件输入
  reCamera 摄像头（人体检测）
  ReSpeaker XVF3800 麦克风阵列（声源定向 + 语音采集）
      │
      ▼
视觉感知链路
  SSCMA 视频 → YOLO11n-pose 人体检测
      → 17个身体关键点（含手腕/肩膀/肘部）
      → 实时人数统计
      │
音频处理链路
  麦克风阵列采集（16kHz / 单声道）
      → 声源到达方向（DOA，0–359°，约每包更新一次）
      → 语音活动检测（VAD，自适应噪声底阈值）
      → 发言片段分割（每段含 DOA 方向标注）
      → Whisper 语音识别（本地推理，faster-whisper tiny）
      → DeepSeek LLM 整理 → 会议摘要 + 日记条目
      │
      ▼
云台自动跟随
  DOA 方向角 → FSM 决策引擎 → 云台转向说话人方向
      │
      ▼
前端展示（心屿 App 多人场景卡）
  当前说话方向 · 人数统计 · 声源状态 · 会议记录写入
```

---

### 共用基础设施

```
┌──────────────────────────────────────────────────────────────┐
│               后端感知聚合层（FastAPI，约 5Hz）               │
│                                                              │
│  摄像头帧 ──→ 人脸/人体/情绪/专注/眼部指标推理               │
│  麦克风阵列 ──→ DOA 方向 + 语音状态更新                      │
│                        │                                    │
│                 状态快照打包                                  │
│                        │                                    │
│            WebSocket 推送至前端（每帧）                      │
│            REST API 按需查询                                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、已实现功能详解

### 2.1 视觉感知

| 功能 | 说明 |
|------|------|
| **实时视频接入** | 通过 Wi-Fi 接收 reCamera 摄像头的 MJPEG 视频流，解析 SSCMA 推理得到的 YOLO 检测框（人体位置 + 置信度）。前端同步显示含标注框的实时画面。 |
| **人脸追踪（FaceTrackerV2）** | 使用 SCRFD 模型进行高精度人脸检测，结合 Kalman 滤波器与 ByteTrack 算法实现跨帧时序追踪，支持多脸场景下的主目标保持，并使用 ArcFace 特征区分不同人脸。 |
| **人体姿态估计（YOLO11n-pose）** | 在多人场景下，为每个检测到的人体估计 17 个身体关键点（COCO 格式，含头部、肩膀、手肘、手腕等）。关键点用于人数统计和手势相关分析的底层数据支持。 |
| **精细面部网格（MediaPipe）** | 使用 MediaPipe Face Landmarker 推理 468 个面部精细关键点，用于更准确的头部姿态估计和眼部区域定位，提升专注度与眼部指标的计算精度。 |
| **视觉连续性保护** | 实现视觉丢帧计数机制（`vision_lost_frames`），通过 Debounce（默认连续 30 帧才判定目标丢失）防止因短暂遮挡或光线变化导致追踪中断，提升系统稳定性。 |
| **实时视频推流** | 后端将摄像头画面编码为 MJPEG 格式，通过 `/video_feed` 接口向前端推流，并在画面上叠加检测框、人脸中心点等可视化信息。 |

---

### 2.2 情绪与专注分析

| 功能 | 说明 |
|------|------|
| **情绪识别（EmotiEffLib）** | 使用 EmotiEffLib 模型对人脸区域进行情绪分类，输出 8 类情绪（快乐 / 悲伤 / 愤怒 / 恐惧 / 惊讶 / 厌恶 / 轻蔑 / 平静）及各类别概率分布，同时输出情绪效价（Valence，表示情绪正负程度）。前端展示中文情绪名称与置信度。 |
| **专注度评分（AttentionEngine）** | 通过 3D 头部姿态估计（solvePnP 算法）计算头部在三维空间中的偏转角，与个人基线进行比较，输出 0–100 的专注分。系统会在用户使用过程中自适应更新个人基线，避免因个体差异导致评分偏差。使用 EMA（指数移动平均）平滑，减少抖动。 |
| **眼部状态指标** | 计算三项眼部量化指标：① EAR（Eye Aspect Ratio，眼睛开合度）；② 眨眼率（每分钟眨眼次数）；③ PERCLOS（单位时间内眼睛闭合比例，常用于疲劳检测）。当眨眼率异常或 PERCLOS 偏高时，提示用户注意休息。 |
| **专注时长计时** | 检测到人脸在画面中持续存在时，系统开始累计专注时长计时，前端实时显示本次专注持续时间。结合情绪识别结果，可判断用户是否处于高效投入状态。 |

---

### 2.3 音频与声源定位

| 功能 | 说明 |
|------|------|
| **声源到达方向（DOA）接收** | 生产模式通过 USB control 直接读取 ReSpeaker XVF3800 四麦阵列计算的 DOA/VAD（最高 10 Hz）；TCP 9999 仅作远端备用输入，备用模式不能控制本机实体 LED。 |
| **语音活动检测（VAD）** | 基于 RMS 自适应噪声底的语音活动检测。系统实时估计环境噪声底，当音量超过动态阈值时判定为有声，并结合 DOA 的 `has_speech` 信号进行双重验证，提高在噪声环境下的语音判断精度。 |
| **发言片段录制与分割** | 检测到语音活动时，系统自动开始录制当前片段（16kHz 单声道 WAV）。在持续静音一定时间后结束本片段，并为每段发言标注其 DOA 方向区域（左侧 / 正前方 / 右侧 / 后方），方便后续区分不同说话人。 |
| **语音识别（Whisper）** | 使用 faster-whisper-tiny 模型在本地对录制的 WAV 片段进行中文语音识别，输出转写文本。tiny 模型在 CPU 上对 10 秒音频的处理时间约为 2 秒，满足会议场景对准实时性的需求。 |
| **会议摘要生成** | 将本次会议所有发言片段（含方向标注 + 转写文本）发送给 DeepSeek LLM，由 LLM 整理为格式化的会议摘要（含主要讨论点 + 行动项），并自动写入当日情绪日记，与个人日记系统打通。 |

---

### 2.4 云台自动追踪控制系统

| 功能 | 说明 |
|------|------|
| **有限状态机（FSM）控制** | 系统核心为一个 5 状态有限状态机（空闲 IDLE / 音频搜索 AUDIO_SEARCH / 视觉追踪 VISION_TRACK / 融合追踪 FUSED_TRACK / 目标丢失 LOST），通过事件（视觉目标出现 / 消失 / 语音激活 / 超时）驱动状态转移，并内置 Debounce 防抖机制（如连续 30 帧才判定目标丢失）。 |
| **多源融合追踪策略** | 根据当前信号质量，系统自动选择最优追踪策略：①仅视觉信号时，使用比例控制精准追踪人脸中心；②仅有声音信号时，云台转向 DOA 方向搜索目标；③视觉与音频信号同时有效时，使用 85% 视觉 + 15% 音频的融合策略，在保持精度的同时减少抖动。 |
| **三级渐进搜索** | 当目标不在视野中时，系统按三个层级依次尝试找回目标：第一级，如有人体但无人脸，云台上下俯仰搜索面部；第二级，若无人体，云台左右横扫 ±50° 进行全视野扫描；第三级，超时后回到空闲状态等待。 |
| **安全约束层（SafetyLayer）** | 每条云台指令在执行前经过安全层过滤：限制最大移动步长（单次 ≤ 2.5°）、最大速度、加速度上限，以及最高指令频率（5Hz）。避免云台因异常指令造成机械损伤或剧烈抖动。 |
| **紧急停止与安全退出** | 进程退出（正常关闭或异常终止）时，系统自动发送停止指令，确保云台静止在安全位置，不会因软件崩溃导致云台持续运动。 |

---

### 2.5 后端服务层

后端基于 Python FastAPI 构建，提供以下接口：

| 接口 | 功能说明 |
|------|----------|
| `GET /video_feed` | 推送含检测框叠加层的 MJPEG 实时视频流，供前端 `<img>` 标签直接展示。 |
| `WebSocket /ws` | 每 200ms 向已连接的前端客户端推送完整状态快照，内容涵盖情绪/专注/DOA/云台遥测/控制决策链等所有感知结果，无需前端主动轮询。 |
| `GET /api/state` | REST 按需查询当前系统状态，与 WebSocket 推送内容相同，供不支持 WebSocket 的客户端或调试工具使用。 |
| `POST /api/chat` | 接收用户输入文本，结合当前情绪状态、专注数据及历史日记上下文，调用 DeepSeek LLM 返回温暖风格的陪伴回复；未配置 DeepSeek 时自动降级为本地模板引擎。 |
| `POST /api/reflect` | 接收用户日记内容（可含专注时长、情绪数据），由 LLM 生成格式化日记条目与个性化回复，结果可直接写入当日情绪日记。 |
| `POST /api/meeting/summarize` | 触发对本次会议录音的转写与摘要生成流程，返回完整转写文本、会议摘要和日记条目。 |
| `POST /api/conversation/{start,stop}` | 控制会议录音的开始与停止；开始时初始化录音会话，停止时完成当前片段并整理时间轴。 |
| `POST /api/gimbal/move`、`/api/gimbal/home` | 在调试模式下，支持通过前端 D-Pad 手动控制云台进行相对位移或回中操作，用于设备调试与演示。 |

---

### 2.6 前端界面

系统提供两套前端界面，分别面向不同使用者。

#### 调试控制台（`/v2`）

面向开发者与演示操作者，实时展示系统内部状态：

- **视频主画面：** 含检测框叠加层的实时 MJPEG 视频流
- **FSM 状态可视化：** 5 个状态节点动态高亮当前状态，实时显示控制权归属（视觉 / 音频 / 融合 / 空闲）
- **决策链展示：** 显示最近一次触发事件、生成的云台指令、安全层审核结果，以及最近 12 条完整决策记录
- **云台遥测：** 实时显示云台当前 Yaw / Pitch 角度、速度（来自硬件读回，非软件状态镜像）
- **感知通道状态：** DOA 方向角、实时人体/人脸数量、是否检测到语音、视觉连续性状态
- **单人分析数据：** 实时专注评分、情绪类别 + 置信度、眨眼率 + PERCLOS
- **系统健康指标：** 视频帧率、DOA 数据新鲜度、WebSocket 客户端数、云台通信延迟
- **功能控制面板：** 追踪模式切换（单人 / 多人 DOA）、麦克风录音控制、云台手动 D-Pad 控制

#### 用户产品界面（`/home`，心屿 App）

采用手机 App 竖屏布局（最大宽度 430px），底部 Tab 导航，共五个页面：

**首页（今日状态 + 功能卡）**
- 情绪监测卡：显示当前情绪类别（中文）、置信度，一键开始/暂停监测，实时更新
- 专注记录卡：显示当前专注评分（0–100）、本次专注时长，一键开始/停止记录
- 多人场景卡：显示当前声源方向（°）、人数、DOA 连接状态，一键开启多人跟随；可触发会议记录整理
- LLM 陪伴对话：发送任意问题，LLM 结合今日情绪与日记上下文回复；支持一键生成今日总结与健康建议

**日记页**
- 月历视图：按日期展示情绪打点，点击可查看当日记录
- 日记编辑器：支持手动写入与 LLM 智能补全，情绪日记与会议记录分开存储
- 历史对话：可基于特定日期的日记与 LLM 继续追问

**周报页**
- 7 天情绪与专注趋势柱状图
- 平均专注评分、情绪分布，支持一键生成文字周报

**健康页**
- 眼部休息提醒（20-20-20 法则计时器）
- 坐姿提醒计时器（45 分钟提醒一次）
- 4-7-8 呼吸引导动画
- 拉伸引导（快速 3 分钟拉伸步骤说明）
- 步数与饮水记录（轻量健康打卡）

**个人页**
- 用户名设置
- 历史数据概览

---

### 2.7 产品级已实现功能（端到端流程）

以下功能均已完成从传感器采集到前端呈现的完整链路：

| 功能 | 用户使用流程 |
|------|-------------|
| **实时情绪监测** | 用户打开情绪监测 → 摄像头识别人脸 → 每帧输出情绪 + 置信度 → 前端动态显示，并每日写入情绪日历 |
| **专注度追踪** | 用户开始专注记录 → 系统实时计算头部姿态 → 输出 0–100 专注分，累计专注时长 → 前端图表更新 |
| **声源定向显示** | 多人场景中，麦克风阵列自动计算说话人方向 → 前端实时显示角度 + 声音状态（有声 / 无声） |
| **摄像头自动追踪** | 系统检测到人脸/人体 → FSM 驱动云台自动对准目标 → 目标丢失时进行搜索扫描 |
| **会议记录生成** | 开启多人场景 → 自动录音并标注说话方向 → 点击"整理会议记录" → Whisper 转写 + LLM 摘要 → 写入当日日记 |
| **情绪日记与 LLM 回复** | 用户填写今日日记 → LLM 结合情绪数据生成温暖回应 → 日记条目保存 → 后续对话保持日记上下文 |
| **7 天情绪周趋势** | 系统每日自动记录情绪与专注数据至本地 → 周报页自动生成 7 天趋势图 + 平均分 |
| **健康建议生成** | 用户点击"生成建议" → LLM 读取今日情绪、专注时长、眼部数据 → 输出个性化健康提示 |

---

## 三、技术栈一览

| 类别 | 技术 / 组件 |
|------|-------------|
| **硬件** | reCamera（SSCMA, Wi-Fi, PTZ 云台）/ ReSpeaker XVF3800 麦克风阵列 |
| **视觉模型** | SCRFD（人脸检测）/ ByteTrack（目标追踪）/ ArcFace（特征提取）/ YOLO11n-pose（人体关键点）/ MediaPipe Face Landmarker（精细网格）/ EmotiEffLib（情绪分类）|
| **音频处理** | ReSpeaker USB control（DOA/VAD/LED）/ USB Audio + sounddevice（16kHz 录音）/ NetworkDOA（TCP 备用）/ faster-whisper-tiny（本地语音识别）|
| **计算机视觉** | OpenCV（图像处理）/ NumPy（数值计算）/ 自实现 EAR/PERCLOS 算法 |
| **后端框架** | Python 3.10+ / FastAPI / WebSocket / asyncio |
| **AI 对话** | DeepSeek API（云端 LLM）/ 本地模板引擎（离线降级）|
| **前端** | 原生 HTML / CSS / JavaScript / PWA（Service Worker）|

---

## 四、系统数据流总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                          硬件层                                      │
│   reCamera 摄像头               ReSpeaker XVF3800 麦克风阵列         │
│   SSCMA 视频 ws://device:8090/  ReSpeaker USB DOA/Audio/WS2812 LED  │
└───────────────────┬─────────────────────────┬───────────────────────┘
                    │                         │
                    ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        感知计算层（FastAPI 内部，5Hz）                │
│                                                                     │
│   JPEG 解码 → 人脸追踪 → 情绪识别 → 专注评分 → 眼部指标              │
│              → 人体关键点估计 → 人数统计                             │
│   DOA 接收 → 方向角解析 → 语音状态判断                               │
│   录音会话 → VAD 分段 → WAV 文件 → Whisper 转写                      │
│                                                                     │
│         状态快照打包（emotion / attention / doa / pose / ...）        │
└───────────────────┬─────────────────────────────────────────────────┘
                    │
          ┌─────────┴───────────┐
          │                     │
          ▼                     ▼
   WebSocket /ws          REST /api/*
   200ms 推送             按需查询
          │                     │
          ▼                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          前端层                                      │
│                                                                     │
│   调试控制台 /v2                    用户产品界面 /home（心屿 App）     │
│   FSM 状态 · 决策链 · 遥测          情绪日记 · 专注记录 · LLM 对话   │
│   视频画面 · 功能控制               多人场景 · 周趋势 · 健康建议      │
└─────────────────────────────────────────────────────────────────────┘
                    │
          LLM 对话 / 日记 / 摘要
                    │
                    ▼
         DeepSeek API（云端 LLM）
         生成：情绪日记回应 / 健康建议 / 会议摘要
```

---

## 五、功能接入状态说明

| 功能模块 | 数据来源 | 接入状态 |
|---------|---------|---------|
| 实时情绪识别 | EmotiEffLib 模型实时推理 | ✅ 真实后端推理，WebSocket 实时推送 |
| 专注评分 | solvePnP 头部姿态 + EMA | ✅ 真实后端推理，WebSocket 实时推送 |
| 眼部指标（EAR/PERCLOS） | MediaPipe + 自实现算法 | ✅ 真实后端推理，WebSocket 实时推送 |
| 声源方向（DOA） | ReSpeaker XVF3800 + 解析算法 | ✅ 真实硬件数据，WebSocket 实时推送 |
| 人数统计 | YOLO11n-pose 推理 | ✅ 真实后端推理，WebSocket 实时推送 |
| 摄像头云台自动追踪 | FSM + RecameraClient | ✅ 真实硬件控制（需连接设备）|
| 情绪日记 / 日记回复 | DeepSeek LLM | ✅ 真实 LLM 调用（需配置 API Key，离线可降级）|
| 会议摘要生成 | Whisper + DeepSeek LLM | ✅ 本地转写 + 云端摘要（需连接 API）|
| 周趋势图 | localStorage 本地存储 | ✅ 每日自动写入，前端读取渲染 |
| 健康建议 | DeepSeek LLM + 情绪/专注数据 | ✅ 真实 LLM 生成（离线可降级）|
| LLM 陪伴对话 | DeepSeek LLM | ✅ 真实 LLM，支持上下文携带日记内容 |
| 视频实时推流 | reCamera MJPEG | ✅ 真实视频流（需设备在线）|

---

> 文档生成时间：2026-06-26
> 对应代码版本：v4.3（已实现：情绪监测 / 专注记录 / DOA 显示 / 周趋势 / LLM 对话 / 会议记录写入）

---

## 2026-06-28 Hardware Closure Update

Device address configuration is shared through `RECAMERA_DEVICE_IP` plus CLI inputs (`--device-ip` for FastAPI video/perception, `--gimbal-ip` for `main_phase3.py` real control). The control dashboard can update the FastAPI video/perception address at runtime, but it never opens a hardware control client.

The system remains single-control-plane: FastAPI is UI Event emitter + telemetry viewer; EventBus carries UI events; `main_phase3.py` owns FSM, orchestration, safety gating, and all `RecameraClient.apply_command()` calls.

Dashboard feature pages are mutually exclusive and manually activated through a lease-backed session. Each page waits for `启动功能`; the browser renews a 2.5-second lease, page switches stop the previous session, and lease expiry stops control after crashes or network loss.

Hardware mapping:

- **ReSpeaker XVF3800**: USB control provides DOA/VAD and the physical 12-LED WS2812 DOA ring; USB Audio Class provides meeting recording. TCP 9999 is fallback DOA transport and cannot control a local physical LED ring.
- **reCamera Gimbal 2002w**: SSCMA provides video; the companion Node-RED flow exposes dual-axis command/stop/status APIs backed by official CAN motor nodes.
- **main_phase3.py**: owns the only Orchestrator, SafetyLayer, RecameraClient, feature session, lease and real motor readback.
- **FastAPI**: converts ReSpeaker/HTML input to Events and displays the runtime snapshot. It never opens a gimbal hardware client.

Closed control paths:

```text
reCamera vision -> Event -> single session -> yaw/pitch command -> CAN motors
ReSpeaker DOA -> EventBus -> multi/meeting-yaw session -> yaw-only command -> CAN motor
Node-RED motor status -> main_phase3 -> EventBus runtime snapshot -> FastAPI -> Dashboard
authoritative audio feature -> ReSpeaker LED_EFFECT=4 -> physical DOA ring + UI sector
```

The device flow is stored at `deploy/node_red/recamera_control_bridge.json`.
