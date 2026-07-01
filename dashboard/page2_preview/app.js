(() => {
  "use strict";

  const STORAGE_KEY = "xinyu_product_home_v2";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
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
  let selectedEntryKey = "";
  let editingEntryKey = "";
  let chosenMood = "";
  let calendarCursor = startOfMonth(new Date());
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

  function initialStore() {
    return {
      version: 2,
      profile: { name: localStorage.getItem("xinyu_user_name") || "心屿用户", reminderTone: "gentle" },
      entries: {},
      meetings: [],
      notifyEnabled: localStorage.getItem("xinyu_notify_enabled") === "true",
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
        entries: saved.entries || {},
        meetings: saved.meetings || [],
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
  }

  function dateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }
  function parseDate(key) { const [y, m, d] = key.split("-").map(Number); return new Date(y, m - 1, d); }
  function startOfMonth(date) { return new Date(date.getFullYear(), date.getMonth(), 1); }
  function formatDate(date, includeYear = false) {
    return `${includeYear ? `${date.getFullYear()}年` : ""}${date.getMonth() + 1}月${date.getDate()}日`;
  }
  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
  }
  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2400);
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

  async function apiPost(path, payload = {}) {
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false || data.success === false) throw new Error(data.error || data.reason || data.message || "暂不可用");
      product.connected = true;
      return data;
    } catch (error) {
      product.connected = false;
      product.notice = "暂时没有连接到心屿服务，本地记录仍可继续使用。";
      renderServiceStatus();
      throw error;
    }
  }

  async function refreshState(silent = false) {
    if (ws && ws.readyState === WebSocket.OPEN && Date.now() - wsLastAt < 1600) return;
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      const body = await response.json();
      applyState(body.data || body);
    } catch (_) {
      product.connected = false;
      if (!silent) showToast("实时陪伴暂不可用，本地记录可以继续使用");
      renderAll();
    }
  }

  function connectWS() {
    if (location.protocol === "file:") return;
    try {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${scheme}://${location.host}/ws`);
      ws.onopen = () => {
        wsLastAt = Date.now();
        product.connected = true;
        ws.send("request_state");
      };
      ws.onmessage = (event) => {
        wsLastAt = Date.now();
        try {
          const msg = JSON.parse(event.data);
          applyState(msg.data || msg);
        } catch (_) {}
      };
      ws.onclose = () => setTimeout(connectWS, 1800);
      ws.onerror = () => {};
    } catch (_) {
      setTimeout(connectWS, 1800);
    }
  }

  function applyState(next) {
    product.state = next || {};
    product.connected = true;
    const feature = String(product.state.control?.active_feature || "");
    const sid = String(product.state.control?.[sidKey] || product.state[sidKey] || "");
    if (sid) product.currentSid = sid;
    product.meetingActive = feature.includes("multi") || feature.includes("meeting") || Boolean(product.state.conversation?.active);
    product.mode = product.meetingActive ? "meeting" : "single";
    mergeTodayObservation();
    renderAll();
    evaluateNotifications();
  }

  function mergeTodayObservation() {
    if (!hasSinglePerson()) return;
    const key = dateKey(new Date());
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
  }

  function renderHome() {
    const now = new Date();
    $("#today-label").textContent = `${now.getFullYear()} · ${String(now.getMonth() + 1).padStart(2, "0")} · ${String(now.getDate()).padStart(2, "0")}`;
    $("#greeting-word").textContent = greeting();
    $("#home-name").textContent = store.profile.name;
    const today = store.entries[dateKey(now)];
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
    const conv = product.state?.conversation || {};
    const active = Boolean(product.meetingActive || conv.active);
    $("#meeting-status").textContent = active ? "记录中" : "未开始";
    $("#meeting-copy").textContent = active ? "心屿正在记录可整理的发言片段。" : "点击开始后，心屿会记录可整理的发言片段。";
    $("#meeting-toggle").textContent = active ? "结束会议记录" : "开始会议记录";
    $("#speaker-direction").textContent = speakerDirection();
    $("#people-count").textContent = peopleText();
    const audio = product.state?.[audioKey] || {};
    $("#audio-quality").textContent = audio.noise_suppression?.enabled ? "录音质量增强已开启" : "当前使用基础录音模式";
  }

  function renderRecords() {
    renderCalendar();
    renderEntryDetail(selectedEntryKey);
    renderWeeklyLetter();
    renderMeetingHistory();
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
    dot.className = `service-dot ${product.connected ? "connected" : "resting"}`;
    $("#service-title").textContent = product.connected ? "心屿陪伴可用" : "本地记录可用";
    $("#service-copy").textContent = product.connected ? "实时陪伴、会议和小屿回应已经可以使用。" : (product.notice || "本地记录始终可用。");
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

  function openMoodDialog(key = dateKey(new Date())) {
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
    try {
      const data = await apiPost("/api/reflect", {
        mode: "diary",
        emotion: mood.name,
        attention: Math.round(Number(product.state?.attention?.score || mood.score)),
        user_text: currentText,
        duration_min: 8
      });
      if (data?.diary) $("#mood-note").value = currentText ? `${currentText}\n\n${data.diary}` : data.diary;
      showToast(data?.reply || "小屿已经整理好一版草稿");
    } catch (_) {
      $("#mood-note").value = currentText || `今天的主要感受是${mood.name}。${mood.note}`;
      showToast("已为你保留一版本地草稿");
    }
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
    const today = dateKey(new Date());
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
  }

  function renderWeeklyLetter() {
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
    $("#meeting-history").innerHTML = list.length ? list.map((item) => `<div class="demo-note"><strong>${escapeHTML(item.date)}</strong><br>${escapeHTML(item.summary)}</div>`).join("") : "暂无会议整理。";
  }

  async function askXiaoyu(message, target) {
    const today = store.entries[dateKey(new Date())];
    const mood = today ? moodById[today.mood] : realMood();
    const payload = {
      message,
      emotion: mood.name,
      user_name: store.profile.name,
      context: `今日心情：${mood.name}；专注：${Math.round(Number(product.state?.attention?.score || mood.score))}；日记：${today?.note || "还没有写下内容"}`,
      diary_text: today?.note || ""
    };
    try {
      const data = await apiPost("/api/chat", payload);
      return data?.reply || `${mood.note} 可以先把最想被理解的一件事写下来。`;
    } catch (_) {
      return `${mood.note} 可以先把最想被理解的一件事写下来。`;
    }
  }

  async function sendChat() {
    const input = $("#chat-input");
    const message = input.value.trim();
    if (!message) return;
    const thread = $("#chat-thread");
    thread.insertAdjacentHTML("beforeend", `<div class="chat-bubble user">${escapeHTML(message)}</div>`);
    input.value = "";
    const pending = document.createElement("div");
    pending.className = "chat-bubble";
    pending.textContent = "小屿正在读你的这句话……";
    thread.append(pending);
    pending.textContent = await askXiaoyu(message);
    thread.scrollTop = thread.scrollHeight;
  }

  async function askAdvice() {
    const reply = await askXiaoyu("请基于我现在的情绪和专注状态，给一句温和、具体的建议。");
    $("#live-emotion-copy").textContent = reply;
    showToast("小屿给了你一句建议");
  }

  async function toggleMeeting() {
    const active = Boolean(product.meetingActive || product.state?.conversation?.active);
    const button = $("#meeting-toggle");
    button.disabled = true;
    try {
      if (active) {
        await apiPost("/api/multi_track/stop", { finalize: true, [sidKey]: product.meetingSid || product.currentSid });
        product.meetingActive = false;
        product.meetingSid = "";
        showToast("会议记录已结束");
      } else {
        const data = await apiPost("/api/multi_track/start", { save_audio: true });
        product.meetingActive = true;
        product.meetingSid = String(data?.[sidKey] || "");
        product.currentSid = product.meetingSid || product.currentSid;
        showToast("会议记录已开始");
      }
      await refreshState(true);
    } catch (_) {
      showToast(active ? "暂时无法结束会议记录" : "暂时无法开始会议记录");
    }
    button.disabled = false;
    renderMeeting();
  }

  function meetingErrorText(code) {
    return {
      recording_not_started: "请先开始会议记录。",
      no_segments: "还没有录到可整理的发言。",
      asr_empty: "这段录音太短或声音不清楚，可以再试一次。"
    }[code] || "暂时没有整理出内容，可以稍后再试。";
  }

  async function summarizeMeeting() {
    $("#meeting-notes").textContent = "小屿正在整理这段交流……";
    try {
      const data = await apiPost("/api/meeting/summarize", {});
      if (data.ok === false) throw data;
      const summary = data.summary || data.reply || "这次交流已经整理完成。";
      const diary = data.diary || "可以把这次交流中最重要的一句话写进今天。";
      const item = { id: `meeting_${Date.now()}`, date: dateKey(new Date()), summary, diary };
      store.meetings.unshift(item);
      store.meetings = store.meetings.slice(0, 20);
      persist();
      $("#meeting-notes").innerHTML = `<div class="demo-note"><strong>这次交流的回声</strong><br>${escapeHTML(summary)}<br><br>${escapeHTML(diary)}</div>`;
      renderMeetingHistory();
      showToast("会议整理完成");
    } catch (error) {
      const code = error?.error_code || error?.message;
      $("#meeting-notes").textContent = meetingErrorText(code);
    }
  }

  async function deviceAction(action) {
    const map = {
      standby: "/api/gimbal/standby",
      sleep: "/api/gimbal/sleep",
      stop: "/api/gimbal/stop",
      calibrate: "/api/gimbal/calibrate"
    };
    try {
      await apiPost(map[action], { [sidKey]: product.currentSid || product.meetingSid || "" });
      $("#device-action-copy").textContent = "已发送。";
    } catch (_) {
      $("#device-action-copy").textContent = "暂不可用，请确认控制服务已启动。";
    }
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
    persist();
    renderAll();
    showToast("设置已经保存");
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
    $("#entry-edit").addEventListener("click", () => openMoodDialog(selectedEntryKey));
    $("#entry-delete").addEventListener("click", () => {
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
    $("#refresh-letter").addEventListener("click", generateWeekWithXiaoyu);
    $("#settings-form").addEventListener("submit", saveProfile);
    $("#notify-toggle").addEventListener("click", toggleNotifications);
    $("#notify-test").addEventListener("click", testNotification);
    $("#reset-data").addEventListener("click", resetData);
    $$("[data-device-action]").forEach((button) => button.addEventListener("click", () => deviceAction(button.dataset.deviceAction)));
    $("#mood-dialog").addEventListener("click", (event) => { if (event.target === $("#mood-dialog")) $("#mood-dialog").close(); });
  }

  bindEvents();
  renderAll();
  connectWS();
  refreshState(true);
  pollTimer = window.setInterval(() => refreshState(true), 1000);
  focusTicker = window.setInterval(renderHome, 1000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  window.addEventListener("beforeunload", () => {
    if (!product.meetingSid) return;
    const blob = new Blob([JSON.stringify({ finalize: true, [sidKey]: product.meetingSid })], { type: "application/json" });
    navigator.sendBeacon("/api/multi_track/stop", blob);
  });
})();
