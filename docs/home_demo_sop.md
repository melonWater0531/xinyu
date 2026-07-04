# 心屿 Home 产品演示 SOP

## 独立五页产品预览（推荐录屏入口）

- 产品名为“心屿”，智能助手名为“小屿”。
- 本轮新增独立页面 `/static/xinyu_preview.html`，用于产品视频录制与视觉确认；它不会替换旧 Home 或 control dashboard。
- 五页分别为首页、陪伴、会议、记录和我的。首页主推情绪识别与今日状态，小屿提供温柔主动建议。
- 情绪识别只是帮助用户回看状态的弱线索，不是医学或心理诊断；多人场景不判断个人私人情绪。
- 记录页的趋势图只展示明显情绪转折点，不呈现高频监控或复杂算法数值。
- 图标使用系统风格 inline SVG，没有生成 PNG、WebP 或独立 SVG 图片资源。
- Preview 的状态、趋势、会议和设备内容使用前端 demo 数据，并在页面标注“产品预览 · 演示数据”；会议按钮只切换演示状态，不启动真实 recorder。
- 陪伴页会优先调用现有 `/api/chat`。Prompt 延续“用户本轮自述 > 近期对话 > 页面演示弱线索”的原则，回复保持温和克制；请求 10 秒超时或 LLM 不可用时立即使用本地 fallback，因此离线录屏也不会出现空白。
- Preview 只使用 `xinyu.preview.v1` localStorage key，不读取旧 Home 数据；除陪伴页 `/api/chat` 外，不调用 camera、tracking、recorder、gimbal 或 control API。
- 旧 control、tracking、recorder、gimbal 和 Home 页面均未被这套 preview 替换。

推荐录屏时直接打开 `/static/xinyu_preview.html`：先展示首页当前情绪、趋势、今日状态与小屿建议，再依次切换陪伴、会议、记录和我的。会议页应说明为纯前端演示，避免将 demo 状态描述成真实录音结果。

### Preview 启动方式

在仓库根目录启动 FastAPI。LLM key 是可选项：配置后陪伴页优先走云端 LLM；未配置或调用失败时，页面会自动使用本地 fallback。

```bash
cd ~/recamera_multimodal
export DEEPSEEK_API_KEY=sk-xxx   # 可选；有 key 时优先走云端 LLM
export ZHIPU_API_KEY=sk-xxx      # 可选；作为兜底
python3 recamera_fastapi.py
```

电脑本机访问：

```text
http://localhost:8001/static/xinyu_preview.html
```

### 手机录屏时如何找到电脑 IP

手机和电脑需要在同一 Wi-Fi / 局域网。Linux 或 WSL 常用：

```bash
hostname -I
```

从输出里选择和手机同一局域网的 IPv4，一般形如 `192.168.x.x`。如果输出较多，可以更精确查看当前网卡：

```bash
ip -4 addr
```

优先找 `wlan`、`wifi`、`eth` 或当前联网网卡下的 `inet 192.168.x.x/xx`。如果在 Windows 主机上查看，则运行：

```powershell
ipconfig
```

查看当前 Wi-Fi 或以太网的 `IPv4 Address`。手机浏览器访问：

```text
http://<电脑IP>:8001/static/xinyu_preview.html
```

例如：

```text
http://192.168.1.23:8001/static/xinyu_preview.html
```

如果手机打不开，先确认手机和电脑在同一网络，并检查防火墙是否允许访问电脑的 `8001` 端口。

### Preview 陪伴页 LLM 调用方式

陪伴页只调用现有 `POST /api/chat`，不调用 camera、tracking、recorder、gimbal 或 control。前端发送的 payload 包含 `message`、`emotion`、`diary_text`、`user_name` 和 `context`。

Prompt 原则是：用户本轮自述优先，近期对话次之，页面里的“有点疲惫”等状态只是产品预览弱线索；当演示状态和用户文字冲突时，只跟随用户文字。回复不输出诊断、模型标签或概率，保持 40–90 字、温和克制。

录屏前可用下面命令验证 `/api/chat` 是否可调用：

```bash
curl -sS -X POST http://localhost:8001/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"今天有点累","emotion":"疲惫","diary_text":"","user_name":"Lintong","context":"用户本轮自述优先；preview 页面演示状态只是弱线索，不得覆盖用户文字。回复 40-90 个中文字符，温和、不诊断。"}'
```

