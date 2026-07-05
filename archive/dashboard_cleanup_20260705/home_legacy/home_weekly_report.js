(function (global) {
  "use strict";

  const moodName = value => ({
    Happiness: "明亮", Neutral: "平稳", Sadness: "低落", Anger: "烦闷",
    Fear: "不安", Surprise: "起伏", Disgust: "不适", Contempt: "疏离",
  }[value] || "多样");

  function collectSelfCare() {
    const repository = global.XinyuSelfCareHome?.repository || (global.XinyuSelfCare ? new global.XinyuSelfCare.SelfCareRepository() : null);
    return repository ? repository.getWeek() : [];
  }

  function summarize(entries, selfcare) {
    const counts = {};
    entries.forEach(entry => { if (entry.emotion) counts[entry.emotion] = (counts[entry.emotion] || 0) + 1; });
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0];
    const stepAverage = Math.round(selfcare.reduce((sum, day) => sum + Number(day.steps?.value || 0), 0) / Math.max(1, selfcare.length));
    const waterAverage = selfcare.reduce((sum, day) => sum + Number(day.water?.cups || 0), 0) / Math.max(1, selfcare.length);
    const exerciseCount = selfcare.reduce((sum, day) => sum + (day.exercise?.sessions || []).length, 0);
    const meditationCount = selfcare.reduce((sum, day) => sum + (day.meditation?.sessions || day.breathing?.sessions || []).length, 0);
    const hasDemo = selfcare.some(day => [day.source, day.steps?.source, day.water?.source].includes("demo"));
    const allDemo = selfcare.length > 0 && selfcare.every(day => day.source === "demo");
    return {days: entries.length, mood: moodName(top), stepAverage, waterAverage, exerciseCount, meditationCount, hasDemo, allDemo};
  }

  function careTrend(summary) {
    const movement = summary.stepAverage >= 6500 || summary.exerciseCount >= 2 ? "身体活动保持着不错的节奏" : "身体活动还有一些温柔补足的空间";
    const water = summary.waterAverage >= 6 ? "补水也比较稳定" : "喝水常常容易被忙碌挤到后面";
    const pause = summary.meditationCount ? "你也给自己留过安静冥想的片刻" : "安静停下来的时刻还可以再多一点";
    return `${movement}，${water}，${pause}`;
  }

  function buildFallback(entries, selfcare = collectSelfCare(), options = {}) {
    const summary = summarize(entries, selfcare);
    const demo = options.demoMode ?? summary.allDemo;
    let content;
    if (demo) {
      content = `这是一份演示数据预览：这一周的心情记录整体偏${summary.mood}，${careTrend(summary)}。愿意停下来观察自己的感受，也愿意为身体做一点小事，本身就值得肯定。下周可以把喝水和短暂散步放在同一个固定时刻，让照顾自己更自然地融进一天。`;
    } else if (summary.days < 3) {
      content = `这周留下的记录还不多，但你已经愿意停下来看看自己的感受，这份觉察很珍贵。${careTrend(summary)}。下周可以选一个固定时刻，喝杯水后走上十分钟，让照顾自己更容易坚持。`;
    } else if (summary.days <= 5) {
      content = `这周的心情有自己的起伏，记录里更多呈现出${summary.mood}的底色。你愿意把真实感受写下来，也在日常里慢慢照顾身体，这份认真值得肯定。${careTrend(summary)}。下周不妨保留一个固定的十分钟，喝水、走动，再做一次短冥想，给忙碌留一道缓冲。`;
    } else {
      content = `这一周的记录比较完整，心情虽然有变化，但整体呈现出${summary.mood}的底色。你持续为自己的感受留出位置，也在一些普通时刻做了照顾身体的选择，这种稳定的觉察很难得。${careTrend(summary)}，仍有几天可能被忙碌打乱。下周可以把目标收得更小：每天选一个固定时刻，先喝一杯水，再走动十分钟；如果心里拥挤，就留三分钟安静冥想。`;
    }
    return {content, sourceLabel: summary.hasDemo ? "含演示数据" : "本机记录", summary};
  }

  function normalizeContent(content, fallback) {
    const value = String(content || "").replace(/^#+\s*/g, "").replace(/\s*\n+\s*/g, "").trim();
    if (value.length < 80) return fallback;
    if (value.length <= 220) return value;
    const window = value.slice(0, 220);
    const boundary = Math.max(window.lastIndexOf("。"), window.lastIndexOf("！"), window.lastIndexOf("？"));
    return boundary >= 120 ? window.slice(0, boundary + 1) : `${window.slice(0, 219)}。`;
  }

  global.XinyuWeeklyReport = Object.freeze({collectSelfCare, summarize, buildFallback, normalizeContent});
})(typeof window !== "undefined" ? window : globalThis);
