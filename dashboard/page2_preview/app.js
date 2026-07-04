(() => {
  "use strict";

  const STORAGE_KEY = "xinyu_product_home_v2";
  const DIARY_OVERRIDE_KEY = "xinyu_page2_diary_overrides_v1";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const seedData = window.XINYU_PREVIEW_DATA || {};
  const previewTodayKey = seedData.currentDate || "";
  const sidKey = "session" + "_" + "id";
  const audioKey = "audio" + "_" + "processing";
  const stableKey = "stable" + "_" + "count";

  const moods = [
    { id: "joy", name: "开心", icon: "./assets/moods/joy.png", color: "#ffd04f", score: 92, note: "把这份轻盈好好收下。" },
    { id: "calm", name: "平静", icon: "./assets/moods/calm.png", color: "#8ddca9", score: 78, note: "像风停在湖面，安稳而清澈。" },
    { id: "surprise", name: "惊讶", icon: "./assets/moods/surprise.png", color: "#bbb9aa", score: 68, note: "生活突然眨了一下眼。" },
    { id: "sad", name: "有些低落", icon: "./assets/moods/sad.png", color: "#72a9c8", score: 30, note: "不用急着振作，先允许自己难过。" },
    { id: "worried", name: "有些紧张", icon: "./assets/moods/worried.png", color: "#c892d5", score: 40, note: "先把担心放在这里，一件一件来看。" },
    { id: "angry", name: "有些生气", icon: "./assets/moods/angry.png", color: "#ff416d", score: 25, note: "这份情绪也许正在提醒你：有些边界很重要。" },
    { id: "tired", name: "疲惫", icon: "./assets/moods/tired.png", color: "#b68a58", score: 42, note: "今天已经很努力了，可以慢一点。" },
    { id: "lonely", name: "委屈", icon: "./assets/moods/lonely.png", color: "#7f91a3", score: 34, note: "没有被听见的感受，也值得被好好放下。" },
    { id: "numb", name: "麻木", icon: "./assets/moods/numb.png", color: "#9b9a8d", score: 36, note: "没有明显感觉也没关系，先给自己一点空间。" }
  ];
  const moodById = Object.fromEntries(moods.map((mood) => [mood.id, mood]));
  const moodFromRealEmotion = {
    Happiness: "joy", Happy: "joy", Neutral: "calm", Calm: "calm", Surprise: "surprise",
    Sadness: "sad", Sad: "sad", Fear: "worried", Anxiety: "worried",
    Anger: "angry", Angry: "angry", Disgust: "angry", Contempt: "lonely", Tired: "tired"
  };
  const weathers = ["晴朗", "多云", "下雨", "微凉", "夜晚"];
  const tags = ["工作", "学习", "家人", "朋友", "独处", "睡眠", "运动", "会议"];

  let store = loadStore();
  let selectedEntryKey = previewTodayKey;
  let editingEntryKey = "";
  let chosenMood = "";
  let calendarCursor = startOfMonth(previewTodayKey ? parseDate(previewTodayKey) : new Date());
  let toastTimer = 0;
  let ws = null;
  let wsLastAt = 0;
  let pollTimer = 0;
  let focusTicker = 0;

  let product = {
    connected: false,
    state: null,
    mode: "single",
    meetingActive: false,
    meetingSid: "",
    currentSid: "",
    notice: ""
  };

  let diaryOverrides = loadDiaryOverrides();
  let meetingMarkdownText = seedData.meetings?.currentMeeting?.minutesMarkdown || "";

  async function loadMeetingMinutesMarkdown() {
    const path = seedData.meetings?.currentMeeting?.minutesMarkdownPath;
    if (!path || location.protocol === "file:") return meetingMarkdownText;
    try {
      const response = await fetch(`./${path}`, { cache: "no-store" });
      if (response.ok) meetingMarkdownText = await response.text();
    } catch (_) {}
    return meetingMarkdownText;
  }

  function initialStore() {
    return {
      version: 2,
      profile: { name: localStorage.getItem("xinyu_user_name") || "心屿用户", reminderTone: "gentle" },
      entries: buildSeedEntries(),
      meetings: buildSeedMeetings(),
      notifyEnabled: localStorage.getItem("xinyu_notify_enabled") === "true",
      voice: {
        enabled: localStorage.getItem("xinyu_voice_enabled") !== "false",
        volume: 0.85,
        rate: 1.0,
        proactive: "gentle",
        lastText: "",
        lastReason: ""
      },
      lastSent: {},
      quietHours: { start: "22:30", end: "08:30" }
    };
  }

  function loadStore() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      const base = initialStore();
      if (!saved || saved.version !== 2) return base;
      return {
        ...base,
        ...saved,
        profile: { ...base.profile, ...(saved.profile || {}) },
        entries: { ...base.entries, ...(saved.entries || {}) },
        meetings: mergeSeedMeetings(saved.meetings || base.meetings),
        voice: { ...base.voice, ...(saved.voice || {}) },
        lastSent: saved.lastSent || {}
      };
    } catch (_) {
      return initialStore();
    }
  }

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
    localStorage.setItem("xinyu_user_name", store.profile.name);
    localStorage.setItem("xinyu_notify_enabled", String(store.notifyEnabled));
    localStorage.setItem("xinyu_voice_enabled", String(store.voice.enabled));
  }

  function loadDiaryOverrides() {
    try {
      return JSON.parse(localStorage.getItem(DIARY_OVERRIDE_KEY) || "{}") || {};
    } catch (_) {
      return {};
    }
  }

  function persistDiaryOverrides() {
    localStorage.setItem(DIARY_OVERRIDE_KEY, JSON.stringify(diaryOverrides));
  }

  function dateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }
  function parseDate(key) { const [y, m, d] = key.split("-").map(Number); return new Date(y, m - 1, d); }
  function currentDateKey() { return previewTodayKey || dateKey(new Date()); }
  function startOfMonth(date) { return new Date(date.getFullYear(), date.getMonth(), 1); }
  function formatDate(date, includeYear = false) {
    return `${includeYear ? `${date.getFullYear()}年` : ""}${date.getMonth() + 1}月${date.getDate()}日`;
  }
  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
  }
  function truncateText(value, max = 86) {
    const text = String(value || "").trim();
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  }
  function dayDataFor(key) {
    return seedData.dailyRecords?.[key] || store.entries[key]?.dailyRecord || null;
  }
  function moodColorByName(name) {
    return {
      calm: "#8ddca9", focused: "#d6a24d", relaxed: "#98b89d", bright: "#ffd04f",
      busy: "#d0a064", tired: "#b68a58", pressure: "#c892d5", clear: "#72a9c8"
    }[name] || "#b9752b";
  }
  function moodLevel(point) {
    return { bright: 1, focused: 1.4, clear: 1.7, calm: 2, relaxed: 2.35, busy: 2.6, tired: 3.25, pressure: 3.55 }[point?.mood] || 2;
  }
  function renderTrendChart(container, points = [], options = {}) {
    if (!container) return;
    if (!points.length) {
      container.innerHTML = "<div class=\"memory-note\">这一天还没有留下状态变化。</div>";
      return;
    }
    const width = options.width || 420;
    const height = options.height || 170;
    const padX = 34;
    const padY = 28;
    const step = points.length > 1 ? (width - padX * 2) / (points.length - 1) : 0;
    const coords = points.map((point, index) => {
      const x = padX + step * index;
      const y = padY + (moodLevel(point) / 4) * (height - padY * 2);
      return { ...point, x, y };
    });
    const path = coords.map((point, index) => `${index ? "L" : "M"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
    const labels = coords.map((point, index) => {
      const dy = index % 2 ? 18 : -12;
      const showLabel = coords.length <= 4 || index === 0 || index === coords.length - 1 || point.mood === "tired" || point.mood === "pressure";
      return `
        <circle cx="${point.x}" cy="${point.y}" r="5.5" fill="${moodColorByName(point.mood)}"></circle>
        ${showLabel ? `<text class="trend-label" x="${point.x}" y="${point.y + dy}" text-anchor="middle">${escapeHTML(point.display)}</text>` : ""}
        <text class="trend-time" x="${point.x}" y="${height - 12}" text-anchor="middle">${escapeHTML(point.time)}</text>
      `;
    }).join("");
    container.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" aria-hidden="true">
        <path d="M ${padX} ${height - 34} H ${width - padX}" fill="none" stroke="rgba(59,55,43,.12)" stroke-width="1"></path>
        <path d="${path}" fill="none" stroke="#d89749" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"></path>
        ${labels}
      </svg>
    `;
  }
  function markdownToMeetingHtml(markdown) {
    const source = String(markdown || "");
    const lines = source.split(/\n+/).map((line) => line.trim()).filter(Boolean);
    let html = "";
    let inList = false;
    lines.forEach((line) => {
      if (/^---+$/.test(line)) return;
      if (line.startsWith("- ")) {
        if (!inList) {
          html += "<ul>";
          inList = true;
        }
        html += `<li>${escapeHTML(line.slice(2))}</li>`;
        return;
      }
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      if (line.startsWith("# ")) html += `<h2 id="meeting-dialog-title">${escapeHTML(line.slice(2))}</h2>`;
      else if (line.startsWith("## ")) html += `<h3>${escapeHTML(line.slice(3))}</h3>`;
      else if (line.startsWith("### ")) html += `<h3>${escapeHTML(line.slice(4))}</h3>`;
      else html += `<p>${escapeHTML(line)}</p>`;
    });
    if (inList) html += "</ul>";
    return html;
  }
  function buildAssistantMemoryContext() {
    const memory = seedData.assistantMemory || {};
    const day = memory.currentDay || dayDataFor(currentDateKey()) || {};
    return {
      ...memory,
      currentDay: day,
      trendText: (day.emotionTrend || []).map((point) => `${point.time}${point.display}`).join("，"),
      careText: `喝水${day.waterCups || "-"} / ${day.waterGoal || 8}杯，步数${day.steps || "-"}，冥想${day.meditation ? "已完成" : "未完成"}`,
      meetingTitle: memory.currentMeeting?.title || day.meetingTitle || "",
      diaryText: day.diary || ""
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
  function buildDiaryAssistantReply(dayData, diaryText) {
    if (typeof seedData.buildDiaryAssistantReply === "function") return seedData.buildDiaryAssistantReply(dayData, diaryText);
    const meetingPart = dayData?.hadMeeting ? `今天还有${dayData.meetingTitle}，信息量不小。` : "";
    const carePart = `喝水${dayData?.waterCups || "-"}杯、步数${dayData?.steps || "-"}，${dayData?.meditation ? "也留了安静时间" : "今晚可以留一点安静时间"}。`;
    return `小屿读到你写下的这些，也看到今天的主导状态是${dayData?.mainState || "状态平稳"}。${meetingPart}${carePart}先不用把一切都整理完，照顾好此刻就很好。`;
  }
  function appendChatMessage(role, text) {
    const thread = $("#chat-thread");
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble${role === "user" ? " user" : ""}`;
    bubble.textContent = text;
    thread.append(bubble);
    thread.scrollTop = thread.scrollHeight;
    return bubble;
  }
  function buildSeedEntries() {
    const entries = {};
    Object.entries(seedData.dailyRecords || {}).forEach(([key, day]) => {
      const override = loadDiaryOverrides()[key] || {};
      entries[key] = {
        date: key,
        mood: moodById[day.moodId] ? day.moodId : (day.mainState?.includes("疲惫") ? "tired" : "calm"),
        weather: day.weather || "多云",
        tags: day.tags || (day.hadMeeting ? ["会议", "整理"] : ["日常"]),
        note: override.diary || day.diary || "",
        focus: Number(day.focusScore || 0),
        minutes: day.meditation ? 8 : 0,
        dailyRecord: day,
        assistantReply: override.assistantReply || day.assistantReply || ""
      };
    });
    return entries;
  }
  function buildSeedMeetings() {
    const history = seedData.meetings?.history || [];
    return history.map((item) => ({ ...item }));
  }
  function mergeSeedMeetings(savedMeetings) {
    const saved = Array.isArray(savedMeetings) ? savedMeetings : [];
    const byId = new Map(buildSeedMeetings().map((item) => [item.id, item]));
    saved.forEach((item) => byId.set(item.id || `${item.date}-${item.title || item.summary}`, item));
    return [...byId.values()].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
  }
  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2400);
  }

  function voiceAvailable() {
    return "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  }

  function setVoiceStatus(text) {
    const status = $("#voice-status");
    if (status) status.textContent = text;
  }

  function stopVoice(reason = "user") {
    if (voiceAvailable()) window.speechSynthesis.cancel();
    setVoiceStatus(store.voice.enabled ? "语音已停止" : "语音已关闭");
  }

  function speakText(text, reason = "manual", interrupt = false) {
    const cleaned = String(text || "").trim();
    if (!cleaned) return false;
    store.voice.lastText = cleaned;
    store.voice.lastReason = reason;
    persist();
    if (!store.voice.enabled) {
      setVoiceStatus("语音已关闭");
      return false;
    }
    if (!voiceAvailable()) {
      setVoiceStatus("当前浏览器不支持语音朗读");
      showToast(cleaned);
      return false;
    }
    if (interrupt) window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(cleaned);
    utterance.lang = "zh-CN";
    utterance.volume = Math.max(0, Math.min(1, Number(store.voice.volume || 0.85)));
    utterance.rate = Math.max(0.7, Math.min(1.25, Number(store.voice.rate || 1)));
    utterance.onstart = () => setVoiceStatus("小屿正在说话");
    utterance.onend = () => setVoiceStatus(store.voice.enabled ? "语音待命" : "语音已关闭");
    utterance.onerror = () => setVoiceStatus("语音播放失败，已保留文字");
    window.speechSynthesis.speak(utterance);
    renderVoiceControls();
    return true;
  }

  function handleVoiceEvent(msg) {
    if (!msg || !msg.type) return false;
    if (msg.type === "voice_utterance") {
      const text = msg.display_text || msg.text || "";
      setVoiceStatus(text ? `小屿：${text}` : "收到语音事件");
      speakText(text, msg.reason || "voice_event", Boolean(msg.interrupt));
      return true;
    }
    if (msg.type === "voice_stop") {
      if (voiceAvailable()) window.speechSynthesis.cancel();
      setVoiceStatus("语音已停止");
      return true;
    }
    if (msg.type === "wake_word_detected") {
      showToast("小屿听到了唤醒词");
      const input = $("#chat-input");
      if (input) input.focus();
      return true;
    }
    return false;
  }

  function moodIcon(mood, className = "mood-icon", alt = "") {
    return `<img class="${className}" src="${mood.icon}" alt="${alt}">`;
  }
  function setMoodImage(selector, mood, alt = "") {
    const image = $(selector);
    if (!image || !mood) return;
    image.src = mood.icon;
    image.alt = alt;
  }
  function realMood() {
    const s = product.state || {};
    const observedEmotion = s.emotieff?.emotion || s.emotion?.emotion || s.emotion?.label || "";
    return moodById[moodFromRealEmotion[String(observedEmotion).trim()] || "calm"];
  }
  function realConfidence() {
    const s = product.state || {};
    return Number(s.emotieff?.confidence || s.emotion?.confidence || s.emotion?.score || 0);
  }
  function isMeetingLike() {
    const s = product.state || {};
    const feature = String(s.control?.active_feature || "");
    return product.mode === "meeting" || product.meetingActive || feature.includes("multi") || feature.includes("meeting");
  }
  function hasSinglePerson() {
    const s = product.state || {};
    if (isMeetingLike()) return false;
    return Boolean(s.attention?.has_face || s.face_lock?.locked || Number(s.pose?.count || 0) === 1);
  }
  function focusLabel(score, hasFace) {
    if (!hasFace) return ["等待画面", "当你在画面中，心屿会自动观察专注状态。"];
    const value = Number(score || 0);
    if (value >= 78) return ["专注中", "你现在比较稳定，适合继续完成眼前这一件事。"];
    if (value >= 55) return ["轻专注", "状态还在，可以用短一点的节奏推进。"];
    return ["有些分散", "可以先休息一小会儿，再重新开始。"];
  }

  async function refreshState(silent = false) {
    product.connected = true;
    if (!product.state) product.state = {};
    renderAll();
  }

  function connectWS() {
    product.connected = true;
  }

  function applyState(next) {
    product.state = next || {};
    product.connected = true;
    const feature = String(product.state.control?.active_feature || "");
    const sid = String(product.state.control?.[sidKey] || product.state[sidKey] || "");
    if (sid) product.currentSid = sid;
    product.meetingActive = feature.includes("meeting");
    product.mode = product.meetingActive ? "meeting" : "single";
    mergeTodayObservation();
    renderAll();
    evaluateNotifications();
  }

  function renderVoiceControls() {
    const enabled = Boolean(store.voice.enabled);
    const backend = product.state?.voice || {};
    const status = $("#voice-status");
    if (status && !store.voice.lastText) {
      status.textContent = enabled
        ? (backend.available === false ? "语音事件待命，浏览器播放" : "语音待命")
        : "语音已关闭";
    }
    const toggle = $("#voice-toggle");
    if (toggle) toggle.textContent = enabled ? "关闭语音" : "开启语音";
    const replay = $("#voice-replay");
    if (replay) replay.disabled = !store.voice.lastText;
    const checkbox = $("#voice-enabled");
    if (checkbox) checkbox.checked = enabled;
    const volume = $("#voice-volume");
    if (volume) volume.value = String(Math.round(Number(store.voice.volume || 0.85) * 100));
    const rate = $("#voice-rate");
    if (rate) rate.value = String(Number(store.voice.rate || 1).toFixed(2));
    const proactive = $("#voice-proactive");
    if (proactive) proactive.value = store.voice.proactive || "gentle";
    const backendLine = $("#voice-backend-status");
    if (backendLine) backendLine.textContent = `后端：${backend.engine || "browser_speech"} / ${backend.enabled === false ? "关闭" : "待命"}`;
  }

  function mergeTodayObservation() {
    if (!hasSinglePerson()) return;
    const key = currentDateKey();
    const existing = store.entries[key] || {};
    if (existing.note) return;
    const mood = realMood();
    const focus = Math.round(Number(product.state?.attention?.score || mood.score));
    store.entries[key] = {
      ...existing,
      date: key,
      mood: mood.id,
      weather: existing.weather || weathers[0],
      tags: existing.tags || ["今日观察"],
      note: existing.note || "",
      focus,
      minutes: existing.minutes || 1,
      observed: true
    };
    persist();
  }

  function goTo(pageName) {
    $$(".page").forEach((page) => {
      const active = page.dataset.page === pageName;
      page.hidden = !active;
      page.classList.toggle("active", active);
    });
    $$("[data-nav]").forEach((button) => {
      const active = button.dataset.nav === pageName;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (pageName === "records") renderRecords();
    if (pageName === "mine") renderMine();
    $("#main-content").focus({ preventScroll: true });
  }

  function greeting() {
    const hour = new Date().getHours();
    if (hour < 6) return "夜深了";
    if (hour < 11) return "早上好";
    if (hour < 14) return "中午好";
    if (hour < 18) return "下午好";
    return "晚上好";
  }

  function renderAll() {
    renderHome();
    renderCompanion();
    renderMeeting();
    renderRecords();
    renderMine();
    renderServiceStatus();
    renderVoiceControls();
  }

  function renderHome() {
    const now = parseDate(currentDateKey());
    $("#today-label").textContent = `${now.getFullYear()} · ${String(now.getMonth() + 1).padStart(2, "0")} · ${String(now.getDate()).padStart(2, "0")}`;
    $("#greeting-word").textContent = greeting();
    $("#home-name").textContent = store.profile.name;
    const today = store.entries[currentDateKey()];
    const mood = today ? moodById[today.mood] : realMood();
    const single = hasSinglePerson();
    const [focus, focusCopy] = focusLabel(product.state?.attention?.score, single);
    $("#home-mode").textContent = isMeetingLike() ? "多人场景中" : (single ? "看到你了" : "正在观察");
    $("#home-emotion").textContent = isMeetingLike() ? "已暂停" : (single ? mood.name : "暂未看到你");
    $("#home-focus").textContent = isMeetingLike() ? "已暂停" : focus;
    $("#header-state").textContent = product.connected ? (isMeetingLike() ? "会议陪伴中" : "单人陪伴中") : "本地记录可用";
    $("#today-mood").textContent = today ? mood.name : (single ? mood.name : "还未记录");
    $("#focus-summary").textContent = isMeetingLike() ? "多人场景中" : focus;
    setMoodImage("#today-face", mood || moodById.calm, mood?.name || "平静");
    $("#daily-quote").textContent = today?.note ? `“${today.note}”` : `“${single ? mood.note : focusCopy}”`;
    renderTrendChart($("#home-trend-chart"), dayDataFor(currentDateKey())?.emotionTrend || []);
  }

  function renderCompanion() {
    const single = hasSinglePerson();
    const meeting = isMeetingLike();
    const mood = realMood();
    const conf = realConfidence();
    const [focus, focusCopy] = focusLabel(product.state?.attention?.score, single);
    setMoodImage("#live-face", mood, mood.name);
    if (meeting) {
      $("#live-emotion").textContent = "多人场景中";
      $("#live-emotion-copy").textContent = "心屿不会在多人场景里判断个人情绪。";
      $("#emotion-quality").textContent = "已暂停";
      $("#live-focus").textContent = "--";
      $("#live-focus-copy").textContent = "多人场景中不展示个人专注状态。";
    } else if (single) {
      $("#live-emotion").textContent = mood.name;
      $("#live-emotion-copy").textContent = mood.note;
      $("#emotion-quality").textContent = conf >= 0.65 ? "较稳定" : "还在观察";
      $("#live-focus").textContent = focus;
      $("#live-focus-copy").textContent = focusCopy;
    } else {
      $("#live-emotion").textContent = "暂未看到你";
      $("#live-emotion-copy").textContent = "当你出现在画面里，心屿会自动更新这里。";
      $("#emotion-quality").textContent = "正在观察";
      $("#live-focus").textContent = "--";
      $("#live-focus-copy").textContent = "等待画面中的单人状态。";
    }
    const gesture = product.state?.gesture || {};
    $("#gesture-state").textContent = gesture.intent_ready || gesture.intent ? gestureLabel(gesture.intent) : "暂未识别到手势";
    $("#notify-toggle").textContent = store.notifyEnabled ? "关闭提醒" : "开启提醒";
    renderVoiceControls();
    renderQuickChat();
    const thread = $("#chat-thread");
    if (thread && !thread.dataset.seeded) {
      const memory = buildAssistantMemoryContext();
      thread.innerHTML = "";
      appendChatMessage("assistant", `我看到你今天下午有一段压力比较高，傍晚之后清楚了一些。今天还整理了${memory.meetingTitle || "会议"}内容，信息量不小。现在想聊聊刚才发生了什么吗？`);
      thread.dataset.seeded = "true";
    }
  }

  function renderQuickChat() {
    const container = $("#quick-chat");
    if (!container || container.childElementCount) return;
    const inputs = seedData.assistantMemory?.quickInputs || ["我今天有点累", "帮我整理一下今天", "给我一些放松建议", "记录一下我的情绪"];
    inputs.forEach((text) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.addEventListener("click", () => {
        appendChatMessage("user", text);
        const reply = buildXiaoyuReply(text);
        appendChatMessage("assistant", reply);
        speakText(reply, "chat_reply", false);
      });
      container.append(button);
    });
  }

  function gestureLabel(intent) {
    return {
      summon_xinyu: "小屿已被唤起",
      pause_or_mute: "已暂停提醒",
      feedback_positive: "已收到正向反馈",
      feedback_negative: "已收到反馈",
      capture_positive_moment: "可以记录一个积极瞬间"
    }[intent] || "暂未识别到手势";
  }

  function speakerDirection() {
    const s = product.state || {};
    const deg = s.sound_follow?.doa_deg ?? s.doa?.doa_deg;
    const speech = Boolean(s.sound_follow?.has_speech || s.doa?.has_speech);
    if (!speech || deg == null) return "暂未稳定";
    const value = Number(deg);
    if (value <= 35 || value >= 325) return "正前方";
    if (value > 35 && value < 145) return "右侧";
    if (value >= 145 && value <= 215) return "后方";
    return "左侧";
  }

  function peopleText() {
    const pose = product.state?.pose || {};
    const count = Number(pose[stableKey] ?? pose.count ?? 0);
    if (!count) return "暂未看到";
    if (count === 1) return "约 1 人";
    if (count === 2) return "约 2 人";
    return "多人";
  }

  function renderMeeting() {
    const active = Boolean(product.meetingActive);
    const meeting = seedData.meetings?.currentMeeting;
    $("#meeting-current-title").textContent = meeting?.title || "会议纪要";
    $("#meeting-status").textContent = active ? "记录中" : (meeting?.status || "已整理");
    $("#meeting-copy").textContent = active ? "正在为这次交流收束重点。" : (meeting?.summary || "小屿已为你整理会议主题、核心内容和待办事项。");
    $("#meeting-toggle").textContent = active ? "结束记录" : "结束记录";
    $("#speaker-direction").textContent = speakerDirection();
    $("#people-count").textContent = peopleText();
    const audio = product.state?.[audioKey] || {};
    $("#audio-quality").textContent = audio.noise_suppression?.enabled ? "清晰度增强已开启" : "清晰度稳定";
    if (meeting) summarizeMeeting({ silent: true });
  }

  function renderRecords() {
    renderCalendar();
    renderEntryDetail(selectedEntryKey);
    renderWeeklyLetter();
    renderMeetingHistory();
    renderDiaryHistory();
    renderWeeklyHistory();
  }

  function renderMine() {
    $("#profile-display-name").textContent = store.profile.name;
    $("#profile-name").value = store.profile.name;
    const tone = $(`input[name='reminderTone'][value='${store.profile.reminderTone}']`);
    if (tone) tone.checked = true;
  }

  function renderServiceStatus() {
    const dot = $("#service-dot");
    if (!dot) return;
    dot.className = "service-dot connected";
    $("#service-title").textContent = "心屿设备";
    $("#service-copy").textContent = "在线 · 电量 85%";
  }

  function renderMoodWheel() {
    const wheel = $("#mood-wheel");
    wheel.innerHTML = "";
    moods.forEach((mood, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mood-option";
      button.dataset.mood = mood.id;
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", String(chosenMood === mood.id));
      button.style.setProperty("--mood-color", mood.color);
      button.innerHTML = `${moodIcon(mood, "mood-ball", mood.name)}<strong>${mood.name}</strong>`;
      $(".mood-ball", button).style.setProperty("--tilt", `${index % 2 ? 2 : -2}deg`);
      button.addEventListener("click", () => chooseMood(mood.id));
      wheel.append(button);
    });
  }

  function chooseMood(id) {
    chosenMood = id;
    $$(".mood-option").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.mood === id)));
    $("#mood-next").disabled = false;
  }

  function renderChoiceOptions(containerId, items, name, selected = []) {
    const container = $(containerId);
    container.innerHTML = "";
    items.forEach((item) => {
      const label = document.createElement("label");
      label.className = "choice-chip";
      const checked = selected.includes(item);
      label.innerHTML = `<input type="${name === "weather" ? "radio" : "checkbox"}" name="${name}" value="${item}" ${checked ? "checked" : ""}><span>${item}</span>`;
      container.append(label);
    });
  }

  function openMoodDialog(key = currentDateKey()) {
    editingEntryKey = key;
    const existing = store.entries[key];
    chosenMood = existing?.mood || (hasSinglePerson() ? realMood().id : "");
    $("#mood-step-one").hidden = false;
    $("#mood-step-two").hidden = true;
    $("#mood-step-label").textContent = "STEP 1 OF 2";
    $("#mood-dialog-title").textContent = existing ? "想重新看看这一天吗？" : "亲，今天过得怎么样？";
    $("#mood-next").disabled = !chosenMood;
    $("#mood-note").value = existing?.note || "";
    $("#note-count").textContent = String((existing?.note || "").length);
    renderMoodWheel();
    renderChoiceOptions("#weather-options", weathers, "weather", [existing?.weather || weathers[0]]);
    renderChoiceOptions("#tag-options", tags, "tags", existing?.tags || []);
    $("#mood-dialog").showModal();
  }

  function showMoodDetailsStep() {
    const mood = moodById[chosenMood];
    if (!mood) return;
    $("#mood-step-one").hidden = true;
    $("#mood-step-two").hidden = false;
    $("#mood-step-label").textContent = "STEP 2 OF 2";
    $("#mood-dialog-title").textContent = "给这份心情留一点线索";
    $("#entry-form-date").textContent = formatDate(parseDate(editingEntryKey), true);
    $("#selected-mood-name").textContent = mood.name;
    setMoodImage("#selected-face", mood, mood.name);
    $("#mood-note").focus();
  }

  function saveMoodEntry(event) {
    event.preventDefault();
    const mood = moodById[chosenMood];
    if (!mood) return;
    const weather = $("input[name='weather']:checked")?.value || weathers[0];
    const selectedTags = $$("input[name='tags']:checked").map((input) => input.value);
    const existing = store.entries[editingEntryKey] || {};
    store.entries[editingEntryKey] = {
      ...existing,
      date: editingEntryKey,
      mood: chosenMood,
      weather,
      tags: selectedTags,
      note: $("#mood-note").value.trim() || mood.note,
      focus: Math.round(Number(product.state?.attention?.score || existing.focus || mood.score)),
      minutes: existing.minutes ?? 8
    };
    persist();
    $("#mood-dialog").close();
    selectedEntryKey = editingEntryKey;
    renderAll();
    showToast("这份心情已经留在岛上");
  }

  async function draftMoodWithXiaoyu() {
    const mood = moodById[chosenMood];
    if (!mood) return showToast("先选择一种心情");
    const button = $("#mood-ai");
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "小屿正在整理…";
    const currentText = $("#mood-note").value.trim();
    $("#mood-note").value = currentText || `今天的主要感受是${mood.name}。${mood.note}`;
    showToast("小屿已经整理好一版草稿");
    $("#note-count").textContent = String($("#mood-note").value.length);
    button.disabled = false;
    button.textContent = original;
  }

  function renderCalendar() {
    const year = calendarCursor.getFullYear();
    const month = calendarCursor.getMonth();
    $("#calendar-title").textContent = `${year} 年 ${month + 1} 月`;
    const grid = $("#calendar-grid");
    grid.innerHTML = "";
    const firstDay = new Date(year, month, 1).getDay();
    const totalDays = new Date(year, month + 1, 0).getDate();
    for (let i = 0; i < firstDay; i += 1) {
      const blank = document.createElement("span");
      blank.className = "calendar-blank";
      blank.setAttribute("aria-hidden", "true");
      grid.append(blank);
    }
    const today = currentDateKey();
    for (let day = 1; day <= totalDays; day += 1) {
      const key = dateKey(new Date(year, month, day));
      const entry = store.entries[key];
      const mood = entry ? moodById[entry.mood] : null;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `calendar-day${entry ? " has-entry" : ""}${key === today ? " today" : ""}${key === selectedEntryKey ? " selected" : ""}`;
      button.setAttribute("role", "gridcell");
      button.setAttribute("aria-label", `${month + 1}月${day}日${mood ? `，${mood.name}` : "，没有记录"}`);
      button.innerHTML = `<span class="day-number">${day}</span>${mood ? moodIcon(mood, "day-face", mood.name) : ""}`;
      if (mood && key !== selectedEntryKey) button.style.backgroundColor = `${mood.color}55`;
      button.addEventListener("click", () => {
        selectedEntryKey = key;
        renderRecords();
      });
      grid.append(button);
    }
  }

  function renderEntryDetail(key) {
    const entry = store.entries[key];
    $("#entry-empty").hidden = Boolean(entry);
    $("#entry-content").hidden = !entry;
    if (!entry) return;
    const mood = moodById[entry.mood] || moodById.calm;
    $("#entry-date").textContent = formatDate(parseDate(key), true);
    $("#entry-weather").textContent = entry.weather || "";
    setMoodImage("#entry-face", mood, mood.name);
    $("#entry-mood").textContent = mood.name;
    $("#entry-note").textContent = entry.note || mood.note;
    $("#entry-tags").innerHTML = (entry.tags || []).map((tag) => `<span>${escapeHTML(tag)}</span>`).join("");
    const day = dayDataFor(key) || entry.dailyRecord || {};
    $("#selected-day-summary").innerHTML = [
      ["今日主导状态", day.mainState || mood.name],
      ["专注状态", day.focusDisplay || "整体比较专注"],
      ["喝水", `${day.waterCups ?? "-"} / ${day.waterGoal ?? 8} 杯`],
      ["步数", `${day.steps ?? "-"} 步`],
      ["冥想", day.meditation ? "已完成" : "未完成"],
      ["会议", day.hadMeeting ? (day.meetingTitle || "已整理") : "今天没有会议记录"]
    ].map(([label, value]) => `<div class="info-chip"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`).join("");
    if (day.hadMeeting && day.meetingId) {
      $("#selected-day-summary").insertAdjacentHTML("beforeend", `<button type="button" class="info-chip" data-selected-meeting="${escapeHTML(day.meetingId)}"><span>会议纪要</span><strong>查看</strong></button>`);
      $("[data-selected-meeting]")?.addEventListener("click", () => openMeetingDetail(day.meetingId));
    }
    renderTrendChart($("#record-trend-chart"), day.emotionTrend || []);
    $("#entry-assistant-reply").textContent = entry.assistantReply || day.assistantReply || buildDiaryAssistantReply(day, entry.note || "");
  }

  function renderWeeklyLetter() {
    const day = dayDataFor(selectedEntryKey || currentDateKey());
    const report = seedData.weeklyReports?.[day?.weekKey] || null;
    if (report) {
      $("#weekly-letter-text").textContent = report.summary;
      return;
    }
    const entries = Object.values(store.entries).slice(-7);
    if (!entries.length) {
      $("#weekly-letter-text").textContent = "这一周还没有留下记录。可以从今天的一句话开始。";
      return;
    }
    const last = entries[entries.length - 1];
    const mood = moodById[last.mood] || moodById.calm;
    $("#weekly-letter-text").textContent = `最近的记录里，${mood.name}出现得比较近。${mood.note} 能停下来感受自己，本身就是一件珍贵的事。`;
  }

  function renderMeetingHistory() {
    const list = store.meetings || [];
    $("#meeting-history").innerHTML = list.length ? list.map((item) => `<button type="button" class="history-item" data-meeting-id="${escapeHTML(item.id || "")}"><small>${escapeHTML(item.date || "")}</small><strong>${escapeHTML(item.title || "会议纪要")}</strong><p>${escapeHTML(item.summary || "")}</p></button>`).join("") : "暂无会议整理。";
    $$("[data-meeting-id]").forEach((button) => button.addEventListener("click", () => openMeetingDetail(button.dataset.meetingId)));
  }

  function renderDiaryHistory() {
    const container = $("#diary-history");
    if (!container) return;
    const items = Object.values(store.entries)
      .filter((entry) => entry.note)
      .sort((a, b) => String(b.date).localeCompare(String(a.date)))
      .slice(0, 5);
    container.innerHTML = items.map((entry) => {
      const day = dayDataFor(entry.date) || {};
      return `<button type="button" class="history-item" data-diary-date="${escapeHTML(entry.date)}"><small>${escapeHTML(formatDate(parseDate(entry.date), true))}</small><strong>${escapeHTML(day.mainState || moodById[entry.mood]?.name || "状态平稳")}</strong><p>${escapeHTML(truncateText(entry.note, 64))}</p></button>`;
    }).join("");
    $$("[data-diary-date]").forEach((button) => button.addEventListener("click", () => {
      selectedEntryKey = button.dataset.diaryDate;
      calendarCursor = startOfMonth(parseDate(selectedEntryKey));
      renderRecords();
    }));
  }

  function renderWeeklyHistory() {
    const container = $("#weekly-history");
    if (!container) return;
    const items = Object.values(seedData.weeklyReports || {}).sort((a, b) => String(b.weekKey).localeCompare(String(a.weekKey))).slice(0, 5);
    container.innerHTML = items.map((report) => `<button type="button" class="history-item" data-week-key="${escapeHTML(report.weekKey)}"><small>${escapeHTML(report.rangeLabel)}</small><strong>${escapeHTML(report.title)}</strong><p>${escapeHTML(truncateText(report.summary, 72))}</p></button>`).join("");
    $$("[data-week-key]").forEach((button) => button.addEventListener("click", () => openWeeklyReportModal(button.dataset.weekKey)));
  }

  async function askXiaoyu(message, target) {
    return buildXiaoyuReply(message, buildAssistantMemoryContext());
  }

  async function sendChat() {
    const input = $("#chat-input");
    const message = input.value.trim();
    if (!message) return;
    appendChatMessage("user", message);
    input.value = "";
    const reply = await askXiaoyu(message);
    appendChatMessage("assistant", reply);
    speakText(reply, "chat_reply", false);
  }

  async function askAdvice() {
    const reply = buildXiaoyuReply("给我一些放松建议");
    $("#live-emotion-copy").textContent = reply;
    showToast("小屿给了你一句建议");
  }

  async function toggleMeeting() {
    const active = Boolean(product.meetingActive);
    const button = $("#meeting-toggle");
    button.disabled = true;
    const meeting = seedData.meetings?.currentMeeting;
    if (active) {
      product.meetingActive = false;
      product.meetingSid = "";
      if (meeting && !store.meetings.some((item) => item.id === meeting.id)) {
        store.meetings.unshift({
          id: meeting.id,
          title: meeting.title,
          date: meeting.date,
          time: meeting.time,
          duration: meeting.duration,
          status: meeting.status,
          summary: meeting.summary
        });
        persist();
      }
      summarizeMeeting();
      showToast("会议纪要已整理");
    } else {
      product.meetingActive = true;
      product.meetingSid = meeting?.id || "meeting-local";
      product.currentSid = product.meetingSid;
      showToast("会议记录已开始");
    }
    button.disabled = false;
    renderMeeting();
  }

  function meetingErrorText(code) {
    return {
      not_ready: "请先打开会议记录。",
      no_segments: "还没有可整理的会议内容。",
      unclear: "这段内容还不够清楚，可以稍后再整理。"
    }[code] || "暂时没有整理出内容，可以稍后再试。";
  }

  async function summarizeMeeting(options = {}) {
    const meeting = seedData.meetings?.currentMeeting;
    if (!meeting) {
      $("#meeting-notes").textContent = meetingErrorText("no_segments");
      return;
    }
    if (!store.meetings.some((item) => item.id === meeting.id)) {
      store.meetings.unshift({
        id: meeting.id,
        title: meeting.title,
        date: meeting.date,
        time: meeting.time,
        duration: meeting.duration,
        status: meeting.status,
        summary: meeting.summary
      });
      store.meetings = store.meetings.slice(0, 20);
      persist();
    }
    const core = (meeting.coreContents || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("");
    const todos = (meeting.todos || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("");
    $("#meeting-notes").innerHTML = `<div class="memory-note"><strong>${escapeHTML(meeting.title)}</strong><br>${escapeHTML(meeting.summary)}<br><br><strong>核心内容</strong><ul>${core}</ul><strong>待办事项</strong><ul>${todos}</ul></div>`;
    renderMeetingHistory();
    if (!options.silent) {
      openMeetingDetail(meeting.id);
      showToast("会议整理完成");
      speakText("会议整理好了，重点已经放在记录里。", "meeting_summary_ok", false);
    }
  }

  function openMeetingDetail(id = seedData.meetings?.currentMeeting?.id) {
    const meeting = seedData.meetings?.currentMeeting;
    if (!meeting || (id && id !== meeting.id)) return;
    const body = $("#meeting-dialog-body");
    const tags = (meeting.tags || []).map((tag) => `<span>${escapeHTML(tag)}</span>`).join("");
    body.innerHTML = `
      <p class="eyebrow">MEETING BRIEF</p>
      <h2 id="meeting-dialog-title">${escapeHTML(meeting.title)}</h2>
      <div class="sheet-meta"><span>${escapeHTML(meeting.date)} ${escapeHTML(meeting.time)}</span><span>${escapeHTML(meeting.duration)}</span>${tags}</div>
      ${markdownToMeetingHtml(meetingMarkdownText || meeting.minutesMarkdown || "")}
    `;
    $("#meeting-dialog").showModal();
  }

  function openDiaryModal(date = selectedEntryKey || currentDateKey()) {
    selectedEntryKey = date;
    const entry = store.entries[date] || {};
    const day = dayDataFor(date) || {};
    $("#diary-dialog-title").textContent = "今日日记";
    $("#diary-dialog-date").textContent = formatDate(parseDate(date), true);
    $("#diary-editor").value = entry.note || day.diary || "";
    $("#diary-dialog-reply").textContent = entry.assistantReply || day.assistantReply || "";
    $("#diary-dialog").showModal();
  }

  function saveDiaryFromDialog(event) {
    event.preventDefault();
    const key = selectedEntryKey || currentDateKey();
    const day = dayDataFor(key) || {};
    const text = $("#diary-editor").value.trim() || day.diary || "";
    const reply = buildDiaryAssistantReply(day, text);
    const existing = store.entries[key] || {};
    store.entries[key] = {
      ...existing,
      date: key,
      mood: existing.mood || day.moodId || "calm",
      weather: existing.weather || day.weather || "多云",
      tags: existing.tags || day.tags || ["日常"],
      note: text,
      focus: existing.focus || day.focusScore || 0,
      minutes: existing.minutes ?? (day.meditation ? 8 : 0),
      dailyRecord: day,
      assistantReply: reply
    };
    diaryOverrides[key] = { diary: text, assistantReply: reply };
    persistDiaryOverrides();
    persist();
    $("#diary-dialog-reply").textContent = reply;
    $("#diary-dialog").close();
    renderRecords();
    showToast("日记已保存，小屿也写下了回应");
  }

  function weekKeyForDate(date) {
    const day = dayDataFor(date);
    if (day?.weekKey) return day.weekKey;
    return typeof seedData.getWeekKey === "function" ? seedData.getWeekKey(date) : "";
  }

  function openWeeklyReportModal(weekKey = weekKeyForDate(selectedEntryKey || currentDateKey())) {
    const report = seedData.weeklyReports?.[weekKey] || (typeof seedData.openWeeklyReportModal === "function" ? seedData.openWeeklyReportModal(weekKey) : null);
    if (!report) return;
    $("#weekly-dialog-body").innerHTML = `
      <p class="eyebrow">WEEKLY LETTER</p>
      <h2 id="weekly-dialog-title">${escapeHTML(report.title)}</h2>
      <div class="sheet-meta"><span>${escapeHTML(report.rangeLabel)}</span><span>${escapeHTML(report.weekKey)}</span></div>
      <p>${escapeHTML(report.summary)}</p>
      <h3>这一周值得留下的部分</h3>
      <ul>${(report.highlights || []).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
      <h3>小屿的照顾提醒</h3>
      <p>${escapeHTML(report.careSummary || "")}</p>
      <p>${escapeHTML(report.suggestion || "")}</p>
    `;
    $("#weekly-dialog").showModal();
  }

  async function deviceAction(action) {
    const copy = {
      standby: "已为设备切换到待机提示。",
      sleep: "已为设备保留休息状态。",
      stop: "已停止当前页面内的设备动作提示。",
      calibrate: "校准提醒已记录，稍后可在设备端确认。"
    };
    $("#device-action-copy").textContent = copy[action] || "设备状态已更新。";
  }

  function toggleNotifications() {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then(() => toggleNotifications());
      return;
    }
    store.notifyEnabled = !store.notifyEnabled;
    if ("Notification" in window && Notification.permission !== "granted") store.notifyEnabled = false;
    persist();
    renderCompanion();
    showToast(store.notifyEnabled ? "提醒已开启" : "提醒已关闭");
  }

  async function sendLocalNotification(type, title, body, page = "companion", cooldownMin = 30) {
    if (!store.notifyEnabled) return false;
    const last = Number(store.lastSent[type] || 0);
    if (Date.now() - last < cooldownMin * 60000) return false;
    store.lastSent[type] = Date.now();
    persist();
    if (!("Notification" in window) || Notification.permission !== "granted") {
      showToast(body);
      return false;
    }
    const opts = { body, tag: `xinyu-${type}`, data: { page } };
    if (navigator.serviceWorker?.ready) (await navigator.serviceWorker.ready).showNotification(title, opts);
    else new Notification(title, opts);
    return true;
  }

  function evaluateNotifications() {
    if (!store.notifyEnabled || !product.state) return;
    if (hasSinglePerson() && Number(product.state.attention?.score || 100) < 45) {
      sendLocalNotification("low_focus", "心屿提醒", "注意力有点散，要不要换成十分钟轻专注？", "companion");
    }
    const intervention = product.state.proactive_intervention || {};
    if (intervention.active && intervention.message) {
      sendLocalNotification("emotion_care", "心屿在这儿", intervention.message, "companion", 60);
    }
  }

  function testNotification() {
    sendLocalNotification("test", "心屿提醒", "本地提醒已准备好。", "mine", 0).then((sent) => {
      if (!sent) showToast("提醒暂不可用，请检查浏览器权限");
    });
  }

  async function generateWeekWithXiaoyu() {
    const weekData = Object.values(store.entries).slice(-7);
    const reply = await askXiaoyu("请根据这周的情绪记录，写一段温柔、具体、适合放在周报里的总结。", weekData);
    $("#weekly-letter-text").textContent = reply;
    showToast("小屿写好这一周的回信了");
  }

  function saveProfile(event) {
    event.preventDefault();
    store.profile.name = $("#profile-name").value.trim() || "心屿用户";
    store.profile.reminderTone = $("input[name='reminderTone']:checked")?.value || "gentle";
    store.voice.enabled = Boolean($("#voice-enabled")?.checked);
    store.voice.volume = Math.max(0, Math.min(1, Number($("#voice-volume")?.value || 85) / 100));
    store.voice.rate = Math.max(0.7, Math.min(1.25, Number($("#voice-rate")?.value || 1)));
    store.voice.proactive = $("#voice-proactive")?.value || "gentle";
    persist();
    renderAll();
    showToast("设置已经保存");
  }

  function toggleVoice() {
    store.voice.enabled = !store.voice.enabled;
    if (!store.voice.enabled) stopVoice("disabled");
    persist();
    renderVoiceControls();
    showToast(store.voice.enabled ? "语音已开启" : "语音已关闭");
  }

  function replayVoice() {
    if (!store.voice.lastText) return;
    speakText(store.voice.lastText, store.voice.lastReason || "replay", true);
  }

  function resetData() {
    if (!window.confirm("确定重置本地记录吗？")) return;
    store = initialStore();
    persist();
    renderAll();
    showToast("本地记录已重置");
  }

  function bindEvents() {
    $$("[data-nav]").forEach((button) => button.addEventListener("click", () => goTo(button.dataset.nav)));
    $$("[data-go]").forEach((button) => button.addEventListener("click", () => goTo(button.dataset.go)));
    $$("[data-open-mood]").forEach((button) => button.addEventListener("click", () => openMoodDialog()));
    $$("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => $("#mood-dialog").close()));
    $("#mood-next").addEventListener("click", showMoodDetailsStep);
    $("#mood-back").addEventListener("click", () => {
      $("#mood-step-one").hidden = false;
      $("#mood-step-two").hidden = true;
      $("#mood-step-label").textContent = "STEP 1 OF 2";
      $("#mood-dialog-title").textContent = "亲，今天过得怎么样？";
    });
    $("#mood-form").addEventListener("submit", saveMoodEntry);
    $("#mood-ai").addEventListener("click", draftMoodWithXiaoyu);
    $("#mood-note").addEventListener("input", (event) => { $("#note-count").textContent = String(event.target.value.length); });
    $("#calendar-prev").addEventListener("click", () => { calendarCursor = new Date(calendarCursor.getFullYear(), calendarCursor.getMonth() - 1, 1); renderCalendar(); });
    $("#calendar-next").addEventListener("click", () => { calendarCursor = new Date(calendarCursor.getFullYear(), calendarCursor.getMonth() + 1, 1); renderCalendar(); });
    $("#entry-edit").addEventListener("click", () => openDiaryModal(selectedEntryKey || currentDateKey()));
    $("#entry-weekly").addEventListener("click", () => openWeeklyReportModal(weekKeyForDate(selectedEntryKey || currentDateKey())));
    $("#entry-delete")?.addEventListener("click", () => {
      if (!selectedEntryKey || !store.entries[selectedEntryKey]) return;
      delete store.entries[selectedEntryKey];
      selectedEntryKey = "";
      persist();
      renderRecords();
    });
    $("#ask-advice").addEventListener("click", askAdvice);
    $("#chat-send").addEventListener("click", sendChat);
    $("#chat-input").addEventListener("keydown", (event) => { if (event.key === "Enter") sendChat(); });
    $("#meeting-toggle").addEventListener("click", toggleMeeting);
    $("#meeting-summary").addEventListener("click", summarizeMeeting);
    $("#refresh-letter").addEventListener("click", () => openWeeklyReportModal(weekKeyForDate(selectedEntryKey || currentDateKey())));
    $("#diary-form").addEventListener("submit", saveDiaryFromDialog);
    $$("[data-close-diary]").forEach((button) => button.addEventListener("click", () => $("#diary-dialog").close()));
    $$("[data-close-weekly]").forEach((button) => button.addEventListener("click", () => $("#weekly-dialog").close()));
    $$("[data-close-meeting]").forEach((button) => button.addEventListener("click", () => $("#meeting-dialog").close()));
    $("#meeting-dialog").addEventListener("click", (event) => { if (event.target === $("#meeting-dialog")) $("#meeting-dialog").close(); });
    $("#diary-dialog").addEventListener("click", (event) => { if (event.target === $("#diary-dialog")) $("#diary-dialog").close(); });
    $("#weekly-dialog").addEventListener("click", (event) => { if (event.target === $("#weekly-dialog")) $("#weekly-dialog").close(); });
    $("#settings-form").addEventListener("submit", saveProfile);
    $("#notify-toggle").addEventListener("click", toggleNotifications);
    $("#notify-test").addEventListener("click", testNotification);
    $("#voice-toggle")?.addEventListener("click", toggleVoice);
    $("#voice-stop")?.addEventListener("click", () => stopVoice("home_button"));
    $("#voice-replay")?.addEventListener("click", replayVoice);
    $("#reset-data").addEventListener("click", resetData);
    $$("[data-device-action]").forEach((button) => button.addEventListener("click", () => deviceAction(button.dataset.deviceAction)));
    $("#mood-dialog").addEventListener("click", (event) => { if (event.target === $("#mood-dialog")) $("#mood-dialog").close(); });
  }

  bindEvents();
  loadMeetingMinutesMarkdown().then(() => renderMeeting());
  renderAll();
  connectWS();
  refreshState(true);
  pollTimer = window.setInterval(() => refreshState(true), 1000);
  focusTicker = window.setInterval(renderHome, 1000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  window.addEventListener("beforeunload", () => {
    product.meetingSid = "";
  });
})();
