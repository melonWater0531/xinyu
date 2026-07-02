# Future Meeting Pipeline Upgrades

> 记录本轮暂不执行的会议 pipeline 功能，便于后续继续升级部署。
> 当前版本只落地云端 ASR/LLM 串联、非阻塞说话人标注和默认关闭的 wake word 基础集成。

## 1. 完整 pitch 搜索

- 暂缓原因：当前单控制面要求所有云台动作都经过 `main_phase3.py`、FSM、Orchestrator 和 SafetyLayer，FastAPI 不直接驱动 pitch。
- 依赖：控制运行时新增安全的 search session、pitch 速度/范围策略、搜索超时和 stop 语义。
- 建议路径：先在 `services/speaker_mapper.build_search_plan()` 保持纯计划输出，再由 `main_phase3.py` 增加会议 search Event 协议并统一执行。

## 2. FastAPI 驱动云台扫描

- 暂缓原因：会引入第二条硬件控制路径，破坏当前 SINGLE CONTROL PLANE。
- 依赖：无。该方向不建议落地。
- 建议路径：FastAPI 只发 Event 或展示计划；实际扫描必须由控制运行时持有 lease 后执行。

## 3. 控制运行时 speaker search 协议

- 暂缓原因：本轮只做只读状态注册，不改 `core/fsm.py`、`core/orchestrator.py`、`main_phase3.py`。
- 依赖：新增 Event 类型、session 权限、状态回传字段，以及 dashboard 操作入口。
- 建议路径：定义 `speaker_search/requested`、`speaker_search/progress`、`speaker_search/completed` 事件，再把 `build_search_plan()` 接到 Orchestrator。

## 4. 唇动验证

- 暂缓原因：需要稳定的人脸关键点时间序列和口部运动阈值校准，短期误判风险高。
- 依赖：Face Landmarker 连续帧缓存、口部关键点差分、VAD 对齐窗口。
- 建议路径：先离线记录 `mouth_open`、VAD、DOA、track_id，再建立阈值和置信度策略。

## 5. ArcFace 重识别说话人

- 暂缓原因：当前人脸追踪已有 ArcFace 能力，但会议说话人映射还未定义用户命名、隐私提示和持久化策略。
- 依赖：用户授权、说话人别名编辑、embedding 存储与清除接口。
- 建议路径：先仅保存 session 内临时 track label，后续再增加 opt-in 的本地身份库。

## 6. 前端说话人可视化（已落地基础闭环）

- 已完成：control 面板已合并多人 DOA 与会议录音，展示 session 内说话人、逐句转写、处理状态和 LLM 会议纪要。
- 已完成：`POST /api/meeting/complete` 严格按停止控制、封存录音、逐段 ASR、生成纪要的顺序执行。
- 后续增强：允许用户在摘要保存前修改临时说话人标签，并将修改同步回 timeline。

## 7. openWakeWord 模型安装与自定义唤醒词

- 暂缓原因：openWakeWord 保持可选依赖，默认不加载，避免影响无音频设备或无模型环境启动。
- 依赖：模型文件下载、音频输入设备选择、唤醒词阈值、误唤醒测试。
- 建议路径：新增安装脚本和模型目录，`ENABLE_WAKE_WORD=true` 时校验模型；再支持自定义唤醒词配置。

## 8. 真正说话人分离模型

- 暂缓原因：pyannote 等模型依赖重、资源占用高，并且可能需要授权 token。
- 依赖：模型授权、GPU/CPU 性能评估、长音频切片与时间戳对齐。
- 建议路径：先保留当前 DOA + face_lock 的轻量标签；后续把 diarization 作为离线增强步骤，不阻塞会议保存。

## 9. 摘要质量评测集

- 暂缓原因：当前先完成链路闭环，缺少多说话人真实样本。
- 依赖：匿名化会议音频、人工标注的 speaker/transcript/summary 对照集。
- 建议路径：收集 5-10 段内部样本，覆盖未知说话人、多人插话、ASR 空转写和短会议场景。
