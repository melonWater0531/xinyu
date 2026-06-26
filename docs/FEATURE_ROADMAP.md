# 心屿 XINYU 功能复现路线图

> 基准：2026-06-26 代码库审计 + home.html 叙事分析
> 叙事核心：**个人情绪陪伴型 AI** — 看见你 → 理解状态 → 陪伴记录 → 有温度的回应

---

## 功能复现性价比评估表

| 功能 | 当前状态 | 实现难度 | 与心屿叙事契合度 | home.html 入口 | 建议 |
|------|----------|----------|------------------|---------------|------|
| **专注时长计时**（face on-screen → timer） | ✅ 已实现 | ★☆☆ 低 | ★★★ 极高 | ✅ `focusDurationValue` 已有 | 已完成 |
| **情绪监测接真实后端**（EmotiEff → 前端实时） | ✅ 已实现 | ★☆☆ 低 | ★★★ 极高 | ✅ `emotionStateValue/Confidence` | 已完成 |
| **多人场景 DOA 实时显示**（NetworkDOA → 前端） | ✅ 已实现 | ★☆☆ 低 | ★★★ 高 | ✅ `doaValue/doaFreshValue` | 已完成 |
| **Wake Word 唤醒"小屿"** | ⚠️ 文件存在未接入 | ★☆☆ 低 | ★★★ 极高 | ❌ 加一行提示即可 | 暂缓 |
| **周趋势图接真实数据**（情绪+专注 → weekBars） | ✅ 已实现 | ★☆☆ 低 | ★★★ 高 | ✅ `weekBars/weekAvg` 已有 | 已完成 |
| **多人场景声源定位 + 会议记录写入** | ✅ 已实现 | ★★☆ 中 | ★★★ 极高 | ✅ 多人场景卡片 + 独立 meeting_notes | 已完成 |
| **健康建议接 LLM**（`/api/chat` DeepSeek） | ✅ 已实现 | ★★☆ 中 | ★★★ 高 | ✅ `homeAdvice/healthInsight` | 已完成 |
| **情绪日记自动写入**（日记写入后 LLM 生成回应） | ✅ 已实现 | ★★☆ 中 | ★★★ 极高 | ✅ `/api/reflect` diary + 聊天注入 | 已完成 |
| **停留时长（dwell time）作为专注度指标** | ❌ 未实现 | ★★☆ 中 | ★★★ 高 | ✅ 复用 `focusDurationValue` | 近期做 |
| **速度控制参数传给云台** | ❌ 字段存在未传 | ★☆☆ 低 | ★☆☆ 低 | ❌ 用户感知不到 | 顺手修 |
| **事件截图**（情绪突变 → 自动截 JPEG） | ⚠️ 部分 | ★★☆ 中 | ★★☆ 中 | 弱（日记可关联） | 考虑做 |
| **噪声抑制**（改善 VAD 准确率） | ❌ 未实现 | ★★☆ 中 | ★★☆ 中 | ❌ 间接改善多人场景 | 已规划（详见下方） |
| **人数统计—跨帧**（滑动窗口） | ⚠️ 部分 | ★☆☆ 低 | ★★☆ 中 | 弱（多人场景卡片） | 考虑做 |
| **Node-RED 流生成** | ⚠️ 部分 | ★★☆ 中 | ★☆☆ 低 | ❌ 开发工具 | 低优先级 |
| **MQTT 事件总线** | ❌ 未实现 | ★★☆ 中 | ★☆☆ 低 | ❌ 基础设施 | 低优先级 |
| **Home Assistant 集成** | ❌ 未实现 | ★★★ 高 | ★★☆ 中 | ❌ 需要 HA 用户 | 低优先级 |
| **视频录像（event-based）** | ⚠️ 部分 | ★★★ 高 | ★☆☆ 低 | ❌ 叙事不符 | 跳过 |
| **RTSP 流输出** | ❌ 未实现 | ★★★ 高 | ★☆☆ 低 | ❌ 监控型需求 | 跳过 |
| **热力图** | ❌ 未实现 | ★★★ 高 | ★☆☆ 低 | ❌ 叙事完全不符 | 跳过 |
| **区域入侵检测** | ❌ 未实现 | ★★★ 高 | ★☆☆ 极低 | ❌ 安防叙事 | 跳过 |
| **手势识别** | ❌ 未实现 | ★★☆ 中 | ★★☆ 中 | ❌ 工具型交互 | 已规划（详见下方） |

---

## 新功能：多人场景声源定位 + 会议记录写入

### 叙事定位

在心屿叙事中，多人场景的核心不是"监控谁说了什么"，而是：

> **"这段对话是今天发生的事，让小屿帮我整理成一段日记。"**

