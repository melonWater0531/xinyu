(function (global) {
  "use strict";

  const MOOD_LEVEL = Object.freeze({
    Happiness: "bright", Happy: "bright", Surprise: "bright", bright: "bright",
    Neutral: "neutral", Calm: "neutral", neutral: "neutral",
    Sadness: "low", Sad: "low", Anger: "low", Angry: "low", Fear: "low",
    Disgust: "low", Contempt: "low", low: "low",
  });
  const LABEL = {low: "低落", neutral: "平稳", bright: "明亮"};
  const Y = {low: 132, neutral: 82, bright: 32};
  const escapeHTML = value => String(value || "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const timeOf = item => {
    const raw = String(item.time || item.at || item.ts || item.created_at || "");
    const match = raw.match(/(?:T|\s)(\d{2}:\d{2})/) || raw.match(/^(\d{2}:\d{2})/);
    return match?.[1] || "12:00";
  };

  function demoPoints(date) {
    return [
      {date, time: "09:00", mood: "neutral", label: "慢慢开始", source: "demo"},
      {date, time: "12:30", mood: "low", label: "有点累", source: "demo"},
      {date, time: "18:00", mood: "bright", label: "松下来", source: "demo"},
      {date, time: "22:00", mood: "neutral", label: "安静收尾", source: "demo"},
    ];
  }

  function buildMoodTrendPointsForDate(date, diaryEntries = [], moodEvents = [], demoMode = false) {
    const rows = [...diaryEntries, ...moodEvents]
      .filter(item => item && String(item.date || item.at || item.ts || item.created_at || "").slice(0, 10) === date)
      .map(item => ({
        date,
        time: timeOf(item),
        mood: MOOD_LEVEL[item.mood || item.emotion] || "neutral",
        label: String(item.label || item.note || item.content || LABEL[MOOD_LEVEL[item.mood || item.emotion]] || "平稳").slice(0, 8),
        source: item.source || "local",
      }))
      .sort((a, b) => a.time.localeCompare(b.time));
    const turns = rows.filter((point, index) => index === 0 || point.mood !== rows[index - 1].mood);
    if (turns.length) return turns.slice(0, 6);
    return demoMode ? demoPoints(date) : [];
  }

  function renderMoodTrend(container, points, options = {}) {
    if (!container) return;
    if (!points.length) {
      container.innerHTML = '<div class="mood-trend-empty">今天还没有明显转折点，<br>晚些时候再看看。</div>';
      return;
    }
    const width = 340;
    const left = 46;
    const right = 16;
    const usable = width - left - right;
    const coords = points.map((point, index) => ({...point, x: left + (points.length === 1 ? usable / 2 : usable * index / (points.length - 1)), y: Y[point.mood]}));
    const path = coords.map(point => `${point.x},${point.y}`).join(" ");
    const nodes = coords.map(point => `
      <g>
        <circle cx="${point.x}" cy="${point.y}" r="5" fill="#FFFDF8" stroke="#8DAE7F" stroke-width="3"/>
        <text x="${point.x}" y="${point.y - 11}" text-anchor="middle" fill="#6F6258" font-size="9">${escapeHTML(point.label)}</text>
        <text x="${point.x}" y="166" text-anchor="middle" fill="#A49382" font-size="9">${escapeHTML(point.time)}</text>
      </g>`).join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} 178" role="img" aria-labelledby="mood-trend-svg-title mood-trend-svg-desc">
      <title id="mood-trend-svg-title">当日情绪转折趋势</title>
      <desc id="mood-trend-svg-desc">${escapeHTML(points.map(point => `${point.time}${point.label}`).join("，"))}</desc>
      <g fill="#A49382" font-size="9"><text x="3" y="35">明亮</text><text x="3" y="85">平稳</text><text x="3" y="135">低落</text></g>
      <g stroke="#E4D8C9" stroke-width="1" stroke-dasharray="3 5"><line x1="${left}" y1="32" x2="${width-right}" y2="32"/><line x1="${left}" y1="82" x2="${width-right}" y2="82"/><line x1="${left}" y1="132" x2="${width-right}" y2="132"/></g>
      ${coords.length > 1 ? `<polyline points="${path}" fill="none" stroke="#8DAE7F" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>` : ""}
      ${nodes}
    </svg>`;
    container.dataset.source = options.demoMode ? "demo" : "local";
  }

  global.XinyuMoodTrend = Object.freeze({buildMoodTrendPointsForDate, renderMoodTrend});
})(typeof window !== "undefined" ? window : globalThis);