Preview 前端对 `/api/chat` 设置 10 秒超时。LLM 不可用、网络失败或接口超时时，陪伴页会立即显示本地 fallback，因此页面有回复不一定代表已经真实调用云端 LLM。若要确认真实调用情况，优先看上面的 `curl` 响应，或在浏览器 DevTools Network 中查看 `/api/chat` 请求。

## 1. Home demo 功能说明

Home 是心屿的日常陪伴入口，演示重点是“用户能看懂、能操作、离线也不空白”。本次界面采用奶油色扁平贴纸风：首页直接展示步数、喝水、散步和冥想，陪伴页提供温和建议与情绪聊天，记录页呈现日记、情绪转折趋势和本周简报。

## 2. Self-care 数据来源

- `演示数据`：用于首次打开和录屏兜底，共 7 天，不代表用户真实健康数据。
- `本机记录`：喝水、散步、冥想以及手动步数保存在当前浏览器的 `xinyu.selfcare.v1`。
- `手机数据`：仅预留 NativeBridge、Health Connect 和 StepCounter provider，本轮没有实现或调用 Android 原生能力。

页面会保留 source 标识。只要用户点击记录按钮，当天对应项目即切换为本机记录；未填写的项目继续使用演示数据补位。

## 3. PWA 当前可实现能力

当前 PWA 可以完整保存喝水、手动散步和冥想记录，并展示 demo/local 步数。冥想当前不播放真实音频，时间和背景声音选择只用于产品流程展示与本地记录。清除浏览器站点数据会同时清除这些记录，页面不依赖 LLM 才能完成 self-care 操作。

## 4. APK 后续接口预留

`NativeBridgeSelfCareProvider`、`HealthConnectProvider` 和 `StepCounterProvider` 已保留稳定接口。后续 APK 可在不改 Home 卡片数据契约的前提下提供步数等数据；本轮不包含 Android、权限申请或 Health Connect 实现。

## 5. 周报生成逻辑

本周简报按用户日记/自述、self-care 主动记录、视觉弱线索的顺序组织。记录越完整，正文可适度展开，但始终保持短文、无标题、无列表，并只给一个具体建议。云端模型不可用时会自动使用本地 fallback；包含 demo 数据时会明确显示“含演示数据”或“演示数据预览”。

## 6. 情绪交互原则

用户说出来的感受永远优先于视觉推断，日记和近期聊天次之。摄像头情绪只是一条不确定的弱线索，不向用户复述模型标签、概率，也不会在冲突时沿错误标签追问。云端不可用时，本地回应仍会保持 40–90 字、温和且不诊断。

Wellbeing 的主动建议来自本地规则，并可在未来接入可选 LLM：用户明确的不适优先，其次是补水、走动和冥想。规则同步执行且始终有 fallback，不会让页面等待模型。多人场景不判断个人情绪，只提示节奏与休息。

记录页的情绪趋势只保留明显转折点，不绘制高频监控曲线。存在当日日记或 mood event 时使用本机记录；仅在 demoMode 开启时展示带“演示数据”标识的示例，否则显示温和空状态。

页面图标当前使用 CSS `icon slot` 绘制低饱和有机形状，后续可以替换为正式扁平贴纸插画。本轮没有生成真实图标文件，也没有新增 PNG、WebP 或 SVG 图标资源。

## 7. 推荐视频录制脚本

1. 打开 `/home`，停留在今日总览，说明数据首次可由演示内容补位。
2. 展示首页的奶油贴纸视觉与四张健康守护卡片。
3. 依次展示步数、喝水、运动和冥想卡片及其数据来源。
4. 点击“记一杯水”，展示杯数增加和“本机记录”。
5. 点击“记录一次散步”，展示最近一次运动更新。
6. 点击“开始一次冥想”，选择 5 分钟和雨声；开始后展示今日冥想分钟数更新，并说明当前不播放真实音频。
7. 进入陪伴页，对小屿说“今天有点累”。
8. 展示小屿先回应用户自述的温和回复；如网络不可用，说明这是本地 fallback。
9. 进入记录页，展示日历下方只记录明显转折点的情绪趋势。
10. 打开“写给这一周的你”，生成并展示简短趋势、肯定、需要照顾之处和一个下周建议。

## 录制前检查与失败兜底

- 建议使用干净浏览器 profile；需要固定演示时保留 demoMode，不要口头称其为真实用户数据。
- 云端聊天失败不会影响 self-care 操作；本地 fallback 会继续显示。
- 周报生成失败会直接使用本地简报，并显示数据来源。
- 若真实相机或控制服务未连接，只录制 Home 的本地 self-care、聊天 fallback 和周报流程；不要在本轮演示中宣称真实硬件验证已完成。