技术上是 DOA 定位 + ASR 转写，产品上呈现为**对话式会议记录 → 日记条目**，完全融入现有日记叙事。

---

### 技术架构

```
ReSpeaker XVF3800
  ├── DOA (0-359°)     → 声源方向 → 判断"谁在说话"（左/中/右区域）
  └── has_speech       → 说话检测 → 触发录音片段

                      ↓
          audio/session_recorder.py（新建）
          • 按 has_speech 切分发言片段
          • 每段标注 DOA 方向区域（左/<135° / 中/135-225° / 右/>225°）
          • 积累到 session buffer（内存，不落盘）

                      ↓
          audio/transcriber.py（新建）
          • Whisper（本地推理，faster-whisper 量化版）
          • 输入：发言片段 bytes
          • 输出：{speaker_zone, text, ts}

                      ↓
          POST /api/meeting/append（新增 FastAPI 路由）
          • 接收转写结果，维护当次会议 transcript
          • 到达停止指令或静默超过 60s 时关闭会议

                      ↓
          POST /api/meeting/summarize（新增路由）
          • 将 transcript 发给 LLM
          • 输出：{summary, action_items, participants_count}
          • 写入 diary 系统（/api/diary/write 现有接口）

                      ↓
          home.html 多人场景卡片（改动最小）
          • 开始多人跟踪 → 同时开启 session_recorder
          • 新增"保存会议记录"按钮 → 调用 /api/meeting/summarize
          • 结果追加到今日日记
```

### 现有代码复用

| 组件 | 复用情况 |
|------|----------|
| `audio/network_doa.py` | DOA + has_speech 信号直接使用，零改动 |
| `recamera_fastapi.py` `_ensure_doa_reader()` | 复用已有 DOA 轮询逻辑 |
| `recamera_fastapi.py` `/api/chat` | LLM 对话路由结构可直接参考 |
| `dashboard/home.html` 日记系统 | `defaultDiary` 结构直接兼容，写入 `text` + `reply` 字段 |
| `dashboard/home.html` 多人场景卡片 | 只需在现有 `multiToggleBtn` 后加一个"导出会议记录"按钮 |

### 新增文件（最小化）

```
audio/session_recorder.py    ~80 行   has_speech 切片 + DOA 标注
audio/transcriber.py         ~60 行   faster-whisper 封装
recamera_fastapi.py          +3 路由  /api/meeting/start /append /summarize
```

### 依赖

```bash
pip install faster-whisper   # Whisper 量化版，CPU 可用，tiny 模型 ~150MB
```

tiny 模型速度：CPU 上约 实时的 4-6×（10s 音频 ≈ 2s 转写），足够满足会议场景。

### 难点与风险

| 风险 | 评估 | 缓解 |
|------|------|------|
| 多说话人区分 | DOA 只能区分方向区域（不能做说话人识别） | 用"左/中/右"代替人名，叙事上说"不同方向的声音" |
| 中文 ASR 准确率 | Whisper tiny 中文约 85-90%，有错漏 | LLM 后处理时做语义修正，不追求逐字精准 |
| 实时性 | 转写有 2-3s 延迟 | 采用异步后台转写，不阻塞 DOA 追踪 |
| 音频落盘隐私 | 不默认保存原始音频 | session buffer 在内存中，关闭会议后丢弃 |

### 整合到 home.html 的方式

```
多人场景跟踪卡片（现有）
  ├── [开始多人跟踪]      → 同时开启 session_recorder（新）
  ├── 声源方向：315°      → 实时 DOA（已有入口）
  ├── DOA 状态：已连接     → 已有
  └── [保存会议记录]（新） → 调用 /api/meeting/summarize
                            → 写入今日日记
                            → toast 提示"会议记录已写入今日日记"
```

前端改动：**仅在多人场景卡片追加一个按钮**，无需新增页面。

---

## 新功能：手势识别（Gesture Recognition）

### 叙事定位

在心屿叙事中，手势是"无需开口的意图表达"：

> **"我举手，小屿就知道我想说话了。"**

技术上是 COCO 关键点位置规则检测，产品上呈现为**手势触发 → 摄像头锁定 / 专注记录开始**，融入现有单人追踪叙事。

---

### 关键限制

YOLO11n-pose 只提供手腕位置（wrist），**无法识别手指手势**（OK/Peace/Thumbs-up）。如需手指细节，需额外引入 MediaPipe Hands（每帧 +15ms 延迟）。当前方案覆盖手臂级别：举手 + 挥手，已足够触发系统级交互。

---

### 技术架构

现有资产（**零新依赖**）：

```
YOLO11n-pose → 17 COCO 关键点（vision/pose_estimator.py:23-29）
  ├── left/right_wrist  (index 9, 10) ← 手势检测信号源
  ├── left/right_elbow  (index 7, 8)
  ├── left/right_shoulder (index 5, 6)
  └── nose              (index 0)    ← 参照坐标基准

_latest_pose_persons（recamera_fastapi.py:297）
  ← 已在每帧更新，直接读取 keypoints，零额外推理开销
```

