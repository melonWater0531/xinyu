(() => {
  "use strict";

  const STORAGE_KEY = "xinyu.preview.v1";
  const DIARY_KEY = "xinyu.actual.diary.v1";
  const seedData = window.XINYU_PREVIEW_DATA || {};
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  let toastTimer = 0;
  let meetingMarkdownText = seedData.meetings?.currentMeeting?.minutesMarkdown || "";

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

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      activePage: state.activePage,
      selectedDate: state.selectedDate,
      calendarMonth: state.calendarMonth,
    }));
  }

  function persistDiary() {
    localStorage.setItem(DIARY_KEY, JSON.stringify(diaryOverrides));
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
      return normalizeLLMReply(body.reply, fallback);
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

  async function loadMeetingMarkdown() {
    const path = seedData.meetings?.currentMeeting?.minutesMarkdownPath;
    if (!path || location.protocol === "file:") return;
    try {
      const response = await fetch(`/static/page2_preview/${path}`, {cache: "no-store"});
      if (response.ok) meetingMarkdownText = await response.text();
    } catch (_error) {}
  }

  function renderMeeting() {
    const meeting = seedData.meetings?.currentMeeting || {};
    $("#xy-current-meeting-title").textContent = meeting.title || "会议纪要";
    $("#xy-meeting-status").textContent = meeting.status || "已整理";
    $("#xy-meeting-summary").textContent = meeting.summary || "";
    $("#xy-meeting-date").textContent = `${meeting.date || ""} ${meeting.time || ""}`.trim();
    $("#xy-meeting-time").textContent = meeting.duration || "";
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

  function saveDiary(event) {
    event.preventDefault();
    const day = currentDay();
    const text = $("#xy-diary-editor").value.trim() || day.diary || "";
    const assistantReply = buildDiaryAssistantReply(day, text);
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
    $$("[data-meeting-detail]").forEach((button) => button.addEventListener("click", openMeetingDetail));
    $("#xy-calendar-prev")?.addEventListener("click", () => shiftMonth(-1));
    $("#xy-calendar-next")?.addEventListener("click", () => shiftMonth(1));
    $("#xy-open-diary")?.addEventListener("click", () => openDiaryModal());
    $("#xy-diary-link")?.addEventListener("click", () => openDiaryModal());
    $("#xy-weekly-link")?.addEventListener("click", () => openWeeklyReportModal());
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
    bind();
    await loadMeetingMarkdown();
    goTo(["home", "companion", "meeting", "records", "mine"].includes(state.activePage) ? state.activePage : "home");
  }

  init();
})();
