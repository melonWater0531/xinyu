# reCamera Multimodal System

本仓库保留当前可复现的 reCamera 多模态演示系统：单人人脸追踪、ReSpeaker DOA 声源转向、reCamera 扬声器播报、手势识别展示、心屿 `/home` 产品页、`/control` 工程调试台，以及 DeepSeek/智谱驱动的陪伴聊天、日记和周报能力。

会议录音和 ASR 工程链路已接入，腾讯会议等外部录音可进入转写链路；但 ReSpeaker 实时录音音质未稳定验收，会议纪要未实现为可交付功能。

## 1. 环境安装

```bash
cd ~/recamera_multimodal
python3 -m pip install -r requirements.txt
```

模型文件放在 `models/`，用途和来源见 `models/README.md`。Node-RED 云台和音频桥接 Flow 放在 `deploy/node_red/`，部署方式见 `deploy/node_red/README.md`。

## 2. 关键环境变量

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
export RECAMERA_DOA_SOURCE=usb
export RECAMERA_AUDIO_DEVICE=<AUDIO_DEVICE_INDEX>
export DEEPSEEK_API_KEY=sk-xxx   # 可选：陪伴聊天、日记、周报首选
export ZHIPU_API_KEY=sk-xxx      # 可选：LLM 兜底、云端 ASR、TTS
export no_proxy="127.0.0.1,localhost,$RECAMERA_DEVICE_IP"
export NO_PROXY="$no_proxy"
```

`RECAMERA_AUDIO_DEVICE` 来自 `sounddevice` 输入设备索引；没有会议录音需求时可以不设置。完整硬件连接、usbipd、Node-RED 验证和故障处理见 `docs/SOP.md`。

## 3. 启动

终端 1：启动 FastAPI、视频感知、产品页和调试台。

```bash
python3 recamera_fastapi.py --device-ip "$RECAMERA_DEVICE_IP"
```

终端 2：启动控制运行时，让 UI Event 经 EventBus 进入 Orchestrator 和 SafetyLayer。

```bash
python3 main_phase3.py \
  --enable-control \
  --gimbal-ip "$RECAMERA_DEVICE_IP" \
  --manual-control \
  --fps 10
```

打开页面：

```text
http://localhost:8001/home      # 心屿五页产品页
http://localhost:8001/control   # 工程调试台
```

## 4. 当前功能状态

- 单人场景：人脸检测/追踪、锁脸、丢脸保持、搜索/回中、reCamera 云台跟随，人工演示已基本稳定。
- 多人/声源场景：ReSpeaker DOA/VAD 事件驱动 reCamera yaw-only 转向，真实设备测试通过且反应迅速。
- 语音播报：智谱 TTS 音频缓存、Node-RED audio bridge、reCamera 端 `aplay` 播放、测试音和自动提醒已通过人工验证。
- 手势识别：MediaPipe 手势类别、置信度和稳定帧展示已接入；当前只展示，不进入控制主链路。
- `/home` 产品页：五页产品形态已完成，接入实时状态、聊天、日记、周报、会议记录入口、语音和提醒设置；接口失败时有本地 fallback。
- LLM 能力：陪伴聊天、日记反思和周报已实现部署，DeepSeek/智谱可配置，失败时回退本地逻辑。
- 健康状态：情绪、专注、眼部指标、久坐/眼疲劳/会议状态提醒已接入；提醒类功能仍缺少长时间真实场景验证。
- 会议录音/ASR：工程链路已接通，但 ReSpeaker 实时录音质量不稳定，会议纪要未完成。

更多交接状态见 `docs/project_handoff_status.md`。

## 5. 验证

```bash
python3 -m compileall recamera_fastapi.py main_phase3.py core hardware audio services vision tests
python3 -m unittest discover -s tests
```

如果只检查前端静态资源，确认 `dashboard/home.html` 仍引用 `/static/product_home/home.css`、`home.js` 和 `seed_data.js`，并确认 `dashboard/sw.js` 不缓存旧 `/home-old` 路径。