---

### 支持手势定义

| 手势 ID | 触发条件 | 防抖 | 建议动作 |
|---------|----------|------|---------|
| `HAND_RAISED`（举手） | wrist.y < nose.y AND wrist.conf ≥ 0.4 AND nose.conf ≥ 0.4 | 连续 3 帧 | 摄像头归位到该人；自动开启单人监测 |
| `WAVE`（挥手） | wrist.y < shoulder.y AND 1s 内水平位移 > 0.15 帧宽 AND ≥2 次方向反转 | 连续 3 帧 | 触发专注记录开始/停止 |
| `NONE` | 以上均不满足 | — | 无动作 |

坐标系说明：图像原点在左上角，y 轴向下，因此"手腕高于鼻子"的判断为 `wrist.y < nose.y`。

---

### 新增文件

```
vision/gesture_detector.py    ~90 行   规则分类器 + 防抖 + 挥手振荡检测
```

**核心逻辑骨架（供实现参考）：**

```python
class GestureDetector:
    def __init__(self, stable_frames=3, wave_window=15, wave_thresh=0.15):
        # stable_frames: 需要连续 N 帧才上报，防抖
        # wave_window: 保留最近 N 帧的 wrist.x 历史
        # wave_thresh: 1s 内累计水平位移阈值（帧宽归一化）
        ...

    def update(self, persons: List[PersonPose]) -> GestureResult:
        """每帧调用，返回稳定后的手势结果（连续 stable_frames 帧相同才输出）。"""
        ...

    def _detect_raw(self, persons) -> GestureResult:
        """检测当帧原始手势，不做防抖。"""
        # 1. 取 persons[0]（主要检测对象）
        # 2. 提取 nose, left/right_wrist, left/right_shoulder
        # 3. 判断 HAND_RAISED: wrist.y < nose.y and conf >= 0.4
        # 4. 判断 WAVE: wrist.y < shoulder.y and _is_wave()
        ...

    def _is_wave(self) -> bool:
        """检测 wrist.x 历史中是否存在明显水平振荡（≥2 次方向反转且总位移 > wave_thresh）。"""
        ...
```

---

### 集成点

```
recamera_fastapi.py
  ├── 全局变量区（~line 315）: _gesture_detector = None; _gesture_result = {"gesture": "NONE", ...}
  ├── state_push_loop(): gr = _gesture_detector.update(_latest_pose_persons)
  │                      _gesture_result = {"gesture": gr.gesture, "hand": gr.hand, "confidence": ...}
  ├── build_state_snapshot(): 追加 "gesture": _gesture_result
  └── 新路由: GET /api/gesture/state（只读调试，不走 WebSocket）

dashboard/home.html
  ├── 情绪卡片 feature-state 区域: 追加"当前手势 / 手势方向"两个 state-cell
  └── renderState(s): const gMap = {HAND_RAISED:"举手", WAVE:"挥手", NONE:"--"}
                      setText("gestureValue", gMap[s.gesture?.gesture] || "--")
```

---

### 实现难度与风险

| 项目 | 评估 |
|------|------|
| 实现难度 | ★★☆ 中（规则逻辑简单；关键点可信度在侧身/遮挡时波动） |
| 叙事契合度 | ★★☆ 中（直觉交互，与情绪陪伴叙事关联偏弱，但触发交互有用） |
| 主要风险 | wrist.conf 在低光或遮挡时 < 0.4 → 漏检；手臂自然运动可能误触发 WAVE |
| 缓解方案 | 提高 `stable_frames` 至 5；增加手臂伸直验证（wrist.y < elbow.y < shoulder.y）|

---

## 新功能：噪声抑制（Noise Suppression）

### 叙事定位

噪声抑制是对用户不可见的后台改善，直接提升会议记录转写准确率：

> **"背景噪声不再干扰小屿听你说话。"**

技术上是音频采集预处理 + VAD 替换，产品上对用户只显示"降噪：启用"标志，无需任何主动操作。

---

### 当前问题

`ConversationRecorder._segment_loop()`（`audio/conversation_recorder.py:212-218`）使用纯 RMS 阈值 VAD：

```python
level = float(np.sqrt(np.mean(np.square(chunk))) + 1e-9)
noise_floor = min(0.06, max(0.004, noise_floor * 0.98 + level * 0.02))
threshold = max(0.014, noise_floor * 2.8)
voiced = level >= threshold
```

**问题表现：**
- 风扇/空调噪声常超过 RMS 阈值 → 误录制背景噪声片段 → 转写出垃圾文本
- 安静环境中低音量语音常低于阈值 → 漏录
- faster-whisper 对含噪音频转写准确率明显下降

