(() => {
  "use strict";

  const STORAGE_KEY = "xinyu.preview.v1";
  const DIARY_KEY = "xinyu.actual.diary.v1";
  const MEETING_SESSION_KEY = "xinyu.product.meeting_session.v1";
  const MEETING_NOTES_KEY = "xinyu.meeting.notes.v1";
  const MEMORY_NOTES_KEY = "xinyu.memory.notes.v1";
  const CLOUD_REFLECT_KEY = "xinyu.cloud_reflect_enabled.v1";
  const SELFCARE_KEY = "xinyu.selfcare.v1";
  const seedData = window.XINYU_PREVIEW_DATA || {};
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  let toastTimer = 0;
  let meetingMarkdownText = seedData.meetings?.currentMeeting?.minutesMarkdown || "";
  let liveState = null;
  let systemHealth = null;
  let voiceState = null;
  let announceSettings = null;
  let meetingCompletionPending = false;
  let ws = null;
  let pollTimer = 0;
  let mediaRecorder = null;
  let voiceChunks = [];
  let diaryTypeTimer = 0;
  let chatHistoryLoading = false;
  const todayKey = dateKey(new Date());
  const seedCurrentDate = seedData.currentDate || "2026-07-04";
  const daySummaryCache = {};

  function loadJSON(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return value && typeof value === "object" ? value : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function loadArray(key) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(value) ? value : [];
    } catch (_error) {
      return [];
    }
  }

  const state = loadJSON(STORAGE_KEY, {activePage: "home", selectedDate: todayKey, calendarMonth: todayKey.slice(0, 7)});
  const diaryOverrides = loadJSON(DIARY_KEY, {});
  const selfcareRecords = loadJSON(SELFCARE_KEY, {});
  const meetingNotes = loadArray(MEETING_NOTES_KEY);
  const memoryNotes = loadArray(MEMORY_NOTES_KEY);
  let cloudReflectEnabled = localStorage.getItem(CLOUD_REFLECT_KEY) !== "false";
  state.meetingSessionId = state.meetingSessionId || localStorage.getItem(MEETING_SESSION_KEY) || "";

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      activePage: state.activePage,
      selectedDate: state.selectedDate,
      calendarMonth: state.calendarMonth,
      meetingSessionId: state.meetingSessionId || "",
      activeConversationId: state.activeConversationId || "",
    }));
  }

  function persistDiary() {
    localStorage.setItem(DIARY_KEY, JSON.stringify(diaryOverrides));
  }

  function persistSelfcare() {
    localStorage.setItem(SELFCARE_KEY, JSON.stringify(selfcareRecords));
  }

  function persistMeetingNotes() {
    localStorage.setItem(MEETING_NOTES_KEY, JSON.stringify(meetingNotes.slice(-30)));
  }

  function persistMemoryNotes() {
    localStorage.setItem(MEMORY_NOTES_KEY, JSON.stringify(memoryNotes.slice(-60)));
  }

  function persistCloudReflect() {
    localStorage.setItem(CLOUD_REFLECT_KEY, cloudReflectEnabled ? "true" : "false");
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

  function emptyDay(date = state.selectedDate) {
    return {
      date,
      emotionTrend: [],
      focusScore: null,
      focusDisplay: "今日还没有观察记录",
      mainState: date === todayKey ? "等待今日状态" : "暂无记录",
      steps: null,
      waterCups: null,
      waterGoal: 8,
      meditation: false,
      hadMeeting: false,
      meetingId: "",
      meetingTitle: "",
      diary: "",
      assistantReply: "",
      weekKey: weekKeyFromDate(date),
      tags: [],
      moodId: "calm",
      source: "empty",
    };
  }

  function weekKeyFromDate(dateText) {
    const date = parseDate(dateText);
    const first = new Date(Date.UTC(date.getFullYear(), 0, 1));
    const day = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNumber = Math.floor((day - first) / 86400000) + 1;
    return `${date.getFullYear()}-W${String(Math.ceil(dayNumber / 7)).padStart(2, "0")}`;
  }

  function weekBounds(dateText = state.selectedDate) {
    const date = parseDate(dateText);
    const day = date.getDay();
    const mondayOffset = day === 0 ? -6 : 1 - day;
    const start = new Date(date);
    start.setDate(date.getDate() + mondayOffset);
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    return {start: dateKey(start), end: dateKey(end)};
  }

  function datesBetween(startKey, endKey) {
    const out = [];
    const cur = parseDate(startKey);
    const end = parseDate(endKey);
    while (cur <= end && out.length < 14) {
      out.push(dateKey(cur));
      cur.setDate(cur.getDate() + 1);
    }
    return out;
  }

  function daySummaryFor(date) {
    return daySummaryCache[date] || null;
  }

  function selfcareFor(date = todayKey) {
    const raw = selfcareRecords[date] || {};
    return {
      waterCups: Number.isFinite(Number(raw.waterCups)) ? Math.max(0, Number(raw.waterCups)) : null,
      steps: Number.isFinite(Number(raw.steps)) ? Math.max(0, Math.round(Number(raw.steps))) : null,
      meditation: Boolean(raw.meditation),
      updatedAt: raw.updatedAt || "",
    };
  }

  function saveSelfcare(date, patch) {
    const prev = selfcareRecords[date] || {};
    selfcareRecords[date] = {...prev, ...patch, updatedAt: new Date().toISOString()};
    persistSelfcare();
  }

  function monthBounds(monthKey = state.calendarMonth) {
    const [year, month] = monthKey.split("-").map(Number);
    const start = `${year}-${String(month).padStart(2, "0")}-01`;
    const endDate = new Date(year, month, 0);
    return {start, end: dateKey(endDate)};
  }

  async function loadDaySummaryRange(monthKey = state.calendarMonth) {
    const {start, end} = monthBounds(monthKey);
    try {
      const data = await apiJSON(`/api/day_summary/range?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`, {timeoutMs: 6000});
      (data.days || []).forEach((day) => {
        if (day?.date) daySummaryCache[day.date] = day;
      });
    } catch (_error) {}
  }

  function emotionZh(emotion) {
    return {
      Happiness: "快乐", Happy: "快乐", Neutral: "平静", Sadness: "低落",
      Sad: "低落", Anger: "烦躁", Angry: "烦躁", Fear: "不安",
      Surprise: "惊讶", Disgust: "不适", Contempt: "疏离",
    }[emotion] || emotion || "";
  }

  function summaryTrend(summary) {
    const hours = Array.isArray(summary?.hours) ? summary.hours : [];
    return hours
      .filter((hour) => hour.presence_sec || hour.dominant_emotion || hour.attention_avg != null)
      .slice(-5)
      .map((hour) => ({
        time: `${String(hour.hour).padStart(2, "0")}:00`,
        mood: hour.dominant_emotion === "Happiness" ? "bright" : hour.attention_avg >= 70 ? "focused" : hour.dominant_emotion ? "calm" : "clear",
        display: emotionZh(hour.dominant_emotion) || (hour.attention_avg >= 70 ? "专注" : "观察"),
      }));
  }

  function mergeDayData(date) {
    const seed = seedData.dailyRecords?.[date] || null;
    const summary = daySummaryFor(date);
    const override = diaryOverrides[date] || {};
    const selfcare = selfcareFor(date);
    const base = {...emptyDay(date), ...(seed || {})};
    if (summary?.available) {
      const dom = emotionZh(summary.dominant_emotion);
      base.mainState = dom ? `${dom}为主` : "今日有观察记录";
      base.focusDisplay = summary.attention_avg != null ? `专注均值 ${Math.round(Number(summary.attention_avg))}` : base.focusDisplay;
      base.emotionTrend = summaryTrend(summary);
      base.presenceMin = summary.presence_min;
      base.observedSummary = summary;
      base.source = seed ? "seed+summary" : "summary";
    }
    if (override.diary != null) base.diary = override.diary;
    if (override.assistantReply != null) base.assistantReply = override.assistantReply;
    if (override.assistantMeta) base.assistantMeta = override.assistantMeta;
    if (selfcare.waterCups != null) base.waterCups = selfcare.waterCups;
    if (selfcare.steps != null) base.steps = selfcare.steps;
    if (selfcare.updatedAt || selfcare.meditation) base.meditation = selfcare.meditation;
    base.selfcare = selfcare;
    return base;
  }

  function currentDay() {
    return mergeDayData(state.selectedDate || todayKey);
  }

  function todayMemoryDay() {
    return mergeDayData(todayKey);
  }

  function homeFallbackAdvice(day, {faceVisible = false, emotionText = ""} = {}) {
    if (faceVisible && emotionText) return `我先按你现在的状态陪着，不急着下判断。可以先写一句最真实的感受，哪怕很短也算数。`;
    if (day.selfcare?.waterCups == null && day.selfcare?.steps == null && !day.selfcare?.updatedAt) {
      return "今天还没留下身体和状态记录。可以先点一下喝水、输入步数，或者只写一句日记，小屿会从这一点开始陪你整理。";
    }
    if ((day.waterCups || 0) < 4) return `今天已经记了${day.waterCups || 0}杯水。不是任务打卡，只是给身体一个小信号：现在可以再补一杯。`;
    if (day.steps == null) return "今天的活动还没有记录。如果愿意，点一下活动卡片填个大概步数就好，不需要特别精确。";
    if (!day.meditation) return "今天已经有一点记录了。冥想不一定要很正式，哪怕只是安静坐两分钟，也可以算是给自己留了空隙。";
    return "今天已经留下了几件真实的小照顾。小屿会把这些记在今天，而不是用历史记录替你定义现在。";
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

  function reducedMotion() {
    return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  }

  function privacyLabel(value) {
    if (value) return String(value);
    return cloudReflectEnabled ? "云端增强已开启" : "仅使用本地回复";
  }

  function seedMemoryNotesFromPreview() {
    const memory = seedData.assistantMemory || {};
    const seeded = new Set(memoryNotes.filter((note) => note?.source === "preview_seed").map((note) => note.id));
    const additions = [];
    (memory.recentDiary || []).slice(0, 5).forEach((item) => {
      if (!item?.date || seeded.has(`preview_diary_${item.date}`)) return;
      additions.push({
        id: `preview_diary_${item.date}`,
        date: item.date,
        content: `演示预览：${item.date} 的日记里，用户写到“${truncate(item.diary || "", 70)}”，当天状态是${item.mainState || "状态平稳"}。`,
        source: "preview_seed",
        created_at: new Date().toISOString(),
      });
    });
    const report = memory.currentWeeklyReport;
    if (report?.weekKey && !seeded.has(`preview_week_${report.weekKey}`)) {
      additions.push({
        id: `preview_week_${report.weekKey}`,
        date: memory.currentDate || seedData.currentDate || "",
        content: `演示预览：${report.rangeLabel || report.weekKey} 的周报提到，${truncate(report.summary || "", 90)}`,
        source: "preview_seed",
        created_at: new Date().toISOString(),
      });
    }
    if (!additions.length) return;
    memoryNotes.push(...additions);
    persistMemoryNotes();
  }

  function buildReflectMemoryContext(memoryContext = buildAssistantMemoryContext()) {
    const confirmed = memoryNotes.filter((note) => note?.source !== "preview_seed").slice(-8);
    const preview = memoryNotes.filter((note) => note?.source === "preview_seed").slice(-6);
    return {
      confirmed_notes: confirmed.map((note) => ({
        date: note.date || "",
        content: truncate(note.content || "", 110),
      })),
      preview_notes: preview.map((note) => ({
        date: note.date || "",
        content: truncate(note.content || "", 120),
      })),
      recent_diary: (memoryContext.recentDiary || []).slice(0, 5).map((item) => ({
        date: item.date || "",
        main_state: item.mainState || "",
        excerpt: truncate(item.diary || "", 90),
        assistant_reply: truncate(item.assistantReply || "", 80),
      })),
      weekly_summary: truncate(memoryContext.currentWeeklyReport?.summary || "", 220),
      current_meeting: truncate(memoryContext.currentMeeting?.summary || "", 160),
      care_suggestion: truncate(memoryContext.careSuggestion || "", 140),
    };
  }

  function normalizeMemoryCandidate(data, diaryText, replyText) {
    const direct = data?.memory_candidate || data?.memoryCandidate || data?.memory_suggestion?.candidate || data?.memorySuggestion?.candidate;
    if (direct) return truncate(direct, 120);
    const text = String(diaryText || "").trim();
    if (text) return `用户希望小屿记得：${truncate(text, 54)}`;
    return replyText ? `用户保存了一篇日记，小屿的回应是：${truncate(replyText, 48)}` : "";
  }

  function clearDiaryTyping() {
    if (diaryTypeTimer) window.clearTimeout(diaryTypeTimer);
    diaryTypeTimer = 0;
  }

  function typeDiaryReply(text) {
    const el = $("#xy-diary-reply");
    if (!el) return;
    clearDiaryTyping();
    const full = String(text || "");
    if (!full || reducedMotion()) {
      el.textContent = full;
      return;
    }
    el.textContent = "";
    let index = 0;
    const step = () => {
      index += 1;
      el.textContent = full.slice(0, index);
      if (index < full.length) diaryTypeTimer = window.setTimeout(step, 34);
      else diaryTypeTimer = 0;
    };
    step();
  }

  function setDiaryLetterState({reply = "", status = "小屿的回信", privacy = "", loading = false, visible = true, animate = false, candidate = ""} = {}) {
    const letter = $("#xy-diary-letter");
    const actions = $("#xy-memory-actions");
    const editor = $("#xy-memory-editor");
    if (letter) {
      const shouldShow = Boolean(visible || reply || loading);
      letter.hidden = !shouldShow;
      letter.classList.toggle("is-visible", shouldShow);
      letter.classList.toggle("is-loading", Boolean(loading));
    }
    setText("xy-diary-reply-status", status);
    setText("xy-diary-privacy", privacyLabel(privacy));
    if (animate) typeDiaryReply(reply);
    else {
      clearDiaryTyping();
      setText("xy-diary-reply", reply);
    }
    if (actions) actions.hidden = !candidate || loading;
    if (editor) editor.hidden = true;
    if (candidate) {
      const start = $("#xy-memory-start");
      if (start) start.dataset.candidate = candidate;
    }
  }

  function renderCloudReflectSettings() {
    const input = $("#xy-cloud-reflect-enabled");
    if (input) input.checked = cloudReflectEnabled;
    setText("xy-cloud-reflect-status", cloudReflectEnabled ? "开启后，小屿会用更细腻的方式回应日记。" : "关闭后仍可保存日记，回复会更简单。");
  }

  function announceDefaults() {
    return {
      enabled: false,
      sedentary_minutes: 45,
      snooze_minutes: 10,
      eye_fatigue_enabled: true,
      meeting_status_enabled: true,
      target: "recamera_speaker",
    };
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
    if (page === "companion") resetInitialChat();
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
    if (heroEmotion) heroEmotion.textContent = faceVisible && emotionText ? emotionText : (day.mainState || "等待今日状态");
    if (heroCopy) {
      heroCopy.textContent = faceVisible
        ? `小屿看到你现在${emotionText || "有一些状态变化"}，${focusText ? `专注状态是${focusText}。` : "先按自己的节奏来。"}`
        : (day.observedSummary?.available ? "今天已经有一些观察记录，小屿会把它们轻轻放在日记旁边作参考。" : "今天还没有实时观察记录；历史记录会作为参考，但不会替你定义今天。");
    }
    if (assistantNote) {
      assistantNote.innerHTML = `<small>小屿回应</small>${control.active_feature === "meeting_recording" ? "会议记录中，情绪判断先放轻。" : homeFallbackAdvice(day, {faceVisible, emotionText})}`;
    }
    const water = $("#xy-water-add strong");
    const steps = $("#xy-steps-edit strong");
    const meditation = $("#xy-meditation-toggle strong");
    if (water) water.textContent = day.waterCups == null ? "未记录" : `${day.waterCups} / ${day.waterGoal || 8} 杯`;
    if (steps) steps.textContent = day.steps == null ? "未记录" : `${day.steps} 步`;
    if (meditation) meditation.textContent = day.meditation ? "已完成" : "未完成";
    const advice = $(".xy-advice > div p:last-child");
    if (advice) advice.textContent = homeFallbackAdvice(day, {faceVisible, emotionText});
    $("[data-trend='home']") && renderTrend($("[data-trend='home']"), day.emotionTrend || []);
  }

  function addWaterCup() {
    const care = selfcareFor(todayKey);
    const next = Math.min(20, (care.waterCups || 0) + 1);
    saveSelfcare(todayKey, {waterCups: next});
    renderHome();
    renderCompanionPrompts();
    renderRecords();
    showToast(`已记录喝水 ${next} 杯`);
  }

  function editSteps() {
    const care = selfcareFor(todayKey);
    const value = window.prompt("今天大概走了多少步？", care.steps == null ? "" : String(care.steps));
    if (value == null) return;
    const steps = Math.round(Number(value));
    if (!Number.isFinite(steps) || steps < 0) {
      showToast("步数需要填一个非负数字");
      return;
    }
    saveSelfcare(todayKey, {steps});
    renderHome();
    renderCompanionPrompts();
    renderRecords();
    showToast("今日步数已记录");
  }

  function toggleMeditation() {
    const care = selfcareFor(todayKey);
    saveSelfcare(todayKey, {meditation: !care.meditation});
    renderHome();
    renderCompanionPrompts();
    renderRecords();
    showToast(!care.meditation ? "已记录今天的安静时间" : "已取消冥想完成标记");
  }

  function pushUniquePrompt(prompts, text) {
    const value = String(text || "").replace(/\s+/g, " ").trim();
    if (!value || value.length < 4) return;
    if (prompts.some((item) => item === value)) return;
    prompts.push(truncate(value, 34));
  }

  function buildCompanionPrompts(memoryContext = buildAssistantMemoryContext()) {
    const day = memoryContext.currentDay || todayMemoryDay();
    const confirmed = memoryNotes.filter((note) => note?.source !== "preview_seed" && note?.content).slice(-3);
    const prompts = [];
    if (day.diary) pushUniquePrompt(prompts, `接着聊聊我日记里写的：${truncate(day.diary, 18)}`);
    if (memoryContext.meetingTitle) pushUniquePrompt(prompts, `帮我消化一下${truncate(memoryContext.meetingTitle, 16)}`);
    if (confirmed.length) pushUniquePrompt(prompts, `围绕你记得的“${truncate(confirmed[confirmed.length - 1].content, 16)}”陪我聊聊`);
    if (memoryContext.trendText) pushUniquePrompt(prompts, `把今天这些状态变化讲给我听`);
    if ((day.waterCups || 0) < 4) pushUniquePrompt(prompts, "用很轻的方式提醒我照顾身体");
    if (day.steps == null) pushUniquePrompt(prompts, "帮我把今天的工作和身体状态分开看");
    if (day.meditation) pushUniquePrompt(prompts, "陪我回顾刚才那段安静时间");
    pushUniquePrompt(prompts, "我现在只想先说一句，不想被建议");
    pushUniquePrompt(prompts, "帮我把脑子里的事分成三小块");
    pushUniquePrompt(prompts, "如果我有点累，先陪我慢下来");
    pushUniquePrompt(prompts, "把这一刻记成一条温柔的心情记录");
    return prompts.slice(0, 4);
  }

  function renderCompanionPrompts() {
    const container = $(".xy-prompt-chips");
    if (!container) return;
    const prompts = buildCompanionPrompts();
    container.innerHTML = prompts
      .map((prompt) => `<button type="button" data-chat-prompt="${escapeHTML(prompt)}">${escapeHTML(prompt)}</button>`)
      .join("");
  }

  function buildAssistantMemoryContext() {
    const memory = seedData.assistantMemory || {};
    const day = todayMemoryDay();
    return {
      ...memory,
      currentDay: day,
      trendText: (day.emotionTrend || []).map((point) => `${point.time}${point.display}`).join("，"),
      careText: `喝水${day.waterCups ?? "未记录"} / ${day.waterGoal || 8}杯，步数${day.steps ?? "未记录"}，冥想${day.meditation ? "已完成" : "未完成"}`,
      meetingTitle: day.meetingTitle || memory.currentMeeting?.title || "",
      diaryText: day.diary || "",
    };
  }

  function buildXiaoyuReply(userInput, memoryContext = buildAssistantMemoryContext()) {
    const text = String(userInput || "").trim();
    const quick = memoryContext.quickReplies || {};
    if (quick[text]) return quick[text];
    if (text.startsWith("接着聊聊我日记里写的")) {
      const excerpt = text.split("：").slice(1).join("：").trim() || memoryContext.diaryText || "那一段日记";
      return `我会先贴着这段日记来陪你，不把它硬拽回任务清单。你写到“${truncate(excerpt, 34)}”，这里面也许有轻松、消耗，或者一些还没说完的东西。我们可以先停在这一句。`;
    }
    if (text.startsWith("帮我消化一下")) {
      const topic = text.replace(/^帮我消化一下/, "").trim() || memoryContext.meetingTitle || "这件事";
      return `${truncate(topic, 34)}听起来信息量不小。小屿先不急着总结成待办，我们可以先分清：哪一部分已经结束，哪一部分还压在心里。`;
    }
    if (text.startsWith("围绕你记得的")) {
      const remembered = text.match(/“(.+?)”/)?.[1] || "这件被记住的事";
      return `我记得的是“${truncate(remembered, 32)}”。这次我会把它当作背景，而不是拿它定义你现在。你可以说说它今天又怎么影响到你了。`;
    }
    if (text.includes("状态变化")) {
      return `我可以陪你把今天的状态慢慢摊开看。${memoryContext.trendText ? `记录里有${memoryContext.trendText}，` : ""}但最重要的还是你此刻怎么感受它。`;
    }
    if (text.includes("不想被建议")) return "好，那我先不急着给建议。你可以只放下一句话，哪怕它不完整，我也会先按你说的来接住。";
    if (text.includes("三小块")) return "可以。我们先把脑子里的东西分成三小块：正在发生的、真正担心的、现在能先放一放的。你先随便说，我来陪你分。";
    if (text.includes("照顾身体")) return `那就轻一点来。${memoryContext.careText ? `今天记录里是${memoryContext.careText}。` : ""}先不把照顾变成任务，只选一个身体现在最容易接受的小动作。`;
    if (text.includes("心情记录")) return "我可以帮你把这一刻记得柔软一点：不是给情绪下结论，而是留下它来过的痕迹。你可以先说最真实的一句。";
    if (text.includes("累") || text.includes("疲惫")) return quick["我今天有点累"] || memoryContext.careSuggestion;
    if (text.includes("整理")) return quick["帮我整理一下今天"] || memoryContext.careSuggestion;
    if (text.includes("放松") || text.includes("休息")) return quick["给我一些放松建议"] || memoryContext.careSuggestion;
    if (text.includes("情绪") || text.includes("记录")) return quick["记录一下我的情绪"] || memoryContext.careSuggestion;
    return `小屿会先按你说的来理解。今天的状态里有${memoryContext.trendText || "一些起伏"}，也处理了${memoryContext.meetingTitle || "几件需要整理的事"}。如果愿意，可以从最想放下的一件事慢慢说。`;
  }

  function buildWorkContext(memoryContext = buildAssistantMemoryContext()) {
    const day = memoryContext.currentDay || todayMemoryDay();
    const meeting = memoryContext.currentMeeting || {};
    const notes = meetingNotes.slice(-5).map((note) => ({
      title: note.title || "",
      summary: truncate(note.summary || note.diary || "", 120),
      date: note.date || note.created_at || "",
    }));
    return {
      current_meeting_title: day.meetingTitle || meeting.title || "",
      current_meeting_summary: truncate(meeting.summary || day.meetingSummary || "", 180),
      recent_meeting_notes: notes,
    };
  }

  function buildChatDaySummary(memoryContext = buildAssistantMemoryContext()) {
    const day = memoryContext.currentDay || todayMemoryDay();
    return {
      date: todayKey,
      main_state: day.mainState || "",
      trend_text: memoryContext.trendText || "",
      care_text: memoryContext.careText || "",
      selfcare: selfcareFor(todayKey),
      observed: daySummaryFor(todayKey),
    };
  }

  function buildLLMPayload(message, memoryContext = buildAssistantMemoryContext()) {
    const memoryPayload = buildReflectMemoryContext(memoryContext);
    const dayPayload = buildChatDaySummary(memoryContext);
    const workPayload = buildWorkContext(memoryContext);
    return {
      message,
      conversation_id: state.activeConversationId || "",
      emotion: memoryContext.currentDay?.mainState || "有点疲惫",
      diary_text: memoryContext.diaryText || "",
      user_name: "蛋挞",
      day_summary: dayPayload,
      memory_context: memoryPayload,
      selfcare: dayPayload.selfcare,
      work_context: workPayload,
      context: [
        "用户本轮自述优先；今日状态、会议、日记和周报只是辅助上下文。",
        `当前用户点击/输入：${message}`,
        `今日情绪趋势：${memoryContext.trendText}`,
        `今日自我照顾：${memoryContext.careText}`,
        `今日会议：${memoryContext.meetingTitle}`,
        `近日日记：${(memoryContext.recentDiary || []).map((item) => item.diary).join(" / ")}`,
        `用户确认记忆：${memoryPayload.confirmed_notes.map((item) => item.content).join(" / ")}`,
        `本周周报：${memoryContext.currentWeeklyReport?.summary || ""}`,
        "回复保持40至90个中文字符，温和克制，不诊断，不提模型、概率或识别过程。",
      ].filter(Boolean).join("；").slice(0, 1100),
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
    const hasContext = ["今天", "日记", "记得", "喝水", "步数", "冥想", "会议", "预算", "活动", "压力", "放松", "平和", "累", "疲惫"].some((token) => value.includes(token));
    return hasContext || value.length >= Math.min(local.length, 42);
  }

  function replyFitsPromptIntent(message, reply) {
    const prompt = String(message || "");
    const value = String(reply || "");
    if (prompt.startsWith("接着聊聊我日记里写的")) {
      const meetingOnly = ["会议", "预算申报", "活动规划", "待办"].some((token) => value.includes(token));
      const diaryLike = ["日记", "写到", "这段", "这一句", "感受", "朋友", "吃饭", "逛街"].some((token) => value.includes(token));
      return !meetingOnly || diaryLike;
    }
    if (prompt.startsWith("围绕你记得的")) return ["记得", "背景", "影响", "这件事"].some((token) => value.includes(token));
    if (prompt.includes("不想被建议")) return !["建议你", "可以尝试", "首先", "第一"].some((token) => value.includes(token));
    return true;
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
      if (body.conversation_id) {
        state.activeConversationId = body.conversation_id;
        persist();
      }
      return {
        reply: shouldUseLLMReply(reply, fallback) && replyFitsPromptIntent(message, reply) ? reply : fallback,
        conversationId: body.conversation_id || state.activeConversationId || "",
      };
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

  function renderChatHistory(messages = [], conversation = null) {
    const thread = $("#xy-chat");
    if (!thread) return false;
    const safeMessages = messages
      .filter((item) => ["user", "assistant"].includes(item?.role) && String(item.content || "").trim())
      .slice(-30);
    if (!safeMessages.length) return false;
    thread.innerHTML = "";
    safeMessages.forEach((item) => appendChat(item.content, item.role));
    thread.dataset.ready = "true";
    thread.dataset.historyLoaded = "true";
    if (conversation?.id) {
      state.activeConversationId = conversation.id;
      persist();
    }
    renderCompanionPrompts();
    return true;
  }

  function buildInitialCompanionMessage() {
    const memory = buildAssistantMemoryContext();
    const day = memory.currentDay || todayMemoryDay();
    const confirmed = memoryNotes.filter((note) => note?.source !== "preview_seed").slice(-2);
    const pieces = [];
    if (day.diary) pieces.push(`今天的日记里，你写到“${truncate(day.diary, 42)}”`);
    if (memory.trendText) pieces.push(`今天状态有${memory.trendText}`);
    if (memory.meetingTitle) pieces.push(`还处理了${memory.meetingTitle}`);
    if (confirmed.length) pieces.push(`我也记得：${truncate(confirmed[confirmed.length - 1].content, 42)}`);
    if (pieces.length) return `${pieces.slice(0, 2).join("，")}。现在不用急着整理完整，先从最占心里的那一点说起就好。`;
    if (day.selfcare?.updatedAt) return `我看到今天已经留下了一些照顾自己的记录。这里可以慢一点，说感受、说工作，或者只是把乱糟糟的一句放下来。`;
    return "我在这里。今天还没有太多记录也没关系，你可以从一句很短的话开始，累、烦、开心、空白，都可以。";
  }

  async function ensureConversation({fresh = false} = {}) {
    if (!fresh && state.activeConversationId) return state.activeConversationId;
    const data = await apiJSON("/api/conversations", {method: "POST", body: {category: "general_chat"}, timeoutMs: 6000});
    const id = data.conversation?.id || "";
    if (id) {
      state.activeConversationId = id;
      persist();
    }
    return id;
  }

  async function persistConversationTurn(role, content) {
    const text = String(content || "").trim();
    if (!text) return;
    try {
      const conversationId = await ensureConversation();
      if (!conversationId) return;
      await apiJSON(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, {
        method: "POST",
        body: {role, content: text},
        timeoutMs: 6000,
      });
    } catch (_error) {}
  }

  async function loadConversation(conversationId) {
    if (!conversationId) return false;
    try {
      const data = await apiJSON(`/api/conversations/${encodeURIComponent(conversationId)}`, {timeoutMs: 6000});
      return renderChatHistory(data.messages || [], data.conversation || null);
    } catch (_error) {
      return false;
    }
  }

  async function resetInitialChat({fresh = false} = {}) {
    const thread = $("#xy-chat");
    if (!thread || thread.dataset.ready || chatHistoryLoading) return;
    chatHistoryLoading = true;
    try {
      const conversationId = state.activeConversationId || await ensureConversation({fresh});
      if (!fresh && await loadConversation(conversationId)) return;
    } catch (_error) {
      // Local opening remains available when the service is offline.
    } finally {
      chatHistoryLoading = false;
    }
    thread.innerHTML = "";
    appendChat(buildInitialCompanionMessage(), "assistant");
    thread.dataset.ready = "true";
  }

  async function startNewConversation() {
    const thread = $("#xy-chat");
    if (thread) {
      thread.dataset.ready = "";
      thread.dataset.historyLoaded = "";
      thread.innerHTML = "";
    }
    await ensureConversation({fresh: true});
    await resetInitialChat({fresh: true});
    await refreshConversationList();
    showToast("已开始新一轮对话");
  }

  function memoryCandidateFromChat(message, reply = "") {
    const text = String(message || "").trim();
    if (text.length < 6) return "";
    const memoryTokens = ["记住", "别忘", "以后", "最近", "这周", "工作", "项目", "会议", "预算", "压力", "喜欢", "不喜欢", "希望"];
    if (!memoryTokens.some((token) => text.includes(token))) return "";
    const cleaned = text.replace(/^(请|帮我|可以)?(记住|别忘了?)[:：，,\s]*/g, "");
    return `用户希望小屿记得：${truncate(cleaned || reply || text, 70)}`;
  }

  function addChatMemoryAction(target, candidate) {
    if (!target || !candidate) return;
    const bubble = target.closest(".xy-bubble-assistant");
    const body = target.closest("div");
    if (!bubble || !body || body.querySelector("[data-chat-memory-save]")) return;
    const row = document.createElement("div");
    row.className = "xy-chat-memory-actions";
    row.innerHTML = `<button class="xy-soft" type="button" data-chat-memory-save>让小屿记住</button>`;
    const button = $("button", row);
    button.dataset.candidate = candidate;
    body.append(row);
  }

  function saveChatMemory(button) {
    const candidate = button?.dataset.candidate || "";
    const text = window.prompt("编辑要让小屿记住的内容", candidate);
    if (text == null) return;
    const value = text.trim();
    if (!value) {
      showToast("先写一句想让小屿记住的内容");
      return;
    }
    const note = {
      id: `memory_${Date.now()}`,
      date: todayKey,
      content: value,
      source: "companion",
      source_conversation_id: state.activeConversationId || "",
      created_at: new Date().toISOString(),
    };
    memoryNotes.push(note);
    persistMemoryNotes();
    apiJSON("/api/memory", {
      method: "POST",
      body: {content: value, conversation_id: state.activeConversationId || "", source: "companion"},
      timeoutMs: 6000,
    }).catch(() => {});
    renderCompanionPrompts();
    button.closest(".xy-chat-memory-actions")?.remove();
    showToast("小屿已经记住这件事");
  }

  async function syncMemoryLibrary() {
    try {
      const data = await apiJSON("/api/memory", {timeoutMs: 5000});
      const existing = new Set(memoryNotes.map((note) => note.id));
      (data.memories || []).forEach((memory) => {
        if (!memory?.id || existing.has(memory.id)) return;
        memoryNotes.push({
          id: memory.id,
          date: String(memory.created_at || "").slice(0, 10),
          content: memory.content || "",
          source: memory.source || "server",
          source_conversation_id: (memory.source_conversation_ids || [])[0] || "",
          created_at: memory.created_at || "",
        });
      });
      persistMemoryNotes();
      renderCompanionPrompts();
    } catch (_error) {}
  }

  function renderConversationList(items = []) {
    const list = $("#xy-chat-history-list");
    if (!list) return;
    if (!items.length) {
      list.innerHTML = `<p class="xy-inline-status">还没有历史对话。</p>`;
      return;
    }
    list.innerHTML = items.map((item) => `
      <div class="xy-chat-history-item" data-conversation-id="${escapeHTML(item.id)}">
        <button type="button" data-open-conversation="${escapeHTML(item.id)}">
          <strong>${escapeHTML(item.title || "未命名对话")}</strong>
          <small>${escapeHTML(item.summary || "还没有摘要")}</small>
          <em>${escapeHTML(item.category_label || item.category || "随便聊聊")}</em>
        </button>
        <div class="xy-chat-history-actions">
          <button type="button" data-open-conversation="${escapeHTML(item.id)}">打开</button>
          <button type="button" data-delete-conversation="${escapeHTML(item.id)}">删除</button>
        </div>
      </div>`).join("");
  }

  async function refreshConversationList() {
    try {
      const data = await apiJSON("/api/conversations?limit=30", {timeoutMs: 6000});
      renderConversationList(data.conversations || []);
    } catch (_error) {
      renderConversationList([]);
    }
  }

  async function openConversation(conversationId) {
    const thread = $("#xy-chat");
    if (thread) {
      thread.dataset.ready = "";
      thread.innerHTML = "";
    }
    if (await loadConversation(conversationId)) {
      $("#xy-chat-history-panel") && ($("#xy-chat-history-panel").hidden = true);
      showToast("已打开历史对话");
    }
  }

  async function deleteConversation(conversationId) {
    if (!conversationId) return;
    const ok = window.confirm("删除这次对话？这会一并删除只来自这次对话的记忆。");
    if (!ok) return;
    try {
      const result = await apiJSON(`/api/conversations/${encodeURIComponent(conversationId)}`, {method: "DELETE", timeoutMs: 6000});
      for (let index = memoryNotes.length - 1; index >= 0; index -= 1) {
        if (memoryNotes[index]?.source_conversation_id === conversationId) memoryNotes.splice(index, 1);
      }
      persistMemoryNotes();
      if (state.activeConversationId === conversationId) {
        state.activeConversationId = "";
        persist();
        const thread = $("#xy-chat");
        if (thread) {
          thread.dataset.ready = "";
          thread.innerHTML = "";
        }
        await startNewConversation();
      }
      await refreshConversationList();
      showToast(`已删除对话和 ${result.deleted_memories || 0} 条相关记忆`);
    } catch (_error) {
      showToast("删除失败，稍后再试");
    }
  }

  async function sendChat(message) {
    const value = String(message || "").trim();
    if (!value) return;
    appendChat(value, "user");
    const fallback = buildXiaoyuReply(value);
    const pending = appendChat(fallback, "assistant");
    try {
      const result = await requestLLMReply(value, fallback);
      pending.textContent = result.reply;
    } catch (_error) {
      pending.textContent = fallback;
    }
    addChatMemoryAction(pending, memoryCandidateFromChat(value, pending.textContent));
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

  function normalizeMeetingFromReport(report = {}, fallback = {}) {
    const now = new Date();
    const structured = report.structured || {};
    const title = report.title || structured.title || fallback.title || "真实会议记录";
    const minutes = report.minutes || report.diary || report.summary || "";
    return {
      id: report.id || report.session_id || `meeting_${Date.now()}`,
      title,
      date: report.date || todayKey,
      time: report.time || `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`,
      duration: report.duration_min ? `${report.duration_min} 分钟` : fallback.duration || "",
      status: report.status || fallback.status || "已整理",
      summary: report.summary || (minutes ? truncate(minutes, 96) : fallback.summary || ""),
      minutesMarkdown: minutes,
      tags: report.tags || fallback.tags || ["真实记录"],
      source: "real",
      report,
    };
  }

  function rememberMeetingReport(report = {}) {
    const status = String(report.status || "");
    if (status !== "ready" || !(report.summary || report.minutes || report.diary)) return false;
    const meeting = normalizeMeetingFromReport(report, {status: "已整理"});
    const key = report.report_path || report.session_id || report.id || meeting.id;
    const existingIndex = meetingNotes.findIndex((item) => {
      const existing = item.report || {};
      return (existing.report_path || existing.session_id || existing.id || item.id) === key;
    });
    if (existingIndex >= 0) meetingNotes[existingIndex] = meeting;
    else meetingNotes.push(meeting);
    persistMeetingNotes();
    return true;
  }

  function settleMeetingCompletion(conversation = stateData().conversation || {}) {
    const report = conversation.report || conversation.meeting_report || {};
    const status = String(report.status || conversation.meeting_state || "");
    if (status === "ready" && rememberMeetingReport(report)) {
      meetingCompletionPending = false;
      persistMeetingSession("");
      setText("xy-meeting-live-status", "会议整理完成，已放入会议历史。");
      return true;
    }
    if (meetingCompletionPending && status === "error") {
      meetingCompletionPending = false;
      persistMeetingSession("");
      setText("xy-meeting-live-status", report.error || conversation.last_recording_error || "会议整理失败，可稍后重试。");
      return true;
    }
    return false;
  }

  function currentRealMeeting() {
    const conversation = stateData().conversation || {};
    const report = conversation.report || conversation.meeting_report || {};
    if (state.meetingSessionId || report.summary || report.minutes || report.status) {
      return normalizeMeetingFromReport(report, {
        title: state.meetingSessionId ? "正在记录的会议" : "最近会议记录",
        status: conversation.recording_status || report.status || "已准备",
        summary: conversation.recording_status ? `当前状态：${statusLabel(conversation.recording_status)}` : "",
      });
    }
    return meetingNotes.length ? meetingNotes[meetingNotes.length - 1] : null;
  }

  function demoMeeting() {
    const meeting = seedData.meetings?.currentMeeting || {};
    return {...meeting, source: "preview_seed", tags: meeting.tags || ["历史示例"]};
  }

  function meetingForDisplay() {
    return currentRealMeeting() || demoMeeting();
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
    const conversation = stateData().conversation || {};
    const report = conversation.report || conversation.meeting_report || {};
    const meeting = meetingForDisplay();
    const isReal = meeting.source === "real";
    const status = report.status || conversation.recording_status || meeting.status || (isReal ? "已准备" : "历史示例");
    const summary = report.summary || meeting.summary || (isReal ? "真实会议整理完成后会显示在这里。" : "暂无真实会议记录，下面显示一条历史示例。");
    const duration = report.duration_min ? `${report.duration_min} 分钟` : (conversation.stats?.duration ? `${Math.round(conversation.stats.duration / 60)} 分钟` : meeting.duration || "");
    $("#xy-current-meeting-title").innerHTML = formatMeetingTitle(meeting.title);
    $("#xy-meeting-status").textContent = state.meetingSessionId || isReal ? statusLabel(status) : "历史示例";
    $("#xy-meeting-summary").textContent = summary;
    $("#xy-meeting-date").textContent = `${meeting.date || ""} ${meeting.time || ""}`.trim();
    $("#xy-meeting-time").textContent = duration;
    setText("xy-meeting-live-status", meetingStatusText(status, conversation, report));
    const start = $("#xy-meeting-start");
    const complete = $("#xy-meeting-complete");
    if (start) start.disabled = Boolean(state.meetingSessionId);
    if (complete) complete.disabled = !state.meetingSessionId;
    renderMeetingHistory();
  }

  function renderMeetingHistory() {
    const container = $("#xy-meeting-history");
    if (!container) return;
    const demo = demoMeeting();
    const items = [
      ...meetingNotes.slice().reverse(),
      ...(meetingNotes.length ? [] : [demo]),
    ].slice(0, 5);
    container.innerHTML = items.map((meeting, index) => `<button type="button" data-meeting-index="${index}"><span class="xy-icon-badge ${meeting.source === "preview_seed" ? "xy-amber" : "xy-sage"}">${iconUse("xy-users")}</span><span><strong>${escapeHTML(meeting.title || "会议纪要")}</strong><small>${escapeHTML(meeting.source === "preview_seed" ? "历史示例" : "真实记录")} · ${escapeHTML(meeting.date || "")} ${escapeHTML(meeting.time || "")}${meeting.duration ? ` · ${escapeHTML(meeting.duration)}` : ""}</small></span>${iconUse("xy-chevron")}</button>`).join("");
    $$("[data-meeting-index]", container).forEach((button) => {
      button.addEventListener("click", () => {
        const meeting = items[Number(button.dataset.meetingIndex)] || demo;
        openMeetingDetail(meeting);
      });
    });
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
      settleMeetingCompletion(data);
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
      const data = await apiJSON("/api/meeting/complete", {method: "POST", body: {session_id: state.meetingSessionId}, timeoutMs: 15000});
      if (data.report || data.minutes || data.summary) {
        rememberMeetingReport(data.report || data);
        persistMeetingSession("");
      } else if (data.processing || data.submitted) {
        meetingCompletionPending = true;
        setText("xy-meeting-live-status", "会议整理已提交，后台完成后会自动放入会议历史。");
      } else {
        persistMeetingSession("");
      }
      showToast("会议整理已提交");
      await refreshConversationState();
    } catch (error) {
      setText("xy-meeting-live-status", `整理暂不可用：${error.message || "请稍后重试"}`);
      showToast("整理失败，已保留当前记录状态");
    } finally {
      renderMeeting();
    }
  }

  function openMeetingDetail(meetingArg = null) {
    const meeting = meetingArg || meetingForDisplay();
    const body = $("#xy-meeting-dialog-body");
    const tags = (meeting.tags || []).map((tag) => `<span>${escapeHTML(tag)}</span>`).join("");
    const label = meeting.source === "preview_seed" ? "历史示例会议纪要" : "会议纪要";
    body.innerHTML = `<p class="xy-label">${label}</p><h2 id="xy-meeting-dialog-title">${escapeHTML(meeting.title || "会议纪要")}</h2><div class="xy-sheet-meta"><span>${escapeHTML(meeting.date || "")} ${escapeHTML(meeting.time || "")}</span><span>${escapeHTML(meeting.duration || "")}</span>${tags}</div>${markdownToHtml(meeting.source === "preview_seed" ? (meetingMarkdownText || meeting.minutesMarkdown || "") : (meeting.minutesMarkdown || meeting.summary || "真实会议整理完成后会显示详细纪要。"))}`;
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
      const selfcare = selfcareFor(key);
      button.classList.toggle("has-record", Boolean(seedData.dailyRecords?.[key] || diaryOverrides[key]?.diary || daySummaryFor(key)?.available || selfcare.updatedAt));
      button.classList.toggle("is-selected", key === state.selectedDate);
      button.classList.toggle("is-today", key === todayKey);
      button.addEventListener("click", () => {
        state.selectedDate = key;
        state.calendarMonth = key.slice(0, 7);
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
    const date = day.date || state.selectedDate;
    const waterText = day.waterCups == null ? "未记录" : `${day.waterCups} / ${day.waterGoal || 8} 杯`;
    const stepsText = day.steps == null ? "未记录" : `${day.steps} 步`;
    const diaryText = day.diary || (date === todayKey ? "今天还没有写日记，可以先留下一句话。" : "这一天还没有日记记录。");
    const replyText = diaryOverrides[date]?.assistantReply || day.assistantReply || "";
    const summaryText = day.observedSummary?.available ? `观察约 ${day.observedSummary.presence_min || 0} 分钟` : (seedData.dailyRecords?.[date] ? "历史参考数据" : "暂无实时观察");
    detail.innerHTML = `<p class="xy-label">${escapeHTML(formatDate(date))}</p><h2>${date === todayKey ? "今日" : "这一天"}主导状态：${escapeHTML(day.mainState || "暂无记录")}</h2><div class="xy-detail-grid"><span>专注状态 <strong>${escapeHTML(day.focusDisplay || "今日还没有观察记录")}</strong></span><span>喝水 <strong>${escapeHTML(waterText)}</strong></span><span>步数 <strong>${escapeHTML(stepsText)}</strong></span><span>冥想 <strong>${day.meditation ? "已完成" : "未完成"}</strong></span><span class="wide">观察 <strong>${escapeHTML(summaryText)}</strong></span><span class="wide">会议 <strong>${escapeHTML(day.hadMeeting ? day.meetingTitle : "今天没有会议记录")}</strong></span></div><p>${escapeHTML(diaryText)}</p><div class="xy-assistant-reply">${escapeHTML(replyText)}</div>`;
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
    const day = mergeDayData(date || state.selectedDate);
    const override = diaryOverrides[date] || {};
    state.selectedDate = date || state.selectedDate;
    persist();
    $("#xy-diary-dialog-title").textContent = `${formatDate(date)} 日记`;
    $("#xy-diary-date").textContent = formatDate(date);
    $("#xy-diary-editor").value = override.diary || (seedData.dailyRecords?.[date] ? day.diary : "");
    const existingReply = override.assistantReply || day.assistantReply || "";
    setDiaryLetterState({
      reply: existingReply,
      status: existingReply ? "小屿的回信" : "保存后，小屿会在这里写一封短回信",
      privacy: override.assistantMeta?.privacyNote || "",
      visible: Boolean(existingReply),
    });
    $("#xy-diary-dialog").showModal();
  }

  async function saveDiary(event) {
    event.preventDefault();
    const day = currentDay();
    const saveButton = $("#xy-diary-save");
    const text = $("#xy-diary-editor").value.trim() || "";
    const memoryContext = buildAssistantMemoryContext();
    let assistantReply = buildDiaryAssistantReply(day, text);
    let responseMeta = {};
    if (saveButton) {
      saveButton.disabled = true;
      saveButton.textContent = "小屿正在读这一页…";
    }
    setDiaryLetterState({
      reply: "小屿正在读这一页…",
      status: "正在生成回信",
      loading: true,
      visible: true,
    });
    try {
      const data = await apiJSON("/api/reflect", {
        method: "POST",
        body: {
          mode: "diary",
          user_text: text,
          emotion: day.mainState || currentEmotionText() || "Neutral",
          user_name: "蛋挞",
          day_summary: {date: state.selectedDate, diary: text, care: memoryContext.careText, selfcare: selfcareFor(state.selectedDate), observed: daySummaryFor(state.selectedDate)},
          memory_context: buildReflectMemoryContext(memoryContext),
          cloud_enhanced: cloudReflectEnabled,
          display_mode: "letter_note",
          reply_style: "gentle",
        },
        timeoutMs: 15000,
      });
      assistantReply = data.reply || data.text || assistantReply;
      responseMeta = {
        source: data.source || "",
        displayMode: data.display_mode || data.displayMode || "letter_note",
        replyStyle: data.reply_style || data.replyStyle || "gentle",
        privacyNote: data.privacy_note || data.privacyNote || "",
        memoryCandidate: normalizeMemoryCandidate(data, text, assistantReply),
      };
    } catch (_error) {}
    if (!responseMeta.memoryCandidate) responseMeta.memoryCandidate = normalizeMemoryCandidate({}, text, assistantReply);
    if (!responseMeta.privacyNote) responseMeta.privacyNote = privacyLabel();
    diaryOverrides[state.selectedDate] = {diary: text, assistantReply, assistantMeta: responseMeta};
    persistDiary();
    setDiaryLetterState({
      reply: assistantReply,
      status: "小屿的回信",
      privacy: responseMeta.privacyNote,
      loading: false,
      visible: true,
      animate: true,
      candidate: responseMeta.memoryCandidate,
    });
    if (saveButton) {
      saveButton.disabled = false;
      saveButton.textContent = "保存日记";
    }
    renderRecords();
    showToast("日记已保存，小屿也写下了回信");
  }

  function openMemoryEditor() {
    const editor = $("#xy-memory-editor");
    const actions = $("#xy-memory-actions");
    const textarea = $("#xy-memory-text");
    const candidate = $("#xy-memory-start")?.dataset.candidate || "";
    if (!editor || !textarea) return;
    textarea.value = candidate;
    editor.hidden = false;
    if (actions) actions.hidden = true;
    textarea.focus();
  }

  function closeMemoryEditor() {
    const editor = $("#xy-memory-editor");
    const actions = $("#xy-memory-actions");
    if (editor) editor.hidden = true;
    if (actions) actions.hidden = !($("#xy-memory-start")?.dataset.candidate);
  }

  function saveMemoryNote() {
    const text = $("#xy-memory-text")?.value.trim() || "";
    if (!text) {
      showToast("先写一句想让小屿记住的内容");
      return;
    }
    memoryNotes.push({
      id: `memory_${Date.now()}`,
      date: state.selectedDate,
      content: text,
      source: "diary",
      created_at: new Date().toISOString(),
    });
    persistMemoryNotes();
    renderCompanionPrompts();
    apiJSON("/api/memory", {
      method: "POST",
      body: {content: text, conversation_id: "", source: "diary"},
      timeoutMs: 6000,
    }).catch(() => {});
    closeMemoryEditor();
    const actions = $("#xy-memory-actions");
    if (actions) actions.hidden = true;
    const start = $("#xy-memory-start");
    if (start) start.dataset.candidate = "";
    showToast("小屿已经记住这件事");
  }

  function weekKeyForDate(date) {
    return seedData.dailyRecords?.[date]?.weekKey || weekKeyFromDate(date);
  }

  function openWeeklyReportModal(weekKey = weekKeyForDate(state.selectedDate)) {
    const report = seedData.weeklyReports?.[weekKey];
    if (!report) return;
    $("#xy-weekly-dialog-body").innerHTML = `<p class="xy-label">本周周报</p><h2 id="xy-weekly-dialog-title">${escapeHTML(report.title)}</h2><div class="xy-sheet-meta"><span>${escapeHTML(report.rangeLabel)}</span><span>${escapeHTML(report.weekKey)}</span></div><p>${escapeHTML(report.summary)}</p><h3>这一周值得留下的部分</h3><ul>${(report.highlights || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul><h3>小屿的照顾提醒</h3><p>${escapeHTML(report.careSummary || "")}</p><p>${escapeHTML(report.suggestion || "")}</p>`;
    $("#xy-weekly-dialog").showModal();
  }

  async function generateWeeklyReport() {
    const {start, end} = weekBounds(state.selectedDate);
    const weekKey = weekKeyForDate(state.selectedDate);
    await loadDaySummaryRange(start.slice(0, 7));
    const dates = datesBetween(start, end);
    const realEntries = dates
      .map((date) => {
        const day = mergeDayData(date);
        const diary = diaryOverrides[date]?.diary || "";
        const selfcare = selfcareFor(date);
        return {
          date,
          emotion: day.mainState,
          content: diary,
          observed_emotion: day.observedSummary?.dominant_emotion || "",
          selfcare: selfcare.updatedAt ? {waterCups: selfcare.waterCups, steps: selfcare.steps, meditation: selfcare.meditation} : null,
        };
      })
      .filter((entry) => entry.content || entry.observed_emotion || entry.selfcare);
    const report = seedData.weeklyReports?.[weekKey];
    const entries = realEntries.length ? realEntries : dates
      .map((date) => mergeDayData(date))
      .filter((day) => seedData.dailyRecords?.[day.date])
      .map((day) => ({date: day.date, emotion: day.mainState, content: day.diary || "", source: "preview_seed"}));
    const fallback = realEntries.length
      ? `这一周已经有 ${realEntries.length} 天留下了真实记录。小屿会先按这些记录来整理，不用历史示例替你定义这一周。`
      : (report?.summary || "这一周真实记录还不多。可以先写一篇日记，周报会从今天开始慢慢积累。");
    try {
      const data = await apiJSON("/api/report/weekly", {
        method: "POST",
        body: {
          entries,
          user_name: "蛋挞",
          week_start: start,
          week_end: end,
          day_summaries: dates.map((date) => daySummaryFor(date)).filter(Boolean),
          options: {data_sufficiency: realEntries.length < 3 ? "low" : realEntries.length <= 5 ? "medium" : "high", demo_mode: !realEntries.length},
        },
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
    const dates = new Set([...Object.keys(seedData.dailyRecords || {}), ...Object.keys(diaryOverrides || {})]);
    const items = [...dates].sort((a, b) => b.localeCompare(a)).slice(0, 5).map((date) => mergeDayData(date));
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

  function renderAnnounceSettings() {
    const settings = {...announceDefaults(), ...(announceSettings || voiceState?.announce || {})};
    const enabled = $("#xy-announce-enabled");
    const sedentary = $("#xy-sedentary-minutes");
    const snooze = $("#xy-snooze-minutes");
    const eye = $("#xy-eye-fatigue-enabled");
    const meeting = $("#xy-meeting-status-enabled");
    if (enabled) enabled.checked = Boolean(settings.enabled);
    if (sedentary) sedentary.value = String(settings.sedentary_minutes || 45);
    if (snooze) snooze.value = String(settings.snooze_minutes || 10);
    if (eye) eye.checked = settings.eye_fatigue_enabled !== false;
    if (meeting) meeting.checked = settings.meeting_status_enabled !== false;
    const playback = voiceState?.playback || {};
    const bridge = playback.bridge || {};
    const zhipu = playback.zhipu || {};
    const voiceReady = zhipu.configured === false ? "TTS 未配置" : bridge.configured === false ? "扬声器未配置" : "reCamera 扬声器";
    setText("xy-announce-status", `${settings.enabled ? "已开启" : "已关闭"} · ${voiceReady}`);
  }

  function collectAnnounceSettings() {
    const current = {...announceDefaults(), ...(announceSettings || {})};
    return {
      ...current,
      enabled: Boolean($("#xy-announce-enabled")?.checked),
      sedentary_minutes: Number($("#xy-sedentary-minutes")?.value || current.sedentary_minutes),
      snooze_minutes: Number($("#xy-snooze-minutes")?.value || current.snooze_minutes),
      eye_fatigue_enabled: Boolean($("#xy-eye-fatigue-enabled")?.checked),
      meeting_status_enabled: Boolean($("#xy-meeting-status-enabled")?.checked),
      target: "recamera_speaker",
    };
  }

  async function saveAnnounceSettings() {
    const next = collectAnnounceSettings();
    announceSettings = next;
    renderAnnounceSettings();
    try {
      const response = await apiJSON("/api/voice/announce/settings", {method: "POST", body: next, timeoutMs: 6000});
      announceSettings = response.settings || next;
      voiceState = response.state || voiceState;
      renderDevice();
      showToast(announceSettings.enabled ? "语音播报已开启" : "语音播报已关闭");
    } catch (_error) {
      setText("xy-announce-status", "设置暂未保存，请稍后再试");
      showToast("语音播报设置暂不可用");
    }
  }

  function shiftMonth(delta) {
    const [year, month] = state.calendarMonth.split("-").map(Number);
    const next = new Date(year, month - 1 + delta, 1);
    const key = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}`;
    state.calendarMonth = key;
    const sameDay = String(parseDate(state.selectedDate).getDate()).padStart(2, "0");
    const candidate = `${key}-${sameDay}`;
    const total = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
    state.selectedDate = Number(sameDay) <= total ? candidate : `${key}-${String(total).padStart(2, "0")}`;
    persist();
    renderRecords();
    loadDaySummaryRange(key).then(renderRecords);
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
    try {
      const announce = await apiJSON("/api/voice/announce/settings", {timeoutMs: 6000});
      announceSettings = announce.settings || voiceState?.announce || announceSettings;
      voiceState = announce.state || voiceState;
    } catch (_error) {
      announceSettings = voiceState?.announce || announceSettings;
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
    renderAnnounceSettings();
  }

  function applyLiveState(snapshot) {
    liveState = snapshot;
    if (stateData().voice) {
      voiceState = stateData().voice;
      announceSettings = voiceState.announce || announceSettings;
    }
    settleMeetingCompletion();
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
      if (data.transcript) {
        appendChat(data.transcript, "user");
        persistConversationTurn("user", data.transcript);
      }
      const reply = data.reply || "小屿听到了。";
      appendChat(reply, "assistant");
      persistConversationTurn("assistant", reply);
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
    $(".xy-prompt-chips")?.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (button) sendChat(button.dataset.chatPrompt || button.textContent);
    });
    $("#xy-chat")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-chat-memory-save]");
      if (button) saveChatMemory(button);
    });
    $("#xy-chat-history-toggle")?.addEventListener("click", async () => {
      const panel = $("#xy-chat-history-panel");
      if (!panel) return;
      panel.hidden = !panel.hidden;
      if (!panel.hidden) await refreshConversationList();
    });
    $("#xy-chat-new")?.addEventListener("click", startNewConversation);
    $("#xy-chat-history-list")?.addEventListener("click", (event) => {
      const open = event.target.closest("[data-open-conversation]");
      const del = event.target.closest("[data-delete-conversation]");
      if (del) {
        deleteConversation(del.dataset.deleteConversation);
        return;
      }
      if (open) openConversation(open.dataset.openConversation);
    });
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
    $("#xy-water-add")?.addEventListener("click", addWaterCup);
    $("#xy-steps-edit")?.addEventListener("click", editSteps);
    $("#xy-meditation-toggle")?.addEventListener("click", toggleMeditation);
    $("#xy-announce-enabled")?.addEventListener("change", saveAnnounceSettings);
    $("#xy-sedentary-minutes")?.addEventListener("change", saveAnnounceSettings);
    $("#xy-snooze-minutes")?.addEventListener("change", saveAnnounceSettings);
    $("#xy-eye-fatigue-enabled")?.addEventListener("change", saveAnnounceSettings);
    $("#xy-meeting-status-enabled")?.addEventListener("change", saveAnnounceSettings);
    $("#xy-cloud-reflect-enabled")?.addEventListener("change", (event) => {
      cloudReflectEnabled = Boolean(event.currentTarget.checked);
      persistCloudReflect();
      renderCloudReflectSettings();
      showToast(cloudReflectEnabled ? "云端增强回复已开启" : "已切换为本地简洁回复");
    });
    $$("[data-meeting-detail]").forEach((button) => button.addEventListener("click", openMeetingDetail));
    $("#xy-calendar-prev")?.addEventListener("click", () => shiftMonth(-1));
    $("#xy-calendar-next")?.addEventListener("click", () => shiftMonth(1));
    $("#xy-open-diary")?.addEventListener("click", () => openDiaryModal());
    $("#xy-diary-link")?.addEventListener("click", () => openDiaryModal());
    $("#xy-weekly-link")?.addEventListener("click", () => generateWeeklyReport());
    $("#xy-diary-form")?.addEventListener("submit", saveDiary);
    $("#xy-memory-start")?.addEventListener("click", openMemoryEditor);
    $("#xy-memory-cancel")?.addEventListener("click", closeMemoryEditor);
    $("#xy-memory-save")?.addEventListener("click", saveMemoryNote);
    $$("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog")?.close()));
    $$("dialog.xy-sheet").forEach((dialog) => dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    }));
  }

  async function init() {
    if (!seedData.currentDate) return;
    if (!state.selectedDate || (state.selectedDate === seedCurrentDate && !diaryOverrides[seedCurrentDate]?.diary)) {
      state.selectedDate = todayKey;
    }
    state.calendarMonth = state.calendarMonth || state.selectedDate.slice(0, 7) || todayKey.slice(0, 7);
    seedMemoryNotesFromPreview();
    await syncMemoryLibrary();
    await loadDaySummaryRange(state.calendarMonth);
    renderHome();
    renderCompanionPrompts();
    await resetInitialChat();
    renderMeeting();
    renderRecords();
    renderDevice();
    renderCloudReflectSettings();
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
