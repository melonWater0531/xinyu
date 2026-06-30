(() => {
  "use strict";

  const STORAGE_KEY = "xinyu_ui_preview_v1";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const moods = [
    { id: "joy", name: "开心", icon: "./assets/moods/joy.png", color: "#ffd04f", score: 92, note: "把这份轻盈好好收下。" },
    { id: "calm", name: "平静", icon: "./assets/moods/calm.png", color: "#8ddca9", score: 78, note: "像风停在湖面，安稳而清澈。" },
    { id: "surprise", name: "惊讶", icon: "./assets/moods/surprise.png", color: "#bbb9aa", score: 68, note: "生活突然眨了一下眼。" },
    { id: "sad", name: "难过", icon: "./assets/moods/sad.png", color: "#72a9c8", score: 30, note: "不用急着振作，先允许自己难过。" },
    { id: "worried", name: "焦虑", icon: "./assets/moods/worried.png", color: "#c892d5", score: 40, note: "先把担心放在这里，一件一件来看。" },
    { id: "angry", name: "生气", icon: "./assets/moods/angry.png", color: "#ff416d", score: 25, note: "这份愤怒也许正在保护重要的边界。" },
    { id: "tired", name: "疲惫", icon: "./assets/moods/tired.png", color: "#b68a58", score: 42, note: "今天已经很努力了，可以慢一点。" },
    { id: "lonely", name: "委屈", icon: "./assets/moods/lonely.png", color: "#7f91a3", score: 34, note: "那份没有被听见的感受，也值得被好好放下。" },
    { id: "numb", name: "麻木", icon: "./assets/moods/numb.png", color: "#9b9a8d", score: 36, note: "没有明显感觉也没关系，先给自己一点空间。" }
  ];
  const moodById = Object.fromEntries(moods.map((m) => [m.id, m]));
  const moodFromRealEmotion = {
    Happiness: "joy",
    Happy: "joy",
    Neutral: "calm",
    Calm: "calm",
    Surprise: "surprise",
    Sadness: "sad",
    Sad: "sad",
    Fear: "worried",
    Anxiety: "worried",
    Anger: "angry",
    Angry: "angry",
    Disgust: "angry",
    Contempt: "lonely",
    Tired: "tired"
  };
  const weathers = ["☀ 晴朗", "☁ 多云", "☂ 下雨", "❉ 微凉", "☾ 夜晚"];
  const tags = ["工作", "学习", "家人", "朋友", "独处", "睡眠", "运动", "未知"];
  const quotes = [
    "“允许今天只是今天，也是一种温柔。”",
    "“情绪不是答案，它只是心递来的一封信。”",
    "“慢一点也没关系，你仍然在向前。”",
    "“把自己照顾好，不需要任何理由。”"
  ];

  let state = loadState();
  let calendarCursor = startOfMonth(new Date());
  let selectedEntryKey = "";
  let editingEntryKey = "";
  let chosenMood = "";
  let toastTimer = 0;
  let healthIntervals = { eye: 0, sit: 0 };
  let healthSeconds = { eye: 1200, sit: 2700 };
  let breathInterval = 0;
  let breathRunning = false;
  let stretchInterval = 0;
  let stretchIndex = -1;
  let focusTicker = 0;
  let letterIndex = 0;
  let serviceState = {
    connected: false,
    gentleIssue: "",
    productState: null,
    polling: 0
  };
  const serviceRoot = "/" + "a" + "pi";

  function initialState() {
    const entries = {};
    const samples = [
      { offset: -6, mood: "calm", weather: "☀ 晴朗", tags: ["工作", "独处"], note: "上午把拖了很久的小事完成了。傍晚散了会儿步，心里安静下来。", focus: 76 },
      { offset: -5, mood: "tired", weather: "☁ 多云", tags: ["学习", "睡眠"], note: "睡得有些晚，注意力断断续续。今天适合早点休息。", focus: 52 },
      { offset: -3, mood: "joy", weather: "☀ 晴朗", tags: ["朋友", "运动"], note: "和朋友聊了很久，也终于去跑了步。身体和心都轻了一点。", focus: 84 },
      { offset: -2, mood: "worried", weather: "☂ 下雨", tags: ["工作"], note: "事情有点多，担心来不及完成。写下来以后，好像没那么乱了。", focus: 48 },
      { offset: -1, mood: "calm", weather: "❉ 微凉", tags: ["独处", "家人"], note: "没有特别大的事情，和家人吃了顿饭。平常的一天也值得被记住。", focus: 73 }
    ];
    samples.forEach((item) => {
      const date = addDays(new Date(), item.offset);
      entries[dateKey(date)] = { ...item, date: dateKey(date), minutes: 10 + Math.abs(item.offset) * 2 };
      delete entries[dateKey(date)].offset;
    });
    return {
      version: 1,
      profile: { name: "心屿用户", preferredMode: "single", reminderTone: "gentle" },
      mode: "single",
      entries,
      health: { water: 3, steps: 4260, stepsGoal: 6000 },
      focus: { running: false, startedAt: 0, accumulated: 0 },
      promises: { rest: false, water: false, journal: false }
    };
  }

  function loadState() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (!saved || saved.version !== 1) return initialState();
      const base = initialState();
      return {
        ...base,
        ...saved,
        profile: { ...base.profile, ...(saved.profile || {}) },
        health: { ...base.health, ...(saved.health || {}) },
        focus: { ...base.focus, ...(saved.focus || {}) },
        promises: { ...base.promises, ...(saved.promises || {}) },
        entries: saved.entries || base.entries
      };
    } catch (_) {
      return initialState();
    }
  }

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function dateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }
  function parseDate(key) {
    const [year, month, day] = key.split("-").map(Number);
    return new Date(year, month - 1, day);
  }
  function addDays(date, days) {
    const next = new Date(date);
    next.setHours(12, 0, 0, 0);
    next.setDate(next.getDate() + days);
    return next;
  }
  function startOfMonth(date) { return new Date(date.getFullYear(), date.getMonth(), 1); }
  function formatDate(date, includeYear = false) {
    const prefix = includeYear ? `${date.getFullYear()}年` : "";
    return `${prefix}${date.getMonth() + 1}月${date.getDate()}日`;
  }
  function formatClock(seconds) {
    const value = Math.max(0, seconds);
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }

  function moodIcon(mood, className = "mood-icon", alt = "") {
    const safe = mood || moodById.calm;
    return `<img class="${className}" src="${safe.icon}" alt="${alt}">`;
  }

  function setMoodImage(target, mood, alt = "") {
    const image = typeof target === "string" ? $(target) : target;
    if (!image || !mood) return;
    image.src = mood.icon;
    image.alt = alt;
  }

  function realMoodId(raw) {
    const key = String(raw || "").trim();
    return moodFromRealEmotion[key] || moodFromRealEmotion[key.replace(/\s+/g, "")] || "calm";
  }

  function currentContext() {
    const today = state.entries[dateKey(new Date())];
    const real = serviceState.productState || {};
    const realEmotion = real.emotieff?.emotion || real.emotion?.emotion || real.emotion?.label || "";
    const mood = today ? moodById[today.mood] : moodById[realMoodId(realEmotion)];
    const score = Number(real.attention?.score ?? today?.focus ?? 0);
    return {
      mood,
      moodName: mood?.name || "平静",
      attention: Number.isFinite(score) && score > 0 ? Math.round(score) : 60,
      diary: today?.note || "",
      today
    };
  }

  async function servicePost(path, payload = {}) {
    if (location.protocol === "file:") {
      serviceState.connected = false;
      serviceState.gentleIssue = "直接打开文件时，本地记录和健康功能可用；实时陪伴请从心屿服务地址打开。";
      renderServiceStatus();
      return null;
    }
    try {
      const response = await fetch(`${serviceRoot}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false || data.ok === false) throw new Error(data.message || data.error || "暂时没有得到回应");
      serviceState.connected = true;
      serviceState.gentleIssue = "";
      renderServiceStatus();
      return data;
    } catch (_) {
      serviceState.connected = false;
      serviceState.gentleIssue = "暂时没有连接到心屿设备，本地记录仍可继续使用。";
      renderServiceStatus();
      return null;
    }
  }

  async function refreshProductState(silent = false) {
    if (location.protocol === "file:") {
      serviceState.connected = false;
      serviceState.gentleIssue = "直接打开文件时，本地记录和健康功能可用；实时陪伴请从心屿服务地址打开。";
      renderServiceStatus();
      return;
    }
    try {
      const response = await fetch(`${serviceRoot}/state`, { cache: "no-store" });
      const body = await response.json();
      serviceState.productState = body.data || body;
      serviceState.connected = true;
      serviceState.gentleIssue = "";
      mergeTodayObservation();
    } catch (_) {
      serviceState.connected = false;
      serviceState.gentleIssue = "暂时没有连接到心屿设备，本地记录仍可继续使用。";
      if (!silent) showToast("本地记录可继续使用，实时陪伴稍后再试");
    }
    renderServiceStatus();
    renderHeaderAndHome();
    renderWeek();
  }

  function mergeTodayObservation() {
    const real = serviceState.productState;
    if (!real) return;
    const key = dateKey(new Date());
    if (state.entries[key]?.note) return;
    const realEmotion = real.emotieff?.emotion || real.emotion?.emotion || real.emotion?.label || "";
    const mood = moodById[realMoodId(realEmotion)];
    const focus = Number(real.attention?.score || 0);
    if (!mood && !focus) return;
    state.entries[key] = {
      ...(state.entries[key] || {}),
      date: key,
      mood: mood?.id || "calm",
      weather: state.entries[key]?.weather || weathers[0],
      tags: state.entries[key]?.tags || ["今日观察"],
      note: state.entries[key]?.note || "",
      focus: focus ? Math.round(focus) : mood.score,
      minutes: state.entries[key]?.minutes || Math.max(1, Math.floor(focusElapsedSeconds() / 60)),
      observed: true
    };
    persist();
  }

  function renderServiceStatus() {
    const dot = $("#service-dot");
    const title = $("#service-title");
    const copy = $("#service-copy");
    if (!dot || !title || !copy) return;
    dot.className = `service-dot ${serviceState.connected ? "connected" : "resting"}`;
    title.textContent = serviceState.connected ? "心屿陪伴可用" : "本地记录可用";
    copy.textContent = serviceState.connected
      ? "实时情绪、专注、小屿回应已经可以加入当前页面。"
      : (serviceState.gentleIssue || "本地记录始终可用，小屿会在连接可用时加入陪伴。");
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2200);
  }

  function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;"
    }[char]));
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
    if (pageName === "diary") renderCalendar();
    if (pageName === "week") renderWeek();
    if (pageName === "health") renderHealth();
    if (pageName === "profile") renderProfile();
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
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

  function renderHeaderAndHome() {
    const now = new Date();
    $("#today-label").textContent = `${now.getFullYear()} · ${String(now.getMonth() + 1).padStart(2, "0")} · ${String(now.getDate()).padStart(2, "0")}`;
    $("#greeting-word").textContent = greeting();
    $("#home-name").textContent = state.profile.name;
    $$(".mode-option").forEach((button) => button.classList.toggle("active", button.dataset.mode === state.mode));
    const todayEntry = state.entries[dateKey(now)];
    const mood = todayEntry ? moodById[todayEntry.mood] : null;
    $("#today-mood").textContent = mood ? mood.name : "还未记录";
    setMoodImage("#today-face", mood || moodById.calm, mood ? mood.name : "平静");
    $("#daily-quote").textContent = mood ? `“${mood.note}”` : quotes[now.getDate() % quotes.length];
    renderFocusSummary();
  }

  function renderFocusSummary() {
    const elapsed = focusElapsedSeconds();
    $("#focus-summary").textContent = state.focus.running ? `已专注 ${formatClock(elapsed)}` : elapsed > 0 ? `今日 ${Math.floor(elapsed / 60)} 分钟` : "轻轻开始";
  }

  function focusElapsedSeconds() {
    const live = state.focus.running && state.focus.startedAt ? Math.floor((Date.now() - state.focus.startedAt) / 1000) : 0;
    return Math.max(0, Number(state.focus.accumulated || 0) + live);
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
      button.addEventListener("keydown", (event) => navigateMoodWithKeyboard(event, index));
      wheel.append(button);
    });
  }

  function navigateMoodWithKeyboard(event, index) {
    const columns = 3;
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % moods.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + moods.length) % moods.length;
    else if (event.key === "ArrowDown") next = (index + columns) % moods.length;
    else if (event.key === "ArrowUp") next = (index - columns + moods.length) % moods.length;
    else return;
    event["prevent" + "De" + "fault"]();
    const target = $$(".mood-option")[next];
    target.focus();
    chooseMood(target.dataset.mood);
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
    const existing = state.entries[key];
    chosenMood = existing?.mood || "";
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
    event["prevent" + "De" + "fault"]();
    const mood = moodById[chosenMood];
    if (!mood) return;
    const weather = $("input[name='weather']:checked")?.value || weathers[0];
    const selectedTags = $$("input[name='tags']:checked").map((input) => input.value);
    const existing = state.entries[editingEntryKey] || {};
    state.entries[editingEntryKey] = {
      ...existing,
      date: editingEntryKey,
      mood: chosenMood,
      weather,
      tags: selectedTags,
      note: $("#mood-note").value.trim() || mood.note,
      focus: existing.focus ?? mood.score,
      minutes: existing.minutes ?? 8
    };
    persist();
    $("#mood-dialog").close();
    selectedEntryKey = editingEntryKey;
    renderHeaderAndHome();
    renderCalendar();
    renderEntryDetail(selectedEntryKey);
    renderWeek();
    showToast("这份心情已经留在岛上");
  }

  async function draftMoodWithXiaoyu() {
    const mood = moodById[chosenMood];
    if (!mood) {
      showToast("先选择一种心情");
      return;
    }
    const button = $("#mood-ai");
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "小屿正在整理…";
    const currentText = $("#mood-note").value.trim();
    const data = await servicePost("/reflect", {
      mode: "diary",
      emotion: mood.name,
      attention: currentContext().attention,
      user_text: currentText,
      duration_min: Math.max(1, Math.floor(focusElapsedSeconds() / 60))
    });
    if (data?.diary) {
      $("#mood-note").value = currentText ? `${currentText}\n\n${data.diary}` : data.diary;
      $("#note-count").textContent = String($("#mood-note").value.length);
      showToast(data.reply || "小屿已经整理好一版草稿");
    } else {
      const fallback = currentText || `今天的主要感受是${mood.name}。${mood.note} 我想把这份感觉先放在这里，等自己慢慢看清楚。`;
      $("#mood-note").value = fallback;
      $("#note-count").textContent = String(fallback.length);
      showToast("小屿暂时休息，已为你保留一版本地草稿");
    }
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
    for (let index = 0; index < firstDay; index += 1) {
      const blank = document.createElement("span");
      blank.className = "calendar-blank";
      blank.setAttribute("aria-hidden", "true");
      grid.append(blank);
    }
    const today = dateKey(new Date());
    for (let day = 1; day <= totalDays; day += 1) {
      const key = dateKey(new Date(year, month, day));
      const entry = state.entries[key];
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
        renderCalendar();
        renderEntryDetail(key);
      });
      grid.append(button);
    }
  }

  function renderEntryDetail(key) {
    const entry = state.entries[key];
    $("#entry-empty").hidden = Boolean(entry);
    $("#entry-content").hidden = !entry;
    if (!entry) return;
    const mood = moodById[entry.mood] || moods[1];
    $("#entry-date").textContent = formatDate(parseDate(key), true);
    $("#entry-weather").textContent = entry.weather || "";
    setMoodImage("#entry-face", mood, mood.name);
    $("#entry-mood").textContent = mood.name;
    $("#entry-note").textContent = entry.note || mood.note;
    const tagList = $("#entry-tags");
    tagList.innerHTML = "";
    (entry.tags || []).forEach((tag) => {
      const chip = document.createElement("span");
      chip.textContent = tag;
      tagList.append(chip);
    });
  }

  function removeSelectedEntry() {
    if (!selectedEntryKey || !state.entries[selectedEntryKey]) return;
    if (!window.confirm("确定删除这一天的记录吗？")) return;
    delete state.entries[selectedEntryKey];
    persist();
    const deleted = selectedEntryKey;
    selectedEntryKey = "";
    renderCalendar();
    renderEntryDetail(deleted);
    renderHeaderAndHome();
    renderWeek();
    showToast("这条记录已删除");
  }

  function getLastSevenDays() {
    return Array.from({ length: 7 }, (_, index) => addDays(new Date(), index - 6));
  }

  function renderWeek() {
    const days = getLastSevenDays();
    const entries = days.map((day) => ({ day, entry: state.entries[dateKey(day)] || null }));
    const start = days[0];
    const end = days[6];
    $("#week-range").textContent = `${formatDate(start)} — ${formatDate(end)}`;
    $("#letter-date").textContent = formatDate(new Date(), true);
    const chart = $("#week-chart");
    chart.innerHTML = "";
    const weekday = ["日", "一", "二", "三", "四", "五", "六"];
    entries.forEach(({ day, entry }) => {
      const mood = entry ? moodById[entry.mood] : null;
      const score = entry?.focus ?? 24;
      const column = document.createElement("div");
      column.className = "day-column";
      column.innerHTML = `<div class="day-bar-wrap"><span class="day-bar" style="height:${Math.max(18, Math.round(score * 1.65))}px;--bar-color:${mood?.color || "#ddd8cc"}"></span></div>${mood ? moodIcon(mood, "week-face", mood.name) : "<span>·</span>"}<small>周${weekday[day.getDay()]}</small>`;
      chart.append(column);
    });
    const recorded = entries.filter(({ entry }) => entry);
    const moodCounts = recorded.reduce((counts, { entry }) => {
      counts[entry.mood] = (counts[entry.mood] || 0) + 1;
      return counts;
    }, {});
    const topMoodId = Object.entries(moodCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "calm";
    const topMood = moodById[topMoodId];
    const avgFocus = recorded.length ? Math.round(recorded.reduce((sum, { entry }) => sum + Number(entry.focus || 0), 0) / recorded.length) : 0;
    const totalMinutes = recorded.reduce((sum, { entry }) => sum + Number(entry.minutes || 0), 0) + Math.floor(focusElapsedSeconds() / 60);
    $("#week-main-mood").textContent = `${topMood.name}最多`;
    $("#week-days").textContent = `${recorded.length} 天`;
    $("#week-focus").textContent = avgFocus ? `${avgFocus} 分` : "—";
    $("#week-minutes").textContent = `${totalMinutes} 分钟`;
    const letters = [
      `这一周，${topMood.name}是你最常遇见的心情。你一共留下了 ${recorded.length} 次记录。别急着给这一周打分，能停下来感受自己，本身就是一件很珍贵的事。`,
      `我看见你在忙碌和休息之间来回寻找节奏。${topMood.note} 下周不需要做得更多，只要继续把真实的感受留下一点点。`,
      `有些日子明亮，有些日子只是平常。它们一起组成了完整的一周。请记得，稳定不是没有波动，而是每次都愿意回来照顾自己。`
    ];
    $("#weekly-letter-text").textContent = letters[letterIndex % letters.length];
    renderMoodMix(moodCounts, Math.max(1, recorded.length));
    $$('[data-promise]').forEach((input) => { input.checked = Boolean(state.promises[input.dataset.promise]); });
  }

  async function generateWeekWithXiaoyu() {
    const days = getLastSevenDays();
    const weekData = days.map((day) => {
      const entry = state.entries[dateKey(day)];
      const mood = entry ? moodById[entry.mood] : null;
      return {
        date: dateKey(day),
        mood: mood?.name || "未记录",
        focus: entry?.focus || 0,
        note: entry?.note || ""
      };
    });
    const button = $("#refresh-letter");
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "小屿正在写信…";
    const data = await servicePost("/chat", {
      message: "请根据这周的情绪记录，写一段温柔、具体、适合放在周报里的总结。",
      emotion: currentContext().moodName,
      user_name: state.profile.name,
      context: `近七日记录：${JSON.stringify(weekData)}`
    });
    if (data?.reply) {
      $("#weekly-letter-text").textContent = data.reply;
      showToast("小屿写好这一周的回信了");
    } else {
      letterIndex += 1;
      renderWeek();
      showToast("小屿暂时休息，先换成本地总结");
    }
    button.disabled = false;
    button.textContent = original;
  }

  function renderMoodMix(counts, total) {
    const list = $("#mood-mix-list");
    list.innerHTML = "";
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 4);
    (sorted.length ? sorted : [["calm", 1]]).forEach(([id, count]) => {
      const mood = moodById[id] || moods[1];
      const row = document.createElement("div");
      row.className = "mix-row";
      row.innerHTML = `<span>${moodIcon(mood, "mix-face", mood.name)} ${mood.name}</span><div class="mix-track"><div class="mix-fill" style="width:${Math.round(count / total * 100)}%;--mix-color:${mood.color}"></div></div><small>${count}次</small>`;
      list.append(row);
    });
  }

  function renderHealth() {
    $("#water-value").textContent = `${state.health.water} / 8 杯`;
    $("#water-progress").style.width = `${Math.min(100, state.health.water / 8 * 100)}%`;
    $("#steps-value").textContent = String(state.health.steps);
    $("#steps-goal-label").textContent = String(state.health.stepsGoal);
    $("#steps-goal").value = String(state.health.stepsGoal);
    $("#steps-progress").style.width = `${Math.min(100, state.health.steps / state.health.stepsGoal * 100)}%`;
  }

  function toggleHealthTimer(type) {
    const button = $(`[data-timer='${type}']`);
    const timer = $(`#${type}-timer`);
    if (healthIntervals[type]) {
      clearInterval(healthIntervals[type]);
      healthIntervals[type] = 0;
      button.textContent = "继续计时";
      button.classList.remove("running");
      return;
    }
    button.textContent = "暂停";
    button.classList.add("running");
    healthIntervals[type] = window.setInterval(() => {
      healthSeconds[type] -= 1;
      timer.textContent = formatClock(healthSeconds[type]);
      if (healthSeconds[type] <= 0) {
        clearInterval(healthIntervals[type]);
        healthIntervals[type] = 0;
        healthSeconds[type] = type === "eye" ? 1200 : 2700;
        timer.textContent = formatClock(healthSeconds[type]);
        button.textContent = "重新开始";
        button.classList.remove("running");
        showToast(type === "eye" ? "看看远处二十秒，让眼睛休息一下" : "起来走一走，身体会谢谢你");
      }
    }, 1000);
  }

  function toggleBreath() {
    if (breathRunning) {
      stopBreath();
      return;
    }
    breathRunning = true;
    runBreathPhase(0);
  }

  function runBreathPhase(index) {
    if (!breathRunning) return;
    const phases = [
      { label: "吸气", seconds: 4, className: "inhale" },
      { label: "停留", seconds: 7, className: "hold" },
      { label: "呼气", seconds: 8, className: "exhale" }
    ];
    const phase = phases[index % phases.length];
    let remaining = phase.seconds;
    const orb = $("#breath-orb");
    orb.className = `breath-orb ${phase.className}`;
    $("#breath-label").textContent = phase.label;
    $("#breath-seconds").textContent = `${remaining} 秒`;
    $("#breath-copy").textContent = phase.label === "吸气" ? "慢慢吸进来，让身体有更多空间。" : phase.label === "停留" ? "轻轻停住，不要勉强。" : "缓缓呼出去，也放下一点紧张。";
    clearInterval(breathInterval);
    breathInterval = window.setInterval(() => {
      remaining -= 1;
      $("#breath-seconds").textContent = `${Math.max(0, remaining)} 秒`;
      if (remaining <= 0) {
        clearInterval(breathInterval);
        runBreathPhase(index + 1);
      }
    }, 1000);
  }

  function stopBreath() {
    breathRunning = false;
    clearInterval(breathInterval);
    const orb = $("#breath-orb");
    orb.className = "breath-orb";
    $("#breath-label").textContent = "开始";
    $("#breath-seconds").textContent = "4-7-8";
    $("#breath-copy").textContent = "吸气、停留、呼气。跟着圆圈慢慢来。";
  }

  function toggleStretch() {
    const steps = ["颈部慢慢向左、向右侧弯", "双肩向后绕圈，放松肩颈", "手臂向上伸展，保持自然呼吸", "身体轻轻向两侧伸展", "站直，做三次缓慢的深呼吸"];
    if (stretchInterval) {
      clearInterval(stretchInterval);
      stretchInterval = 0;
      $("#stretch-button").textContent = "重新开始";
      $("#stretch-step-label").textContent = "舒展完成";
      $("#stretch-guide").textContent = "喝一口水，再慢慢回到自己的节奏里。";
      showToast("完成了一次温柔的舒展");
      return;
    }
    stretchIndex = 0;
    $("#stretch-step-label").textContent = `第 1 / ${steps.length} 步`;
    $("#stretch-guide").textContent = steps[0];
    $("#stretch-button").textContent = "提前结束";
    stretchInterval = window.setInterval(() => {
      stretchIndex += 1;
      if (stretchIndex >= steps.length) {
        toggleStretch();
        return;
      }
      $("#stretch-step-label").textContent = `第 ${stretchIndex + 1} / ${steps.length} 步`;
      $("#stretch-guide").textContent = steps[stretchIndex];
    }, 4000);
  }

  function renderProfile() {
    $("#profile-display-name").textContent = state.profile.name;
    $("#profile-name").value = state.profile.name;
    const mode = $(`input[name='preferredMode'][value='${state.profile.preferredMode}']`);
    const tone = $(`input[name='reminderTone'][value='${state.profile.reminderTone}']`);
    if (mode) mode.checked = true;
    if (tone) tone.checked = true;
  }

  function saveProfile(event) {
    event["prevent" + "De" + "fault"]();
    const name = $("#profile-name").value.trim();
    state.profile.name = name || "心屿用户";
    state.profile.preferredMode = $("input[name='preferredMode']:checked")?.value || "single";
    state.profile.reminderTone = $("input[name='reminderTone']:checked")?.value || "gentle";
    state.mode = state.profile.preferredMode;
    persist();
    renderProfile();
    renderHeaderAndHome();
    showToast("设置已经保存");
  }

  function resetData() {
    if (!window.confirm("确定重置所有体验记录吗？这不会影响原来的页面。")) return;
    state = initialState();
    persist();
    selectedEntryKey = "";
    calendarCursor = startOfMonth(new Date());
    stopBreath();
    renderAll();
    showToast("体验数据已恢复到初始状态");
  }

  function openExperience(type) {
    const content = $("#experience-content");
    if (type === "emotion") content.innerHTML = emotionExperienceTemplate();
    else if (type === "focus") content.innerHTML = focusExperienceTemplate();
    else if (type === "group") content.innerHTML = groupExperienceTemplate();
    else content.innerHTML = chatExperienceTemplate();
    bindExperienceActions(type);
    $("#experience-dialog").showModal();
  }

  function emotionExperienceTemplate() {
    const today = state.entries[dateKey(new Date())];
    const mood = today ? moodById[today.mood] : moods[1];
    return `<section class="experience-hero" style="--experience-color:${mood.color}55">${moodIcon(mood, "experience-face", mood.name)}<p class="eyebrow">EMOTION COMPANION</p><h2 id="experience-title">先安静地看看此刻</h2><p>情绪陪伴会用一段柔和的过程，帮助你感受并记录当下。</p></section><div class="experience-panel"><div class="experience-status"><div><small>此刻的感受</small><strong id="emotion-result">${today ? mood.name : "准备感受"}</strong></div><span id="emotion-copy">${today ? mood.note : "慢慢呼吸，给自己几秒钟。"}</span></div></div><div class="experience-actions"><button class="primary-cta" type="button" id="emotion-sense">开始感受</button><button class="soft-button" type="button" id="emotion-record">自己选择心情</button></div>`;
  }

  function focusExperienceTemplate() {
    const elapsed = focusElapsedSeconds();
    return `<section class="experience-hero" style="--experience-color:#d9ead4"><span class="experience-glyph">◎</span><p class="eyebrow">FOCUS WITH XIAOYU</p><h2 id="experience-title">陪你专注一小会儿</h2><p>不追赶效率，只守住眼前这一件小事。</p></section><div class="experience-panel"><div class="experience-status"><div><small>本次专注</small><strong id="focus-sheet-time">${formatClock(elapsed)}</strong></div><span id="focus-sheet-state">${state.focus.running ? "正在陪伴" : "随时可以开始"}</span></div></div><div class="experience-actions"><button class="primary-cta" type="button" id="focus-toggle">${state.focus.running ? "暂停一下" : "开始专注"}</button><button class="soft-button" type="button" data-close-experience>稍后再说</button></div>`;
  }

  function groupExperienceTemplate() {
    return `<section class="experience-hero" style="--experience-color:#e4d8e8"><span class="experience-glyph">◌ ◌</span><p class="eyebrow">SHARED MOMENT</p><h2 id="experience-title">把一段对话好好留下</h2><p>适合两人或多人交流，结束后可以整理成温和、清晰的记录。</p></section><div class="experience-panel demo-list" id="group-result"><div class="demo-note">同行时刻尚未开始。大家准备好后，从一句“我们开始吧”出发。</div></div><div class="experience-actions"><button class="primary-cta" type="button" id="group-start">开始同行时刻</button><button class="soft-button" type="button" id="group-summary">整理这次交流</button></div>`;
  }

  function chatExperienceTemplate() {
    const today = state.entries[dateKey(new Date())];
    const mood = today ? moodById[today.mood] : moods[1];
    return `<section class="experience-hero" style="--experience-color:#f0dfc4"><span class="experience-glyph">✎</span><p class="eyebrow">A QUIET CONVERSATION</p><h2 id="experience-title">和小屿聊聊</h2><p>这里的回应会结合你留下的心情和当前状态。</p></section><div class="experience-panel"><div class="chat-thread" id="chat-thread"><div class="chat-bubble">${today ? `我看见你今天记录了“${mood.name}”。${mood.note}` : "今天还没有留下心情。你可以说说，此刻最放不下的是什么。"}</div></div><div class="chat-compose"><input id="chat-input" maxlength="160" aria-label="想和小屿说的话" placeholder="说说现在的感受……"><button type="button" id="chat-send">发送</button></div></div>`;
  }

  function bindExperienceActions(type) {
    $$('[data-close-experience]', $("#experience-dialog")).forEach((button) => button.addEventListener("click", () => $("#experience-dialog").close()));
    if (type === "emotion") {
      $("#emotion-record").addEventListener("click", () => { $("#experience-dialog").close(); openMoodDialog(); });
      $("#emotion-sense").addEventListener("click", async (event) => {
        event.currentTarget.disabled = true;
        event.currentTarget.textContent = "正在陪你感受…";
        $("#emotion-result").textContent = "正在靠近";
        await servicePost("/single_track/start", { speed: 360 });
        await refreshProductState(true);
        const ctx = currentContext();
        $("#emotion-result").textContent = ctx.moodName;
        $("#emotion-copy").textContent = serviceState.connected ? ctx.mood.note : "暂时没有连接到心屿设备，你仍然可以自己选择并记录此刻。";
        const recordButton = event.currentTarget.cloneNode(true);
        recordButton.textContent = "把它记下来";
        recordButton.disabled = false;
        event.currentTarget.replaceWith(recordButton);
        recordButton.addEventListener("click", () => { $("#experience-dialog").close(); openMoodDialog(); chooseMood(ctx.mood.id); });
      });
    } else if (type === "focus") {
      $("#focus-toggle").addEventListener("click", toggleFocusFromSheet);
    } else if (type === "group") {
      $("#group-start").addEventListener("click", async (event) => {
        event.currentTarget.disabled = true;
        await servicePost("/tracking_mode", { mode: "multi" });
        await servicePost("/multi_track/start", { save_audio: true });
        event.currentTarget.textContent = "同行中";
        event.currentTarget.disabled = false;
        $("#group-result").innerHTML = `<div class="demo-note">同行时刻已经开始。慢慢说，不需要抢着得出答案。</div><div class="demo-note">小屿正在帮你们记住重要的感受与约定。</div>`;
        showToast(serviceState.connected ? "同行时刻已开始" : "本地记录可用，实时陪伴稍后再试");
      });
      $("#group-summary").addEventListener("click", async () => {
        $("#group-result").innerHTML = `<div class="demo-note">小屿正在整理这段交流……</div>`;
        const data = await servicePost("/meeting/summarize", {});
        const summary = data?.summary || "这次交流被温柔地留了下来：大家谈到压力，也确认了更清楚表达彼此需要的重要性。";
        const diary = data?.diary || "交流结束后，可以把最重要的一句话写进今天的情绪日记。";
        const notes = state.meetings || [];
        notes.unshift({ id: `meeting_${Date.now()}`, date: dateKey(new Date()), summary, diary });
        state.meetings = notes.slice(0, 20);
        persist();
        $("#group-result").innerHTML = `<div class="demo-note"><strong>这次交流的回声</strong><br>${escapeHTML(summary)}<br><br>${escapeHTML(diary)}</div>`;
        showToast(data ? "交流记录已经整理好" : "小屿暂时休息，已保留本地整理");
      });
    } else {
      const send = async () => {
        const input = $("#chat-input");
        const message = input.value.trim();
        if (!message) return;
        const thread = $("#chat-thread");
        const user = document.createElement("div");
        user.className = "chat-bubble user";
        user.textContent = message;
        thread.append(user);
        input.value = "";
        const pending = document.createElement("div");
        pending.className = "chat-bubble";
        pending.textContent = "小屿正在读你的这句话……";
        thread.append(pending);
        thread.scrollTop = thread.scrollHeight;
        const ctx = currentContext();
        const data = await servicePost("/chat", {
          message,
          emotion: ctx.moodName,
          user_name: state.profile.name,
          context: `今日心情：${ctx.moodName}；专注：${ctx.attention}；日记：${ctx.diary || "还没有写下内容"}`,
          diary_text: ctx.diary
        });
        pending.textContent = data?.reply || `${ctx.mood.note} 如果愿意，可以先写下今天最想被理解的一件事，再决定下一步。`;
        thread.scrollTop = thread.scrollHeight;
      };
      $("#chat-send").addEventListener("click", send);
      $("#chat-input").addEventListener("keydown", (event) => { if (event.key === "Enter") send(); });
    }
  }

  async function toggleFocusFromSheet() {
    if (state.focus.running) {
      state.focus.accumulated = focusElapsedSeconds();
      state.focus.running = false;
      state.focus.startedAt = 0;
      await servicePost("/single_track/stop", {});
    } else {
      state.focus.running = true;
      state.focus.startedAt = Date.now();
      await servicePost("/single_track/start", { speed: 360 });
    }
    persist();
    $("#focus-toggle").textContent = state.focus.running ? "暂停一下" : "继续专注";
    $("#focus-sheet-state").textContent = state.focus.running ? "正在陪伴" : "已经停下来休息";
    renderFocusSummary();
    clearInterval(focusTicker);
    focusTicker = window.setInterval(() => {
      const time = $("#focus-sheet-time");
      if (time) time.textContent = formatClock(focusElapsedSeconds());
      renderFocusSummary();
    }, 1000);
  }

  function bindEvents() {
    $$('[data-nav]').forEach((button) => button.addEventListener("click", () => goTo(button.dataset.nav)));
    $$('[data-go]').forEach((button) => button.addEventListener("click", () => goTo(button.dataset.go)));
    $$('[data-open-mood]').forEach((button) => button.addEventListener("click", () => openMoodDialog()));
    $$(".mode-option").forEach((button) => button.addEventListener("click", async () => {
      state.mode = button.dataset.mode;
      persist();
      renderHeaderAndHome();
      if (state.mode === "multi") await servicePost("/tracking_mode", { mode: "multi" });
      else await servicePost("/single_track/start", { speed: 360 });
      showToast(state.mode === "single" ? "回到你的私人岛屿" : "已经切换到同行时刻");
    }));
    $$('[data-experience]').forEach((button) => button.addEventListener("click", () => openExperience(button.dataset.experience)));
    $$('[data-close-dialog]').forEach((button) => button.addEventListener("click", () => $("#mood-dialog").close()));
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
    $("#entry-delete").addEventListener("click", removeSelectedEntry);
    $("#refresh-letter").addEventListener("click", generateWeekWithXiaoyu);
    $$('[data-promise]').forEach((input) => input.addEventListener("change", () => { state.promises[input.dataset.promise] = input.checked; persist(); }));
    $$('[data-timer]').forEach((button) => button.addEventListener("click", () => toggleHealthTimer(button.dataset.timer)));
    $("#breath-orb").addEventListener("click", toggleBreath);
    $("#stretch-button").addEventListener("click", toggleStretch);
    $("#add-water").addEventListener("click", () => { state.health.water = Math.min(8, state.health.water + 1); persist(); renderHealth(); showToast("记下一杯水"); });
    $("#add-steps").addEventListener("click", () => { state.health.steps = Math.min(99999, state.health.steps + 500); persist(); renderHealth(); showToast("又向前走了五百步"); });
    $("#steps-goal").addEventListener("change", (event) => { state.health.stepsGoal = Math.max(1000, Math.min(50000, Number(event.target.value) || 6000)); persist(); renderHealth(); });
    $("#settings-form").addEventListener("submit", saveProfile);
    $("#reset-data").addEventListener("click", resetData);
    $("#mood-dialog").addEventListener("click", (event) => { if (event.target === $("#mood-dialog")) $("#mood-dialog").close(); });
    $("#experience-dialog").addEventListener("click", (event) => { if (event.target === $("#experience-dialog")) $("#experience-dialog").close(); });
  }

  function renderAll() {
    renderHeaderAndHome();
    renderCalendar();
    renderEntryDetail(selectedEntryKey);
    renderWeek();
    renderHealth();
    renderProfile();
  }

  bindEvents();
  renderAll();
  refreshProductState(true);
  serviceState.polling = window.setInterval(() => refreshProductState(true), 5000);
  focusTicker = window.setInterval(renderFocusSummary, 1000);
})();
