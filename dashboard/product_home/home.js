(() => {
  "use strict";

  const STORAGE_KEY = "xinyu.preview.v1";
  const DIARY_KEY = "xinyu.actual.diary.v1";
  const MEETING_SESSION_KEY = "xinyu.product.meeting_session.v1";
  const seedData = window.XINYU_PREVIEW_DATA || {};
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  let toastTimer = 0;
  let meetingMarkdownText = seedData.meetings?.currentMeeting?.minutesMarkdown || "";
  let liveState = null;
  let systemHealth = null;
  let voiceState = null;
  let ws = null;
  let pollTimer = 0;
  let mediaRecorder = null;
  let voiceChunks = [];

  function loadJSON(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return value && typeof value === "object" ? value : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  const state = loadJSON(STORAGE_KEY, {activePage: "home", selectedDate: seedData.currentDate || "2026-07-04", calendarMonth: "2026-07"});
  const diaryOverrides = loadJSON(DIARY_KEY, {});
  state.meetingSessionId = state.meetingSessionId || localStorage.getItem(MEETING_SESSION_KEY) || "";

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      activePage: state.activePage,
      selectedDate: state.selectedDate,
      calendarMonth: state.calendarMonth,
      meetingSessionId: state.meetingSessionId || "",
    }));
  }

  function persistDiary() {
    localStorage.setItem(DIARY_KEY, JSON.stringify(diaryOverrides));
  }

  function persistMeetingSession(sessionId) {
    state.meetingSessionId = sessionId || "";
    if (state.meetingSessionId) localStorage.setItem(MEETING_SESSION_KEY, state.meetingSessionId);
    else localStorage.removeItem(MEETING_SESSION_KEY);
    persist();
  }

  async function apiJSON(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || 12000);
    try {
      const response = await fetch(path, {
        method: options.method || "GET",
        headers: options.body ? {"Content-Type": "application/json"} : undefined,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
        cache: options.method ? "no-store" : "no-store",
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || body.reason || `HTTP ${response.status}`);
      return body;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function stateData() {
    return liveState?.data || liveState || {};
  }

  function controlState() {
    return stateData().control || {};
  }

  function singleFaceVisible() {
    const data = stateData();
    if (data.single_face_visible != null) return Boolean(data.single_face_visible);
    if (data.face_visible != null) return Boolean(data.face_visible);
    if (data.emotion?.face_visible != null) return Boolean(data.emotion.face_visible);
    return false;
  }

  function currentEmotionText() {
    const data = stateData();
    const emotion = data.emotion || data.emotieff || {};
    return emotion.label || emotion.zh || emotion.display || emotion.emotion_zh || emotion.emotion || "";
  }

  function currentFocusText() {
    const data = stateData();
    const attention = data.attention || data.focus || {};
    const score = attention.score ?? data.attention_score ?? data.focus_score;
    if (attention.label) return attention.label;
    if (Number.isFinite(Number(score))) return Number(score) >= 70 ? "比较专注" : Number(score) >= 45 ? "需要休息" : "有些分散";
    return "";
  }

  function iconUse(id) {
    return `<svg aria-hidden="true"><use href="#${id}"/></svg>`;
  }

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function truncate(value, max = 76) {
    const text = String(value || "").trim();
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  }

  function parseDate(key) {
    const [year, month, day] = String(key).split("-").map(Number);
    return new Date(year, month - 1, day || 1);
  }

  function dateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }

  function formatDate(key) {
    const date = parseDate(key);
    return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  }

  function currentDay() {
    return seedData.dailyRecords?.[state.selectedDate] || seedData.dailyRecords?.[seedData.currentDate] || {};
  }

  function todayMemoryDay() {
    return seedData.assistantMemory?.currentDay || seedData.dailyRecords?.[seedData.currentDate] || {};
  }

  function showToast(message) {
    const toast = $("#xy-toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2300);
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value == null ? "" : String(value);
  }

  function goTo(page) {
    const target = $(`.xy-page[data-page="${page}"]`);
    if (!target) return;
    $$(".xy-page").forEach((section) => {
      const active = section === target;
      section.hidden = !active;
      section.classList.toggle("is-active", active);
    });
    $$(".xy-bottom-nav [data-go]").forEach((button) => {
      const active = button.dataset.go === page;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    state.activePage = page;
    persist();
    window.scrollTo({top: 0, behavior: "smooth"});
  }

  function trendLevel(point) {
    return {bright: 1, focused: 1.35, clear: 1.65, calm: 2, relaxed: 2.35, busy: 2.65, tired: 3.2, pressure: 3.55}[point?.mood] || 2;
  }

  function trendColor(mood) {
    return {bright: "#E7BD68", focused: "#D99B7A", clear: "#8FA9D8", calm: "#A8CFA0", relaxed: "#A8CFA0", busy: "#D99B7A", tired: "#B68A58", pressure: "#C9B7D8"}[mood] || "#8A6A45";
  }

  function renderTrend(container, points = todayMemoryDay().emotionTrend || []) {
    if (!container) return;
    const width = 350;
    const height = 184;
    const left = 22;
    const right = 16;
    const top = 26;
    const bottom = 144;
    const step = points.length > 1 ? (width - left - right) / (points.length - 1) : 0;
    const coords = points.map((point, index) => ({
      ...point,
      x: left + step * index,
      y: top + (trendLevel(point) / 4) * (bottom - top),
    }));
    const line = coords.map((point, index) => `${index ? "L" : "M"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
    const nodes = coords.map((point, index) => {
      const dy = index % 2 ? 18 : -12;
      const label = coords.length <= 4 || index === 0 || index === coords.length - 1 || point.mood === "tired" || point.mood === "pressure";
      return `<g><circle cx="${point.x}" cy="${point.y}" r="5.5" fill="#FFFDF8" stroke="${trendColor(point.mood)}" stroke-width="2.4"/>${label ? `<text class="xy-trend-label" x="${point.x}" y="${point.y + dy}" text-anchor="middle">${escapeHTML(point.display)}</text>` : ""}<text class="xy-trend-time" x="${point.x}" y="170" text-anchor="middle">${escapeHTML(point.time)}</text></g>`;
    }).join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="情绪状态变化"><g stroke="#E7DCCF" stroke-width="1" stroke-dasharray="3 6"><line x1="${left}" y1="42" x2="${width - right}" y2="42"/><line x1="${left}" y1="86" x2="${width - right}" y2="86"/><line x1="${left}" y1="130" x2="${width - right}" y2="130"/></g><path d="${line}" stroke="#D99B7A" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>${nodes}</svg>`;
  }

  function renderHome() {
    const day = todayMemoryDay();
    const faceVisible = singleFaceVisible();
    const emotionText = currentEmotionText();
    const focusText = currentFocusText();
    const control = controlState();
    const online = Boolean(liveState) || Boolean(systemHealth);
    const onlineBadge = $(".xy-home-header .xy-online");
    const title = $("#xy-home-title");
    const heroEmotion = $(".xy-emotion-hero h2");
    const heroCopy = $(".xy-hero-copy > p:nth-of-type(2)");
    const assistantNote = $(".xy-assistant-note span:last-child");
    if (onlineBadge) {
      onlineBadge.classList.toggle("is-offline", !online);
      onlineBadge.innerHTML = `<i></i>${online ? "心屿在线" : "离线陪伴"}`;
    }
    if (title && emotionText) title.textContent = faceVisible ? "我在这里，慢慢来" : "今天先慢一点也可以";
    if (heroEmotion) heroEmotion.textContent = faceVisible && emotionText ? emotionText : (day.mainState || "有点疲惫");
    if (heroCopy) {
      heroCopy.textContent = faceVisible
        ? `小屿看到你现在${emotionText || "有一些状态变化"}，${focusText ? `专注状态是${focusText}。` : "先按自己的节奏来。"}`
        : "小屿会先用本地记录陪你回看状态，等设备同步后再更新。";
    }
    if (assistantNote) {
      assistantNote.innerHTML = `<small>小屿回应</small>${control.active_feature === "meeting_recording" ? "会议记录中，情绪判断先放轻。" : (seedData.assistantMemory?.careSuggestion || "不急着完成，先让自己舒服一点。")}`;
    }
    const water = $(".xy-mini-states article:nth-child(1) strong");
    const steps = $(".xy-mini-states article:nth-child(2) strong");
    const meditation = $(".xy-mini-states article:nth-child(3) strong");
    if (water) water.textContent = `${day.waterCups || 5} / ${day.waterGoal || 8} 杯`;
    if (steps) steps.textContent = `${day.steps || 3200} 步`;
    if (meditation) meditation.textContent = day.meditation ? "已完成" : "未完成";
    const advice = $(".xy-advice > div p:last-child");
    if (advice) advice.textContent = seedData.assistantMemory?.careSuggestion || "先补一杯水，再给自己留一点安静时间。";
    $("[data-trend='home']") && renderTrend($("[data-trend='home']"), day.emotionTrend || []);
  }

  function buildAssistantMemoryContext() {
    const memory = seedData.assistantMemory || {};
    const day = memory.currentDay || todayMemoryDay();
    return {
      ...memory,
      currentDay: day,
      trendText: (day.emotionTrend || []).map((point) => `${point.time}${point.display}`).join("，"),
      careText: `喝水${day.waterCups || "-"} / ${day.waterGoal || 8}杯，步数${day.steps || "-"}，冥想${day.meditation ? "已完成" : "未完成"}`,
      meetingTitle: memory.currentMeeting?.title || day.meetingTitle || "",
      diaryText: day.diary || "",
    };
  }

  function buildXiaoyuReply(userInput, memoryContext = buildAssistantMemoryContext()) {
    const text = String(userInput || "").trim();
    const quick = memoryContext.quickReplies || {};
    if (quick[text]) return quick[text];
    if (text.includes("累") || text.includes("疲惫")) return quick["我今天有点累"] || memoryContext.careSuggestion;
    if (text.includes("整理") || text.includes("今天")) return quick["帮我整理一下今天"] || memoryContext.careSuggestion;
    if (text.includes("放松") || text.includes("休息")) return quick["给我一些放松建议"] || memoryContext.careSuggestion;
    if (text.includes("情绪") || text.includes("记录")) return quick["记录一下我的情绪"] || memoryContext.careSuggestion;
    return `小屿会先按你说的来理解。今天的状态里有${memoryContext.trendText || "一些起伏"}，也处理了${memoryContext.meetingTitle || "几件需要整理的事"}。如果愿意，可以从最想放下的一件事慢慢说。`;
  }

  function buildLLMPayload(message, memoryContext = buildAssistantMemoryContext()) {
    return {
      message,
      emotion: memoryContext.currentDay?.mainState || "有点疲惫",
      diary_text: memoryContext.diaryText || "",
      user_name: "蛋挞",
      context: [
        "用户本轮自述优先；今日状态、会议、日记和周报只是辅助上下文。",
        `今日情绪趋势：${memoryContext.trendText}`,
        `今日自我照顾：${memoryContext.careText}`,
        `今日会议：${memoryContext.meetingTitle}`,
        `近日日记：${(memoryContext.recentDiary || []).map((item) => item.diary).join(" / ")}`,
        `本周周报：${memoryContext.currentWeeklyReport?.summary || ""}`,
        "回复保持40至90个中文字符，温和克制，不诊断，不提模型、概率或识别过程。",
      ].filter(Boolean).join("；").slice(0, 900),
    };
  }

  function normalizeLLMReply(reply, fallback) {
    const value = String(reply || "").replace(/^#+\s*/g, "").replace(/\s*\n+\s*/g, "").trim();
    if (!value) return fallback;
    if (value.length <= 120) return value;
    const head = value.slice(0, 120);
    const boundary = Math.max(head.lastIndexOf("。"), head.lastIndexOf("？"), head.lastIndexOf("！"));
    return boundary >= 40 ? head.slice(0, boundary + 1) : `${head.slice(0, 89)}。`;
  }

  function shouldUseLLMReply(reply, fallback) {
    const value = String(reply || "").trim();
    const local = String(fallback || "").trim();
    if (!value || value === local) return false;
    const genericReplies = [
      "谢谢你愿意说出来。我在这里听着，也陪你一起整理。",
      "心屿收到了你的话。",
      "小屿收到了你的话。",
      "我在这里听着，也陪你一起整理。",
    ];
    if (genericReplies.some((item) => value.includes(item))) return false;
    const hasContext = ["今天", "喝水", "步数", "冥想", "会议", "预算", "活动", "压力", "放松", "平和", "累", "疲惫"].some((token) => value.includes(token));
    return hasContext || value.length >= Math.min(local.length, 42);
  }

  async function requestLLMReply(message, fallback) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        signal: controller.signal,
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(buildLLMPayload(message)),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || !body.reply) throw new Error("chat unavailable");
      const reply = normalizeLLMReply(body.reply, fallback);
      return shouldUseLLMReply(reply, fallback) ? reply : fallback;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function appendChat(message, role) {
    const thread = $("#xy-chat");
    const bubble = document.createElement("article");
    let textTarget = bubble;
    bubble.className = `xy-bubble ${role === "user" ? "xy-bubble-user" : "xy-bubble-assistant"}`;
    if (role === "assistant") {
      bubble.innerHTML = `<span class="xy-icon-badge xy-amber">${iconUse("xy-message")}</span><div><strong>小屿</strong><p></p></div>`;
      textTarget = $("p", bubble);
      textTarget.textContent = message;
    } else {
      bubble.textContent = message;
    }
    thread.append(bubble);
    bubble.scrollIntoView({behavior: "smooth", block: "nearest"});
    return textTarget;
  }

  function resetInitialChat() {
    const thread = $("#xy-chat");
    if (!thread || thread.dataset.ready) return;
    const memory = buildAssistantMemoryContext();
    thread.innerHTML = "";
    appendChat(`我看到你今天下午有一段压力比较高，晚上状态平和了一些。今天还整理了${memory.meetingTitle}，信息量不小。现在想聊聊刚才发生了什么吗？`, "assistant");
    thread.dataset.ready = "true";
  }

  async function sendChat(message) {
    const value = String(message || "").trim();
    if (!value) return;
    appendChat(value, "user");
    const fallback = buildXiaoyuReply(value);
    const pending = appendChat(fallback, "assistant");
    try {
      pending.textContent = await requestLLMReply(value, fallback);
    } catch (_error) {
      pending.textContent = fallback;
    }
  }

  function markdownToHtml(markdown) {
    const lines = String(markdown || "").split(/\n+/).map((line) => line.trim()).filter(Boolean);
    let html = "";
    let listOpen = false;
    lines.forEach((line) => {
      if (/^-{3,}$/.test(line)) return;
      if (line.startsWith("- ")) {
        if (!listOpen) {
          html += "<ul>";
          listOpen = true;
        }
        html += `<li>${escapeHTML(line.slice(2))}</li>`;
        return;
      }
      if (listOpen) {
        html += "</ul>";
        listOpen = false;
      }
      if (line.startsWith("# ")) html += `<h2 id="xy-meeting-dialog-title">${escapeHTML(line.slice(2))}</h2>`;
      else if (line.startsWith("## ")) html += `<h3>${escapeHTML(line.slice(3))}</h3>`;
      else if (line.startsWith("### ")) html += `<h3>${escapeHTML(line.slice(4))}</h3>`;
      else html += `<p>${escapeHTML(line)}</p>`;
    });
    if (listOpen) html += "</ul>";
    return html;
  }

  function formatMeetingTitle(title) {
    const safe = escapeHTML(title || "会议纪要");
    return safe
      .replace("下半年活动规划与预算申报周会", '下半年活动规划与<span class="xy-meeting-title-break">预算申报<span class="xy-nowrap">周会</span></span>')
      .replace(/([^>])周会/g, '$1<span class="xy-nowrap">周会</span>');
  }

  async function loadMeetingMarkdown() {
    const path = seedData.meetings?.currentMeeting?.minutesMarkdownPath;
    if (!path || location.protocol === "file:") return;
    try {
      const response = await fetch(`/static/product_home/${path.split("/").pop()}`, {cache: "no-store"});
      if (response.ok) meetingMarkdownText = await response.text();
    } catch (_error) {}
  }

  function renderMeeting() {
    const meeting = seedData.meetings?.currentMeeting || {};
    const conversation = stateData().conversation || {};
    const report = conversation.report || conversation.meeting_report || {};
    const status = report.status || conversation.recording_status || meeting.status || "已整理";
    const summary = report.summary || meeting.summary || "";
    const duration = report.duration_min ? `${report.duration_min} 分钟` : (conversation.stats?.duration ? `${Math.round(conversation.stats.duration / 60)} 分钟` : meeting.duration || "");
    $("#xy-current-meeting-title").innerHTML = formatMeetingTitle(meeting.title);
    $("#xy-meeting-status").textContent = state.meetingSessionId ? statusLabel(status) : (meeting.status || "已整理");
    $("#xy-meeting-summary").textContent = summary;
    $("#xy-meeting-date").textContent = `${meeting.date || ""} ${meeting.time || ""}`.trim();
    $("#xy-meeting-time").textContent = duration;
    setText("xy-meeting-live-status", meetingStatusText(status, conversation, report));
    const start = $("#xy-meeting-start");
    const complete = $("#xy-meeting-complete");
    if (start) start.disabled = Boolean(state.meetingSessionId);
    if (complete) complete.disabled = !state.meetingSessionId;
  }

  function statusLabel(status) {
    const map = {recording_starting: "启动中", recording: "记录中", summarizing: "整理中", stopping: "收尾中", ready: "已整理", error: "需重试", recording_disabled: "已准备"};
    return map[status] || status || "已准备";
  }

  function meetingStatusText(status, conversation, report) {
    if (report?.error) return report.error;
    if (state.meetingSessionId) return `会议记录进行中：${statusLabel(status)}。`;
    if (conversation?.recording_status === "error") return conversation.last_recording_error || "录音暂不可用，可先查看已有纪要。";
    return "可直接查看已整理内容，也可以连接设备后实时记录。";
  }

  async function refreshConversationState() {
    try {
      const data = await apiJSON("/api/conversation/state", {timeoutMs: 6000});
      liveState = {...(liveState || {}), data: {...stateData(), conversation: data}};
      renderMeeting();
    } catch (_error) {}
    try {
      const data = await apiJSON("/api/meeting/speakers", {timeoutMs: 6000});
      if (data.total) setText("xy-meeting-live-status", `已识别 ${data.total} 位说话人，整理时会保留说话人标签。`);
    } catch (_error) {}
  }

  async function startMeeting() {
    const button = $("#xy-meeting-start");
    if (button) button.disabled = true;
    setText("xy-meeting-live-status", "正在开始会议记录…");
    try {
      const data = await apiJSON("/api/conversation/start", {method: "POST", body: {control_session: true, save_audio: true}, timeoutMs: 15000});
      const sessionId = data.session_id || data.runtime?.session_id || data.state?.session_id || "";
      persistMeetingSession(sessionId);
      showToast(data.recording_state === "starting" ? "会议记录已开始" : "会议已进入记录模式");
      await refreshConversationState();
    } catch (error) {
      setText("xy-meeting-live-status", `会议记录暂不可用：${error.message || "请稍后重试"}`);
      showToast("会议记录暂不可用，已保留已有纪要");
    } finally {
      renderMeeting();
    }
  }

  async function completeMeeting() {
    if (!state.meetingSessionId) return;
    const button = $("#xy-meeting-complete");
    if (button) button.disabled = true;
    setText("xy-meeting-live-status", "正在结束并整理会议…");
    try {
      await apiJSON("/api/meeting/complete", {method: "POST", body: {session_id: state.meetingSessionId}, timeoutMs: 15000});
      persistMeetingSession("");
      showToast("会议整理已提交");
      await refreshConversationState();
    } catch (error) {
      setText("xy-meeting-live-status", `整理暂不可用：${error.message || "请稍后重试"}`);
      showToast("整理失败，已保留当前记录状态");
    } finally {
      renderMeeting();
    }
  }

  function openMeetingDetail() {
    const meeting = seedData.meetings?.currentMeeting || {};
    const body = $("#xy-meeting-dialog-body");
    const tags = (meeting.tags || []).map((tag) => `<span>${escapeHTML(tag)}</span>`).join("");
    body.innerHTML = `<p class="xy-label">会议纪要</p><h2 id="xy-meeting-dialog-title">${escapeHTML(meeting.title || "会议纪要")}</h2><div class="xy-sheet-meta"><span>${escapeHTML(meeting.date || "")} ${escapeHTML(meeting.time || "")}</span><span>${escapeHTML(meeting.duration || "")}</span>${tags}</div>${markdownToHtml(meetingMarkdownText || meeting.minutesMarkdown || "")}`;
    $("#xy-meeting-dialog").showModal();
  }

  function renderCalendar() {
    const grid = $("#xy-calendar-days");
    if (!grid) return;
    const [year, month] = state.calendarMonth.split("-").map(Number);
    $("#xy-calendar-title").textContent = `${year} 年 ${month} 月`;
    grid.innerHTML = "";
    const firstDay = new Date(year, month - 1, 1).getDay();
    const total = new Date(year, month, 0).getDate();
    for (let blank = 0; blank < firstDay; blank += 1) grid.append(document.createElement("span"));
    for (let day = 1; day <= total; day += 1) {
      const key = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(day);
      button.setAttribute("aria-label", `${month}月${day}日`);
      button.classList.toggle("has-record", Boolean(seedData.dailyRecords?.[key]));
      button.classList.toggle("is-selected", key === state.selectedDate);
      button.addEventListener("click", () => {
        if (!seedData.dailyRecords?.[key]) return;
        state.selectedDate = key;
        persist();
        renderRecords();
      });
      grid.append(button);
    }
  }

  function renderDayDetail() {
    const day = currentDay();
    const detail = $("#xy-day-detail");
    if (!detail) return;
    detail.innerHTML = `<p class="xy-label">${escapeHTML(formatDate(day.date || state.selectedDate))}</p><h2>今日主导状态：${escapeHTML(day.mainState || "状态平稳")}</h2><div class="xy-detail-grid"><span>专注状态 <strong>${escapeHTML(day.focusDisplay || "整体比较专注")}</strong></span><span>喝水 <strong>${escapeHTML(`${day.waterCups || "-"} / ${day.waterGoal || 8} 杯`)}</strong></span><span>步数 <strong>${escapeHTML(`${day.steps || "-"} 步`)}</strong></span><span>冥想 <strong>${day.meditation ? "已完成" : "未完成"}</strong></span><span class="wide">会议 <strong>${escapeHTML(day.hadMeeting ? day.meetingTitle : "今天没有会议记录")}</strong></span></div><p>${escapeHTML(day.diary || "")}</p><div class="xy-assistant-reply">${escapeHTML(diaryOverrides[day.date]?.assistantReply || day.assistantReply || "")}</div>`;
    if (day.hadMeeting && day.meetingId) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "xy-inline-link";
      button.textContent = "查看会议纪要";
      button.addEventListener("click", openMeetingDetail);
      detail.append(button);
    }
  }

  function renderRecords() {
    renderCalendar();
    const day = currentDay();
    renderTrend($("[data-trend='records']"), day.emotionTrend || []);
    renderDayDetail();
    renderDiaryHistory();
    renderWeeklyHistory();
  }

  function buildDiaryAssistantReply(dayData, diaryText) {
    if (typeof seedData.buildDiaryAssistantReply === "function") return seedData.buildDiaryAssistantReply(dayData, diaryText);
    return `小屿读到了你今天写下的内容，也看到今天的主导状态是${dayData.mainState || "状态平稳"}。先照顾好此刻，不必把所有事情一次想完。`;
  }

  function openDiaryModal(date = state.selectedDate) {
    const day = seedData.dailyRecords?.[date] || currentDay();
    const override = diaryOverrides[date] || {};
    $("#xy-diary-dialog-title").textContent = `${formatDate(date)} 日记`;
    $("#xy-diary-date").textContent = formatDate(date);
    $("#xy-diary-editor").value = override.diary || day.diary || "";
    $("#xy-diary-reply").textContent = override.assistantReply || day.assistantReply || "";
    $("#xy-diary-dialog").showModal();
  }

  async function saveDiary(event) {
    event.preventDefault();
    const day = currentDay();
    const text = $("#xy-diary-editor").value.trim() || day.diary || "";
    let assistantReply = buildDiaryAssistantReply(day, text);
    try {
      const data = await apiJSON("/api/reflect", {
        method: "POST",
        body: {
          mode: "diary",
          user_text: text,
          emotion: day.mainState || currentEmotionText() || "Neutral",
          user_name: "蛋挞",
          day_summary: {date: state.selectedDate, diary: text, care: buildAssistantMemoryContext().careText},
        },
        timeoutMs: 15000,
      });
      assistantReply = data.reply || data.text || assistantReply;
    } catch (_error) {}
    diaryOverrides[state.selectedDate] = {diary: text, assistantReply};
    persistDiary();
    $("#xy-diary-reply").textContent = assistantReply;
    $("#xy-diary-dialog").close();
    renderRecords();
    showToast("日记已保存，小屿也写下了回应");
  }

  function weekKeyForDate(date) {
    return seedData.dailyRecords?.[date]?.weekKey || "";
  }

  function openWeeklyReportModal(weekKey = weekKeyForDate(state.selectedDate)) {
    const report = seedData.weeklyReports?.[weekKey];
    if (!report) return;
    $("#xy-weekly-dialog-body").innerHTML = `<p class="xy-label">本周周报</p><h2 id="xy-weekly-dialog-title">${escapeHTML(report.title)}</h2><div class="xy-sheet-meta"><span>${escapeHTML(report.rangeLabel)}</span><span>${escapeHTML(report.weekKey)}</span></div><p>${escapeHTML(report.summary)}</p><h3>这一周值得留下的部分</h3><ul>${(report.highlights || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul><h3>小屿的照顾提醒</h3><p>${escapeHTML(report.careSummary || "")}</p><p>${escapeHTML(report.suggestion || "")}</p>`;
    $("#xy-weekly-dialog").showModal();
  }

  async function generateWeeklyReport() {
    const weekKey = weekKeyForDate(state.selectedDate);
    const report = seedData.weeklyReports?.[weekKey];
    const entries = Object.values(seedData.dailyRecords || {})
      .filter((day) => !weekKey || day.weekKey === weekKey)
      .map((day) => ({date: day.date, emotion: day.mainState, content: diaryOverrides[day.date]?.diary || day.diary || ""}));
    const fallback = report?.summary || "这一周你有认真留下自己的状态。下周也可以慢一点，先照顾好睡眠、喝水和走动。";
    try {
      const data = await apiJSON("/api/report/weekly", {
        method: "POST",
        body: {entries, user_name: "蛋挞", week_start: entries.at(-1)?.date || "", week_end: entries[0]?.date || ""},
        timeoutMs: 30000,
      });
      $("#xy-weekly-dialog-body").innerHTML = `<p class="xy-label">本周周报</p><h2 id="xy-weekly-dialog-title">写给这一周的你</h2><p>${escapeHTML(data.content || fallback)}</p>`;
    } catch (_error) {
      $("#xy-weekly-dialog-body").innerHTML = `<p class="xy-label">本周周报</p><h2 id="xy-weekly-dialog-title">${escapeHTML(report?.title || "写给这一周的你")}</h2><p>${escapeHTML(fallback)}</p>`;
    }
    $("#xy-weekly-dialog").showModal();
  }

  function renderDiaryHistory() {
    const container = $("#xy-diary-history");
    if (!container) return;
    const items = Object.values(seedData.dailyRecords || {}).sort((a, b) => b.date.localeCompare(a.date)).slice(0, 5);
    container.innerHTML = items.map((day) => `<button type="button" data-date="${escapeHTML(day.date)}" aria-label="打开 ${escapeHTML(formatDate(day.date))} 日记"><span class="xy-icon-badge xy-amber">${iconUse("xy-book")}</span><span><strong>${escapeHTML(formatDate(day.date))}</strong><small>${escapeHTML(day.mainState)} · ${escapeHTML(truncate(diaryOverrides[day.date]?.diary || day.diary, 34))}</small></span>${iconUse("xy-chevron")}</button>`).join("");
    $$("[data-date]", container).forEach((button) => button.addEventListener("click", () => {
      state.selectedDate = button.dataset.date;
      state.calendarMonth = state.selectedDate.slice(0, 7);
      persist();
      renderRecords();
      openDiaryModal(button.dataset.date);
    }));
  }

  function renderWeeklyHistory() {
    const container = $("#xy-weekly-history");
    if (!container) return;
    const reports = Object.values(seedData.weeklyReports || {}).sort((a, b) => b.weekKey.localeCompare(a.weekKey)).slice(0, 5);
    container.innerHTML = reports.map((report) => `<button type="button" data-week="${escapeHTML(report.weekKey)}"><span class="xy-icon-badge xy-sage">${iconUse("xy-activity")}</span><span><strong>${escapeHTML(report.rangeLabel)}</strong><small>${escapeHTML(truncate(report.summary, 42))}</small></span>${iconUse("xy-chevron")}</button>`).join("");
    $$("[data-week]", container).forEach((button) => button.addEventListener("click", () => openWeeklyReportModal(button.dataset.week)));
  }

  function shiftMonth(delta) {
    const [year, month] = state.calendarMonth.split("-").map(Number);
    const next = new Date(year, month - 1 + delta, 1);
    const key = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}`;
    if (!["2026-06", "2026-07"].includes(key)) return;
    state.calendarMonth = key;
    const firstRecord = Object.keys(seedData.dailyRecords || {}).find((date) => date.startsWith(key));
    if (firstRecord) state.selectedDate = key === "2026-07" ? "2026-07-04" : firstRecord;
    persist();
    renderRecords();
  }

  async function refreshDeviceState() {
    try {
      systemHealth = await apiJSON("/api/system/health", {timeoutMs: 6000});
    } catch (_error) {
      systemHealth = null;
    }
    try {
      voiceState = await apiJSON("/api/voice/state", {timeoutMs: 6000});
    } catch (_error) {
      voiceState = null;
    }
    renderDevice();
  }

  function renderDevice() {
    const status = systemHealth?.status || (liveState ? "ready" : "degraded");
    const online = $("#xy-device-online");
    if (online) {
      online.classList.toggle("is-offline", status !== "ready");
      online.innerHTML = `<i></i>${status === "ready" ? "在线" : status === "degraded" ? "部分可用" : "离线"}`;
    }
    const components = systemHealth?.components || {};
    const readyCount = Object.values(components).filter((item) => item?.status === "ready").length;
    const totalCount = Object.keys(components).length;
    setText("xy-device-summary", totalCount ? `设备状态 ${readyCount}/${totalCount} 项可用` : "正在等待设备同步");
    const playback = voiceState?.playback || {};
    setText("xy-voice-summary", playback.state ? `语音播放：${playback.state}` : "语音可用时会自动播放小屿回应");
  }

  function applyLiveState(snapshot) {
    liveState = snapshot;
    renderHome();
    renderMeeting();
    renderDevice();
  }

  function startRealtime() {
    if (!("WebSocket" in window) || location.protocol === "file:") {
      startPolling();
      return;
    }
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    try {
      ws = new WebSocket(`${scheme}://${location.host}/ws`);
      ws.onmessage = (event) => {
        try {
          applyLiveState(JSON.parse(event.data));
        } catch (_error) {}
      };
      ws.onclose = startPolling;
      ws.onerror = startPolling;
    } catch (_error) {
      startPolling();
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(async () => {
      try {
        applyLiveState(await apiJSON("/api/state", {timeoutMs: 5000}));
      } catch (_error) {}
      if (state.activePage === "meeting") refreshConversationState();
    }, 3000);
  }

  async function stopVoice(reason = "home_button") {
    try {
      await apiJSON("/api/voice/stop", {method: "POST", body: {reason}, timeoutMs: 6000});
      setText("xy-voice-status", "语音已停止");
      showToast("语音已停止");
    } catch (_error) {
      setText("xy-voice-status", "语音停止请求暂不可用");
    }
  }

  async function sendVoiceBlob(blob) {
    setText("xy-voice-status", "小屿正在听…");
    const qs = new URLSearchParams({user_name: "蛋挞", context: buildAssistantMemoryContext().trendText || ""});
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 65000);
    try {
      const response = await fetch(`/api/voice/chat?${qs}`, {
        method: "POST",
        headers: {"Content-Type": blob.type || "audio/webm"},
        body: blob,
        signal: controller.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "voice_chat_failed");
      if (data.transcript) appendChat(data.transcript, "user");
      appendChat(data.reply || "小屿听到了。", "assistant");
      if (data.audio_url) new Audio(data.audio_url).play().catch(() => {});
      setText("xy-voice-status", "语音回复完成");
    } catch (_error) {
      setText("xy-voice-status", "语音暂不可用，可以继续打字给小屿");
      showToast("语音暂不可用，文字陪伴仍可使用");
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function toggleVoiceRecording() {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setText("xy-voice-status", "当前浏览器不支持录音，可以继续打字");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      voiceChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = (event) => {
        if (event.data?.size) voiceChunks.push(event.data);
      };
      mediaRecorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        $(".xy-voice-row")?.classList.remove("is-recording");
        if (voiceChunks.length) sendVoiceBlob(new Blob(voiceChunks, {type: mediaRecorder.mimeType || "audio/webm"}));
      };
      mediaRecorder.start();
      $(".xy-voice-row")?.classList.add("is-recording");
      setText("xy-voice-status", "正在录音，再点一次发送");
    } catch (_error) {
      setText("xy-voice-status", "无法打开麦克风，可以继续打字");
    }
  }

  function bind() {
    $$("[data-go]").forEach((button) => button.addEventListener("click", () => goTo(button.dataset.go)));
    $$("[data-toast]").forEach((button) => button.addEventListener("click", () => showToast(button.dataset.toast)));
    $$(".xy-prompt-chips button").forEach((button) => button.addEventListener("click", () => sendChat(button.textContent)));
    $("#xy-chat-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("#xy-chat-input");
      sendChat(input.value);
      input.value = "";
    });
    $("#xy-meeting-toggle")?.addEventListener("click", openMeetingDetail);
    $("#xy-meeting-start")?.addEventListener("click", startMeeting);
    $("#xy-meeting-complete")?.addEventListener("click", completeMeeting);
    $("#xy-voice-record")?.addEventListener("click", toggleVoiceRecording);
    $("#xy-voice-stop")?.addEventListener("click", () => stopVoice("home_button"));
    $$("[data-meeting-detail]").forEach((button) => button.addEventListener("click", openMeetingDetail));
    $("#xy-calendar-prev")?.addEventListener("click", () => shiftMonth(-1));
    $("#xy-calendar-next")?.addEventListener("click", () => shiftMonth(1));
    $("#xy-open-diary")?.addEventListener("click", () => openDiaryModal());
    $("#xy-diary-link")?.addEventListener("click", () => openDiaryModal());
    $("#xy-weekly-link")?.addEventListener("click", () => generateWeeklyReport());
    $("#xy-diary-form")?.addEventListener("submit", saveDiary);
    $$("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog")?.close()));
    $$("dialog.xy-sheet").forEach((dialog) => dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    }));
  }

  async function init() {
    if (!seedData.currentDate) return;
    state.selectedDate = state.selectedDate || seedData.currentDate;
    state.calendarMonth = state.calendarMonth || "2026-07";
    renderHome();
    resetInitialChat();
    renderMeeting();
    renderRecords();
    renderDevice();
    bind();
    await loadMeetingMarkdown();
    startRealtime();
    refreshDeviceState();
    refreshConversationState();
    window.setInterval(refreshDeviceState, 15000);
    goTo(["home", "companion", "meeting", "records", "mine"].includes(state.activePage) ? state.activePage : "home");
  }

  init();
})();