---

### 技术架构

```
ReSpeaker 音频流（16kHz, mono, float32, 100ms 块）
  ↓  _audio_callback()
  ↓  [新] suppress_noise(chunk, sr=16000)   ← noisereduce 频谱噪声抑制
  ↓  _audio_q.put(chunk)
  ↓  _segment_loop()
  ↓  [替换] is_voiced_webrtcvad(chunk, sr)  ← WebRTC VAD（帧级，10ms/帧）
  ↓  分段写 WAV → faster-whisper 转写
```

---

### 噪声抑制方案：`noisereduce`

**库：** `noisereduce`（频谱减法，CPU-only）

**工作原理：**
1. 收集前 0.5s（8000 样本）的静音段建立噪声档案（`_noise_profile`）
2. 对每个 100ms 块调用 `nr.reduce_noise(y=chunk, y_noise=profile, stationary=True, prop_decrease=0.7)`
3. `stationary=True` 模式处理平稳噪声（风扇/空调），速度远快于非平稳模式
4. 新录音开始时（`start()` 调用）重置档案，重新采集噪声基线

---

### VAD 替换方案：`webrtcvad`

**库：** `webrtcvad-wheels`（WebRTC VAD，预编译二进制，WSL 兼容）

**工作原理：**
- float32 → int16 转换（`(clip(audio,-1,1)*32767).astype(int16)`）
- 按 10ms 帧（160 样本 @ 16kHz）逐帧判断，`aggressiveness=2`
- 任意一帧被判断为语音 → 整个 100ms 块标记为 voiced

**降级策略：** `webrtcvad-wheels` 未安装时，`is_voiced_webrtcvad()` 内部自动回退到现有 RMS 逻辑，零破坏性。

---

### 新增文件

```
audio/noise_suppressor.py    ~70 行   noisereduce 封装 + webrtcvad VAD + 双重降级
```

**接口设计：**

```python
def suppress_noise(audio: np.ndarray, sr: int = 16000) -> np.ndarray:
    """频谱噪声抑制。noisereduce 未安装时 pass-through。"""

def is_voiced_webrtcvad(audio: np.ndarray, sr: int = 16000) -> bool:
    """帧级 VAD。webrtcvad 未安装时回退 RMS 阈值。"""

def try_init_webrtcvad(aggressiveness: int = 2) -> bool:
    """初始化 VAD 实例，返回是否成功。在 start() 中调用一次。"""

def reset_noise_profile():
    """清空噪声档案，下次 suppress_noise() 重新采集基线。在 start() 中调用。"""
```

---

### 集成点

```
audio/conversation_recorder.py
  ├── start()（~line 108）:
  │     from audio.noise_suppressor import try_init_webrtcvad, reset_noise_profile
  │     try_init_webrtcvad(aggressiveness=2)
  │     reset_noise_profile()
  ├── _audio_callback()（~line 186，mono 提取后、queue 写入前）:
  │     from audio.noise_suppressor import suppress_noise
  │     mono = suppress_noise(mono, self.sample_rate)   # 预处理
  └── _segment_loop()（~line 212，替换 RMS 判断）:
        from audio.noise_suppressor import is_voiced_webrtcvad
        voiced = is_voiced_webrtcvad(chunk, self.sample_rate)

recamera_fastapi.py
  └── build_state_snapshot(): 追加
        "audio_processing": {
            "noise_sup": _nr_available,       # bool
            "vad_mode": "webrtcvad" or "rms"
        }

dashboard/home.html
  └── 多人场景卡片 feature-state 区域追加:
        <div class="state-cell"><span>降噪</span><b id="noiseSupValue">--</b></div>
      renderState(s): setText("noiseSupValue", s.audio_processing?.noise_sup ? "启用" : "关闭")
```

---

### 依赖安装

```bash
pip install noisereduce            # 频谱噪声抑制（~5 MB，无 C 扩展）
pip install webrtcvad-wheels       # WebRTC VAD（预编译，WSL 无需 C 编译器）
```

---

### 实现难度与风险

| 项目 | 评估 |
|------|------|
| 实现难度 | ★★☆ 中（API 简单；调参需要真实噪声环境测试）|
| 叙事契合度 | ★★☆ 中（改善会议记录质量，间接支撑核心叙事）|
| 主要风险 | 噪声档案建立需要开头约 0.5s 静音段；对突发性非平稳噪声（键盘敲击）效果有限 |
| 缓解方案 | 录音开始前显示"请稍候…"给用户暗示停顿；嘈杂环境改 `aggressiveness=3`；每次 start() 自动重置档案 |

---

## 实施顺序建议

见《开发顺序建议》章节。
