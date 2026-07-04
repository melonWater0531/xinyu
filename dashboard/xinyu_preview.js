(() => {
  "use strict";

  const STORAGE_KEY = "xinyu.preview.v1";
  const DEMO = Object.freeze({
    emotion: {label: "有点疲惫", assistantText: "小屿留意到你最近压力有点高，可以先慢下来。"},
    trend: [
      {time: "00:00", label: "平静", level: 1},
      {time: "09:20", label: "有点累", level: 2},
      {time: "13:10", label: "压力高", level: 3},
      {time: "18:30", label: "放松", level: 0},
      {time: "22:00", label: "平稳", level: 1},
    ],
    today: {water: "5 / 8 杯", steps: "3200 步", meditation: "0 / 1 次"},
    advice: "今天水喝得有点少，先补一杯吧。出门走一走也能帮你缓解压力。",
  });

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  let toastTimer = 0;
  let meetingTimer = 0;
  let meetingSeconds = 0;
  const chatHistory = [];

  function loadState() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      return value && typeof value === "object" ? value : {activePage: "home", selectedDay: 4};
    } catch (_error) {
      return {activePage: "home", selectedDay: 4};
    }
  }

  const state = loadState();
  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({activePage: state.activePage, selectedDay: state.selectedDay}));
  }

  function iconUse(id) {
    return `<svg aria-hidden="true"><use href="#${id}"/></svg>`;
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
    $$(".xy-page").forEach(section => {
      const active = section === target;
      section.hidden = !active;
      section.classList.toggle("is-active", active);
    });
    $$(".xy-bottom-nav [data-go]").forEach(button => {
      const active = button.dataset.go === page;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    state.activePage = page;
    persist();
    window.scrollTo({top: 0, behavior: "smooth"});
  }

  function renderTrend(container) {
    if (!container) return;
    const width = 350;
    const left = 18;
    const right = 12;
    const top = 32;
    const bottom = 144;
    const levels = [bottom - 22, bottom - 52, bottom - 78, top];
    const points = DEMO.trend.map((point, index) => ({
      ...point,
      x: left + index * ((width - left - right) / (DEMO.trend.length - 1)),
      y: levels[point.level],
    }));
    const lines = points.slice(0, -1).map((point, index) => {
      const next = points[index + 1];
      const colors = ["#8FA9D8", "#D99B7A", "#C58C70", "#A8CFA0"];
      return `<path d="M${point.x} ${point.y} C${point.x + 28} ${point.y - 8},${next.x - 28} ${next.y + 8},${next.x} ${next.y}" stroke="${colors[index]}" stroke-width="3" fill="none"/>`;
    }).join("");
    const nodes = points.map((point, index) => `<g><circle cx="${point.x}" cy="${point.y}" r="5" fill="#FFFDF8" stroke="${["#8FA9D8", "#D99B7A", "#C58C70", "#A8CFA0", "#8FA9D8"][index]}" stroke-width="3"/><text x="${point.x}" y="${point.y - 12}" text-anchor="middle" fill="#766A5F" font-size="9">${point.label}</text><text x="${point.x}" y="169" text-anchor="middle" fill="#A29588" font-size="9">${point.time}</text></g>`).join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} 180" role="img" aria-labelledby="xy-trend-title-${container.dataset.trend} xy-trend-desc-${container.dataset.trend}"><title id="xy-trend-title-${container.dataset.trend}">今日情绪趋势</title><desc id="xy-trend-desc-${container.dataset.trend}">演示数据：平静、有点累、压力高、放松、平稳五个明显转折点</desc><g stroke="#E7DCCF" stroke-width="1" stroke-dasharray="3 6"><line x1="${left}" y1="42" x2="${width-right}" y2="42"/><line x1="${left}" y1="86" x2="${width-right}" y2="86"/><line x1="${left}" y1="130" x2="${width-right}" y2="130"/></g>${lines}${nodes}</svg>`;
  }

  function renderCalendar() {
    const grid = $("#xy-calendar-days");
    if (!grid) return;
    grid.innerHTML = "";
    for (let blank = 0; blank < 3; blank += 1) grid.append(document.createElement("span"));
    for (let day = 1; day <= 31; day += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(day);
      button.setAttribute("aria-label", `7月${day}日`);
      button.classList.toggle("is-selected", day === Number(state.selectedDay || 4));
      button.addEventListener("click", () => {
        state.selectedDay = day;
        persist();
        renderCalendar();
        showToast(`已查看 7 月 ${day} 日的演示记录`);
      });
      grid.append(button);
    }
  }

  function companionReply(message) {
    if (/难受|不舒服|低落|伤心|想哭|委屈/.test(message)) return "听起来你现在不太舒服。先不用急着解释原因，可以慢慢说，也可以只是让我陪你待一会儿。是身体上更难受，还是心里更闷一点？";
    if (/压力|累|疲惫|没精神|困/.test(message)) return "听见你说累了，今天或许已经消耗了不少力气。可以先把肩膀放松一下，喝口水；如果愿意，也可以告诉我最让你费劲的是哪一段。";
    if (/焦虑|紧张|担心|不安|害怕/.test(message)) return "这份不安值得被认真听见，不必马上把它压下去。我们可以先从最具体的一件担心说起，也可以一起做三次慢一点的呼吸。";
    if (/放松|建议/.test(message)) return "可以先把肩膀放松，慢慢呼吸三次，再喝一杯水。今天不需要一下子做好很多，只完成这一件小事就很好。";
    if (/记录|情绪/.test(message)) return "我会先按你亲口说出的感受来记录，不让任何视觉线索替你下结论。你想从今天最明显的那个瞬间开始吗？";
    return "我在听。你不需要配合任何情绪判断，按自己的感受慢慢说就好；如果现在还不想展开，也可以只告诉我，希望被陪着还是想一起理一理。";
  }

  function appendChat(message, role) {
    const thread = $("#xy-chat");
    const bubble = document.createElement("article");
    bubble.className = `xy-bubble ${role === "user" ? "xy-bubble-user" : "xy-bubble-assistant"}`;
    if (role === "assistant") bubble.innerHTML = `<span class="xy-icon-badge xy-amber">${iconUse("xy-message")}</span><div><strong>小屿</strong><p></p></div>`;
    const text = role === "assistant" ? $("p", bubble) : bubble;
    text.textContent = message;
    thread.append(bubble);
    bubble.scrollIntoView({behavior: "smooth", block: "nearest"});
    return text;
  }

  function selfReportLabel(message) {
    if (/难受|不舒服|低落|伤心|想哭|委屈/.test(message)) return "低落";
    if (/累|疲惫|没精神|困|压力/.test(message)) return "疲惫";
    if (/焦虑|紧张|担心|不安|害怕/.test(message)) return "不安";
    if (/生气|烦|恼火|愤怒/.test(message)) return "烦闷";
    if (/开心|高兴|顺利|轻松/.test(message)) return "开心";
    return "";
  }

  function buildLLMPayload(message) {
    const selfReport = selfReportLabel(message);
    const recent = chatHistory.slice(-6).map(item => `${item.role === "user" ? "用户" : "小屿"}：${item.content}`).join("；");
    return {
      message,
      emotion: selfReport,
      diary_text: "",
      user_name: "Lintong",
      context: [
        "用户本轮自述是最高优先级，近期对话次之，页面中的‘有点疲惫’仅是产品演示弱线索",
        "若用户文字与演示情绪冲突，只跟随用户文字，不提及冲突、模型、概率或识别过程",
        "先回应用户明确说出的感受，再给一个轻而具体的陪伴选择或开放问题",
        "回复保持40至90个中文字符，自然克制，不诊断、不说教、不使用标题或列表",
        recent ? `近期对话：${recent.slice(0, 240)}` : "",
      ].filter(Boolean).join("；").slice(0, 500),
    };
  }

  function normalizeReply(reply, fallback) {
    const value = String(reply || "").replace(/^#+\s*/g, "").replace(/\s*\n+\s*/g, "").trim();
    if (!value) return fallback;
    if (value.length <= 120) return value;
    const windowText = value.slice(0, 120);
    const boundary = Math.max(windowText.lastIndexOf("。"), windowText.lastIndexOf("？"), windowText.lastIndexOf("！"));
    return boundary >= 40 ? windowText.slice(0, boundary + 1) : `${windowText.slice(0, 89)}。`;
  }

  async function requestLLM(message) {
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
      return normalizeReply(body.reply, companionReply(message));
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function sendChat(message) {
    const value = String(message || "").trim();
    if (!value) return;
    appendChat(value, "user");
    chatHistory.push({role: "user", content: value});
    const pending = appendChat("小屿正在读你的这句话……", "assistant");
    try {
      const reply = await requestLLM(value);
      pending.textContent = reply;
      chatHistory.push({role: "assistant", content: reply});
    } catch (_error) {
      const fallback = companionReply(value);
      pending.textContent = fallback;
      chatHistory.push({role: "assistant", content: fallback});
      showToast("暂时未连上小屿，已使用本地回应");
    }
  }

  function buildWave() {
    const wave = $("#xy-wave");
    const heights = [18, 32, 24, 46, 30, 56, 38, 64, 34, 48, 26, 58, 31, 44, 21, 36, 18, 28, 16, 24];
    wave.innerHTML = heights.map((height, index) => `<i style="--h:${height}px;--d:${(index % 7) * -.11}s"></i>`).join("");
  }

  function formatTime(seconds) {
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function toggleMeeting() {
    const button = $("#xy-meeting-toggle");
    const status = $("#xy-meeting-status");
    const wave = $("#xy-wave");
    if (meetingTimer) {
      clearInterval(meetingTimer);
      meetingTimer = 0;
      button.textContent = "开始记录";
      status.textContent = "已保存演示";
      wave.classList.remove("is-running");
      showToast("演示会议已整理，不包含真实录音");
      return;
    }
    meetingSeconds = 0;
    $("#xy-meeting-time").textContent = "00:00";
    button.textContent = "结束记录";
    status.textContent = "演示进行中";
    wave.classList.add("is-running");
    meetingTimer = window.setInterval(() => {
      meetingSeconds += 1;
      $("#xy-meeting-time").textContent = formatTime(meetingSeconds);
    }, 1000);
  }

  function bind() {
    $$('[data-go]').forEach(button => button.addEventListener("click", () => goTo(button.dataset.go)));
    $$("[data-toast]").forEach(button => button.addEventListener("click", () => showToast(button.dataset.toast)));
    $$(".xy-prompt-chips button").forEach(button => button.addEventListener("click", () => sendChat(button.textContent)));
    $("#xy-chat-form")?.addEventListener("submit", event => {
      event.preventDefault();
      const input = $("#xy-chat-input");
      sendChat(input.value);
      input.value = "";
    });
    $("#xy-meeting-toggle")?.addEventListener("click", toggleMeeting);
  }

  function init() {
    $$("[data-trend]").forEach(renderTrend);
    renderCalendar();
    buildWave();
    bind();
    goTo(["home", "companion", "meeting", "records", "mine"].includes(state.activePage) ? state.activePage : "home");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once: true});
  else init();
})();
