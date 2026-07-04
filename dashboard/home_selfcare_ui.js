(function (global) {
  "use strict";

  const api = global.XinyuSelfCare;
  if (!api) return;

  const repository = new api.SelfCareRepository();
  const $ = id => document.getElementById(id);
  const sourceLabel = source => ({
    demo: "演示数据",
    local: "本机记录",
    native: "手机数据",
    health_connect: "Health Connect",
    step_counter: "手机计步",
  }[source] || "演示数据");
  const moodLabel = emotion => ({
    Happiness: "心情明亮", Neutral: "心情平静", Sadness: "有些低落",
    Anger: "有些烦闷", Fear: "有些不安", Surprise: "有点意外",
    Disgust: "有些不适", Contempt: "有些疏离",
  }[emotion] || "等你记录");

  function todayMood() {
    try {
      const entries = JSON.parse(localStorage.getItem("xinyu_diary_entries") || "[]");
      const today = new Date();
      const key = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
      const entry = entries.find(item => item.date === key);
      return entry ? {label: moodLabel(entry.emotion), note: "来自你今天的记录"} : {label: "等你记录", note: "你的感受，由你来定义"};
    } catch (_error) {
      return {label: "等你记录", note: "你的感受，由你来定义"};
    }
  }

  function latestExercise(week) {
    return week.flatMap(day => day.exercise?.sessions || [])
      .sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")))[0] || null;
  }

  function render() {
    const today = repository.getToday();
    const week = repository.getWeek();
    const steps = Number(today.steps?.value || 0);
    const stepGoal = Number(today.steps?.goal || 8000);
    const water = Number(today.water?.cups || 0);
    const waterGoal = Number(today.water?.goal || 8);
    const exerciseSessions = today.exercise?.sessions || [];
    const breathingSessions = today.breathing?.sessions || [];
    const breathingMinutes = breathingSessions.reduce((sum, item) => sum + Number(item.minutes || 0), 0);
    const completed = [steps >= stepGoal, water >= waterGoal, exerciseSessions.length > 0, breathingMinutes >= 3].filter(Boolean).length;
    const progress = Math.round(completed / 4 * 100);
    const mood = todayMood();
    const average = Math.round(week.reduce((sum, day) => sum + Number(day.steps?.value || 0), 0) / Math.max(1, week.length));
    const lastExercise = latestExercise(week);

    $("selfcare-mood").textContent = mood.label;
    $("selfcare-mood-note").textContent = mood.note;
    $("selfcare-progress-value").textContent = `${progress}%`;
    $("selfcare-progress-fill").style.width = `${progress}%`;
    $("selfcare-progress-fill").parentElement.setAttribute("aria-valuenow", String(progress));
    $("selfcare-reminder").textContent = water < waterGoal
      ? `今天还可以慢慢补 ${waterGoal - water} 杯水，不用一次完成。`
      : "今天的喝水目标已经完成，照顾自己的节奏很好。";

    $("selfcare-steps").textContent = steps.toLocaleString("zh-CN");
    $("selfcare-steps-average").textContent = `本周平均 ${average.toLocaleString("zh-CN")} 步`;
    $("selfcare-steps-source").textContent = sourceLabel(today.steps?.source);
    $("selfcare-water").textContent = `${water} / ${waterGoal}`;
    $("selfcare-water-source").textContent = sourceLabel(today.water?.source);
    $("selfcare-exercise").textContent = lastExercise ? lastExercise.label || "散步" : "还没有记录";
    $("selfcare-exercise-note").textContent = lastExercise ? `${lastExercise.minutes || 20} 分钟 · ${sourceLabel(lastExercise.source)}` : "走一小段也算数";
    $("selfcare-breathing").textContent = `${breathingSessions.length} 次`;
    $("selfcare-breathing-note").textContent = `今天共 ${breathingMinutes} 分钟`;
  }

  function announce(message) {
    const status = $("selfcare-status");
    status.textContent = message;
    global.setTimeout(() => { if (status.textContent === message) status.textContent = ""; }, 2200);
  }

  function bind() {
    $("selfcare-water-add")?.addEventListener("click", () => {
      repository.incrementWater(); render(); announce("已记下一杯水");
    });
    $("selfcare-walk-add")?.addEventListener("click", () => {
      repository.recordWalk(); render(); announce("已记录一次散步");
    });
    $("selfcare-breathing-add")?.addEventListener("click", () => {
      repository.recordBreathing(); render(); announce("已记录 3 分钟呼吸");
    });
  }

  function init() {
    if (!$("selfcare-overview")) return;
    bind();
    render();
    global.addEventListener("storage", event => {
      if (event.key === api.KEYS.data) render();
    });
    global.XinyuSelfCareHome = {repository, render};
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once: true});
  else init();
})(window);
