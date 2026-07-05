(function (global) {
  "use strict";

  const api = global.XinyuSelfCare;
  if (!api) return;

  const repository = new api.SelfCareRepository();
  const $ = id => document.getElementById(id);
  const sourceLabel = source => ({
    demo: "演示数据", local: "本机记录", native: "手机数据",
    health_connect: "Health Connect", step_counter: "手机计步",
  }[source] || "演示数据");

  function latestExercise(week) {
    return week.flatMap(day => day.exercise?.sessions || [])
      .sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")))[0] || null;
  }

  function latestSelfReport() {
    try {
      const today = new Date().toISOString().slice(0, 10);
      const entries = JSON.parse(localStorage.getItem("xinyu_diary_entries") || "[]");
      const entry = entries.find(item => item.date === today);
      return entry?.content || "";
    } catch (_error) {
      return "";
    }
  }

  function render() {
    const today = repository.getToday();
    const week = repository.getWeek();
    const steps = Number(today.steps?.value || 0);
    const water = Number(today.water?.cups || 0);
    const waterGoal = Number(today.water?.goal || 8);
    const meditationSessions = today.meditation?.sessions || [];
    const meditationMinutes = meditationSessions.reduce((sum, item) => sum + Number(item.minutes || 0), 0);
    const lastExercise = latestExercise(week);
    const average = Math.round(week.reduce((sum, day) => sum + Number(day.steps?.value || 0), 0) / Math.max(1, week.length));

    $("selfcare-steps").textContent = steps.toLocaleString("zh-CN");
    $("selfcare-steps-average").textContent = `本周平均 ${average.toLocaleString("zh-CN")} 步`;
    $("selfcare-steps-source").textContent = sourceLabel(today.steps?.source);
    $("selfcare-water").textContent = `${water} / ${waterGoal}`;
    $("selfcare-water-source").textContent = sourceLabel(today.water?.source);
    $("selfcare-exercise").textContent = lastExercise ? lastExercise.label || "散步" : "还没有记录";
    $("selfcare-exercise-note").textContent = lastExercise ? `${lastExercise.minutes || 20} 分钟 · ${sourceLabel(lastExercise.source)}` : "走一小段也算数";
    $("selfcare-meditation").textContent = meditationMinutes ? `${meditationMinutes} 分钟` : "0 / 1 次";
    $("selfcare-meditation-note").textContent = meditationMinutes ? "今天已留下一段安静" : "给自己 5 分钟安静一下";

    const suggestion = api.buildProactiveCareSuggestion(today, {selfReport: latestSelfReport()}, {}, {
      multiScene: Boolean(global.XinyuHomeScene?.isMulti?.()),
    });
    const careText = $("comp-care-text");
    if (careText) careText.textContent = suggestion;
    $("comp-care-inline")?.classList.add("visible");
  }

  function announce(message) {
    const status = $("selfcare-status");
    status.textContent = message;
    global.setTimeout(() => { if (status.textContent === message) status.textContent = ""; }, 2200);
  }

  function openMeditationDialog() {
    const dialog = $("meditation-dialog");
    if (dialog?.showModal) dialog.showModal();
    else dialog?.setAttribute("open", "");
  }

  function closeMeditationDialog() {
    const dialog = $("meditation-dialog");
    if (dialog?.close) dialog.close();
    else dialog?.removeAttribute("open");
  }

  function bind() {
    $("selfcare-water-add")?.addEventListener("click", () => {
      repository.incrementWater(); render(); announce("已记下一杯水");
    });
    $("selfcare-walk-add")?.addEventListener("click", () => {
      repository.recordWalk(); render(); announce("已记录一次散步");
    });
    $("selfcare-meditation-open")?.addEventListener("click", openMeditationDialog);
    $("meditation-later")?.addEventListener("click", closeMeditationDialog);
    $("meditation-close")?.addEventListener("click", closeMeditationDialog);
    $("meditation-start")?.addEventListener("click", () => {
      const minutes = Number(document.querySelector('input[name="meditation-duration"]:checked')?.value || 5);
      const music = document.querySelector('input[name="meditation-music"]:checked')?.value || "none";
      repository.recordMeditation(new Date(), minutes, music);
      closeMeditationDialog(); render(); announce(`已记下 ${minutes} 分钟冥想`);
    });
    $("meditation-dialog")?.addEventListener("click", event => {
      if (event.target === event.currentTarget) closeMeditationDialog();
    });
  }

  function init() {
    if (!$("selfcare-steps")) return;
    bind();
    render();
    global.addEventListener("storage", event => { if (event.key === api.KEYS.data) render(); });
    global.XinyuSelfCareHome = {repository, render, openMeditationDialog};
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once: true});
  else init();
})(window);
