(function (global) {
  "use strict";

  function selfReportLabel(text) {
    const value = String(text || "");
    if (/难受|不舒服|低落|伤心|想哭|委屈/.test(value)) return "低落";
    if (/累|疲惫|没精神|困/.test(value)) return "疲惫";
    if (/焦虑|紧张|担心|不安|害怕/.test(value)) return "不安";
    if (/生气|烦|恼火|愤怒/.test(value)) return "烦闷";
    if (/开心|高兴|顺利|轻松/.test(value)) return "开心";
    return "";
  }

  function fallbackReply(text) {
    const label = selfReportLabel(text);
    if (label === "低落") return "听起来你现在不太舒服。先不用急着解释原因，可以慢慢说，也可以只是让我陪你待一会儿。是身体上更难受，还是心里更闷一点？";
    if (label === "疲惫") return "听见你说累了，今天或许已经消耗了不少力气。可以先把肩膀放松一下，喝口水；如果愿意，也可以告诉我最让你费劲的是哪一段。";
    if (label === "不安") return "这份不安值得被认真听见，不必马上把它压下去。我们可以先从最具体的一件担心说起，也可以一起做三次慢一点的呼吸。";
    if (label === "烦闷") return "听起来有些事情正让你很烦。你不用先把情绪整理得很完整，想到哪里就说到哪里；我会跟着你的感受听，不急着下结论。";
    if (label === "开心") return "听到你今天有一点开心，我也替你留住这份轻松。愿意的话，可以说说是哪件小事让心里亮了一下，让这个瞬间多停一会儿。";
    return "我在听。你不需要配合任何情绪判断，按自己的感受慢慢说就好；如果现在还不想展开，也可以只告诉我，希望被陪着还是想一起理一理。";
  }

  function buildPayload({message, diaryText = "", recentChat = [], visualHint = "", userName = ""}) {
    const selfReport = selfReportLabel(message);
    const chatContext = recentChat.slice(-4).map(item => `${item.role === "user" ? "用户" : "小屿"}：${item.content}`).join("；");
    return {
      message: String(message || "").trim(),
      emotion: selfReport,
      diary_text: String(diaryText || "").slice(0, 200),
      context: [
        `用户本轮自述${selfReport ? `表达了“${selfReport}”` : "为最高优先级"}`,
        diaryText ? `今日日记：${String(diaryText).slice(0, 120)}` : "",
        chatContext ? `近期对话：${chatContext.slice(0, 180)}` : "",
        visualHint ? `摄像头弱线索（不得覆盖用户自述）：${visualHint}` : "",
      ].filter(Boolean).join("；").slice(0, 500),
      user_name: userName,
    };
  }

  global.XinyuEmotionChat = Object.freeze({selfReportLabel, fallbackReply, buildPayload});
})(typeof window !== "undefined" ? window : globalThis);
