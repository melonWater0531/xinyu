(() => {
  "use strict";

  const currentDate = "2026-07-04";
  const meetingId = "meeting-2026-07-04-activity-budget";

  const meetingMinutesMarkdown = `# 下半年活动规划与预算申报周会

会议主题：下半年活动规划与预算申报周会

会议摘要：会议明确了书画展、迎新摊点、辩论赛及手作活动四大核心任务，制定了“高报低批”的预算申报策略，并要求在7月9日前完成所有策划与预算表的填报。

---

## 一、核心活动规划与分工

针对下半年即将开展的四大核心活动，会议明确了各自的执行方案与负责人：

### 1. 书画展活动（待定）

场地与形式：初步拟定图书馆1楼东侧展厅或C2区域，需确认是否举办讲座等系列配套活动。

执行难点：因暑期成员不在校导致线下对接困难，决定先由部长联系办展老师了解流程，若流程复杂则放弃承办。

### 2. 迎新夏令营摊点活动

活动形式：在C2设立学生会专属摊位，设置签名墙（KT板）、便利贴寄语及校园知识问答环节。

物资准备：计划发放基于IP形象定制的钥匙扣、帆布包等文创产品作为奖品。

人员安排：利用生活部建立的志愿者池进行人员调配，具体执行由公共关系部负责设计。

### 3. 辩论赛筹备

时间节点：预计8月底开始征集辩题，建立辩题池并发起投票。

合作模式：由部长负责，后续将联系辩论社共同策划具体赛制。

### 4. 手作系列活动

活动形式：由李同学负责，结合节日或热门元素开展手工制作活动。

物资采购：针对拼豆等材料，建议购买大分量分装或成品套装，避免分装麻烦。

---

## 二、新增任务与协作

### 1. 社团活动协助

龙舟社筹建：团委要求学生会协助策划龙舟社的建立，需纳入活动规划。

艺术团支持：协助管弦乐团与合唱团策划活动形式，对方有独立经费，学生会主要负责创意支持。

### 2. 策划与预算整合

全量申报：除核心活动外，鼓励成员提交其他创意活动策划，一并纳入预算表进行申报。

协作机制：若成员在寻找价格或策划上有困难，可寻求团队协助。

---

## 三、待办事项

- 联系办展老师确认书画展流程可行性，若复杂则放弃承办。@陈同学
- 完成迎新摊点KT板背景设计及问答题库准备。@陈同学
- 完善手作系列活动策划，并调研拼豆等材料价格。@李同学
- 整理辩论赛辩题池，准备8月底投票。@陈同学
- 填写活动策划与预算表，需在7月9日前完成。@全体成员`;

  const meetings = {
    currentMeeting: {
      id: meetingId,
      title: "下半年活动规划与预算申报周会",
      date: "2026-07-04",
      time: "10:30",
      duration: "42:18",
      status: "已整理",
      participants: ["陈同学", "李同学", "公共关系部", "全体成员"],
      summary: "会议明确了书画展、迎新摊点、辩论赛及手作活动四大核心任务，制定了预算申报策略，并要求在7月9日前完成所有策划与预算表的填报。",
      coreContents: [
        "书画展需先确认图书馆场地、讲座配套和办展流程，流程过重时及时调整承办安排。",
        "迎新摊点围绕签名墙、便利贴寄语和校园知识问答展开，并准备钥匙扣、帆布包等文创奖品。",
        "辩论赛预计8月底征集辩题、建立辩题池并发起投票，后续与辩论社共同细化赛制。",
        "手作系列活动结合节日或热门元素推进，材料采购优先选择大分量分装或成品套装。"
      ],
      todos: [
        "联系办展老师确认书画展流程可行性，若复杂则放弃承办。@陈同学",
        "完成迎新摊点KT板背景设计及问答题库准备。@陈同学",
        "完善手作系列活动策划，并调研拼豆等材料价格。@李同学",
        "整理辩论赛辩题池，准备8月底投票。@陈同学",
        "填写活动策划与预算表，需在7月9日前完成。@全体成员"
      ],
      tags: ["活动规划", "预算申报", "迎新", "辩论赛", "手作活动"],
      minutesMarkdownPath: "data/meeting_summary_2026-07-04.md",
      minutesMarkdown: meetingMinutesMarkdown
    },
    history: [
      {
        id: meetingId,
        title: "下半年活动规划与预算申报周会",
        date: "2026-07-04",
        time: "10:30",
        duration: "42:18",
        status: "已整理",
        summary: "确认下半年四大核心活动、预算申报策略和7月9日前的策划填报节点。"
      }
    ]
  };

  const focusDisplays = ["专注状态很好", "整体比较专注", "偶尔有些走神", "需要多休息", "今天适合放慢一点"];
  const moodPatterns = [
    [
      { time: "09:00", mood: "calm", display: "平静" },
      { time: "14:30", mood: "focused", display: "专注" },
      { time: "21:00", mood: "relaxed", display: "放松" }
    ],
    [
      { time: "08:40", mood: "calm", display: "平静" },
      { time: "11:20", mood: "busy", display: "忙碌" },
      { time: "16:40", mood: "tired", display: "有点累" },
      { time: "21:30", mood: "relaxed", display: "放松" }
    ],
    [
      { time: "09:10", mood: "bright", display: "明亮" },
      { time: "13:50", mood: "focused", display: "专注" },
      { time: "18:20", mood: "calm", display: "平静" }
    ],
    [
      { time: "10:00", mood: "relaxed", display: "放松" },
      { time: "15:10", mood: "bright", display: "明亮" },
      { time: "22:00", mood: "calm", display: "平静" }
    ],
    [
      { time: "08:50", mood: "calm", display: "平静" },
      { time: "12:30", mood: "pressure", display: "压力高" },
      { time: "17:40", mood: "tired", display: "有点累" },
      { time: "20:50", mood: "clear", display: "平和" }
    ]
  ];
  const meetingDays = new Set([
    "2026-06-02", "2026-06-05", "2026-06-09", "2026-06-12", "2026-06-16", "2026-06-18",
    "2026-06-23", "2026-06-26", "2026-06-30", "2026-07-02", "2026-07-04"
  ]);
  const meditationDays = new Set([
    "2026-06-01", "2026-06-03", "2026-06-06", "2026-06-10", "2026-06-14", "2026-06-17",
    "2026-06-20", "2026-06-22", "2026-06-25", "2026-06-28", "2026-07-01"
  ]);
  const weatherCycle = ["晴朗", "多云", "微凉", "下雨", "夜晚"];
  const meetingTitles = {
    "2026-06-02": "社团协作沟通",
    "2026-06-05": "迎新物资确认",
    "2026-06-09": "项目节奏同步",
    "2026-06-12": "活动执行复盘",
    "2026-06-16": "预算材料讨论",
    "2026-06-18": "志愿者安排沟通",
    "2026-06-23": "宣传内容确认",
    "2026-06-26": "月末任务整理",
    "2026-06-30": "七月安排预沟通",
    "2026-07-02": "活动方案检查",
    "2026-07-04": "下半年活动规划与预算申报周会"
  };

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function dateKey(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function getWeekKey(dateText) {
    const date = new Date(`${dateText}T00:00:00`);
    const first = new Date(Date.UTC(date.getFullYear(), 0, 1));
    const day = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNumber = Math.floor((day - first) / 86400000) + 1;
    return `${date.getFullYear()}-W${pad(Math.ceil(dayNumber / 7))}`;
  }

  function diaryFor(dateText, index, day) {
    if (dateText === currentDate) {
      return "今天主要在整理下半年活动规划和预算申报，信息很多，下午有点累，但把任务拆开之后心里清楚了一些。";
    }
    if (day.hadMeeting) {
      return `今天开完${day.meetingTitle}后，脑子里还在整理几件待办。虽然有点耗神，但把重点写下来之后，晚上轻松了一些。`;
    }
    if (day.meditation) {
      return "今天给自己留了一小段安静时间，没急着处理下一件事。呼吸慢下来以后，心里像被轻轻整理过。";
    }
    if (day.steps < 3600 || day.waterCups <= 4) {
      return "今天大部分时间都坐着，水也喝得不算多。事情没有特别糟，只是身体好像在提醒我，晚上要慢一点。";
    }
    if (index % 5 === 0) {
      return "今天的节奏比前几天顺一些，完成了几件小事。没有特别兴奋，但心里比较踏实，像把桌面清出来了一点。";
    }
    return "今天的状态起伏不大，上午比较安静，下午处理了一些零散事情。晚上回看时，觉得自己已经尽力照顾好了这一天。";
  }

  function buildDiaryAssistantReply(dayData, diaryText) {
    if (dayData.date === currentDate) {
      return "小屿看到你今天处理了很多需要统筹的事情，下午的压力确实更明显。已经把任务拆清楚很不容易了，今晚可以先补一杯水，给自己留一点安静的时间。";
    }
    const care = [];
    if (dayData.waterCups <= 4) care.push("今晚可以先补一杯水");
    if (dayData.steps < 3600) care.push("让身体轻轻走动几分钟");
    if (!dayData.meditation) care.push("给自己留一小段安静时间");
    const careText = care.length ? `如果愿意，${care.slice(0, 2).join("，")}。` : "这份稳定感值得被好好收下。";
    if (dayData.hadMeeting) {
      return `小屿看到你今天在交流和整理之间切换，消耗感是可以理解的。你已经把重点慢慢放回纸面上了，${careText}`;
    }
    if (dayData.meditation) {
      return `你今天有让自己安静下来一会儿，这不是逃开事情，而是在给心里留一点缓冲。${careText}`;
    }
    return `小屿读到这一天的节奏比较细碎，但你仍然在认真收拾自己的状态。${careText}`;
  }

  function dominantMoodId(dayData) {
    if (dayData.mainState.includes("疲惫")) return "tired";
    if (dayData.mainState.includes("放松")) return "calm";
    if (dayData.mainState.includes("明亮")) return "joy";
    if (dayData.mainState.includes("压力")) return "worried";
    return "calm";
  }

  function buildDailyRecords() {
    const records = {};
    const start = new Date("2026-06-01T00:00:00");
    const end = new Date("2026-07-04T00:00:00");
    for (let cursor = new Date(start), index = 0; cursor <= end; cursor.setDate(cursor.getDate() + 1), index += 1) {
      const key = dateKey(cursor);
      const weekend = cursor.getDay() === 0 || cursor.getDay() === 6;
      const hadMeeting = meetingDays.has(key);
      const meditation = meditationDays.has(key);
      const steps = weekend ? 5600 + ((index * 811) % 3600) : 2800 + ((index * 743) % 5400);
      const waterCups = 3 + ((index * 5 + (hadMeeting ? 1 : 0)) % 6);
      const focusScore = weekend ? 72 + (index % 14) : 58 + ((index * 7) % 34);
      const mainState = hadMeeting
        ? (index % 2 ? "信息较多，有点疲惫" : "忙碌之后更清楚")
        : weekend
          ? (index % 3 ? "状态放松" : "心情明亮")
          : (focusScore > 80 ? "状态平稳" : "节奏稍紧");
      const day = {
        date: key,
        emotionTrend: moodPatterns[(index + (hadMeeting ? 4 : 0)) % moodPatterns.length].map((point) => ({ ...point })),
        focusScore,
        focusDisplay: focusDisplays[focusScore >= 82 ? 0 : focusScore >= 70 ? 1 : focusScore >= 58 ? 2 : 3],
        mainState,
        steps: Math.max(2200, Math.min(9200, steps)),
        waterCups: Math.max(3, Math.min(8, waterCups)),
        waterGoal: 8,
        meditation,
        hadMeeting,
        meetingId: hadMeeting && key === currentDate ? meetingId : "",
        meetingTitle: hadMeeting ? meetingTitles[key] : "",
        diary: "",
        assistantReply: "",
        weekKey: getWeekKey(key),
        weather: weatherCycle[index % weatherCycle.length],
        tags: hadMeeting ? ["会议", "整理"] : meditation ? ["独处", "休息"] : ["日常", weekend ? "放松" : "学习"],
        moodId: "calm"
      };
      if (key === currentDate) {
        Object.assign(day, {
          mainState: "有点疲惫",
          steps: 3200,
          waterCups: 5,
          waterGoal: 8,
          meditation: false,
          hadMeeting: true,
          meetingId,
          meetingTitle: "下半年活动规划与预算申报周会",
          emotionTrend: [
            { time: "09:00", mood: "calm", display: "平静" },
            { time: "11:30", mood: "focused", display: "专注" },
            { time: "15:40", mood: "tired", display: "有点累" },
            { time: "20:30", mood: "clear", display: "平和" }
          ],
          focusScore: 64,
          focusDisplay: "今天适合放慢一点",
          weather: "多云",
          tags: ["会议", "预算", "整理"],
          moodId: "tired"
        });
      }
      day.diary = diaryFor(key, index, day);
      day.assistantReply = buildDiaryAssistantReply(day, day.diary);
      day.moodId = day.moodId || dominantMoodId(day);
      records[key] = day;
    }
    return records;
  }

  const dailyRecords = buildDailyRecords();

  function collectWeekData(weekKey) {
    return Object.values(dailyRecords).filter((day) => day.weekKey === weekKey);
  }

  const weeklyReports = {
    "2026-W23": {
      weekKey: "2026-W23",
      rangeLabel: "6月1日 - 6月7日",
      title: "这一周的状态回顾",
      summary: "这一周像是在重新找到节奏：有几天被会议和物资确认推着往前走，也有几次短暂的安静时间把心绪放慢。你没有把疲惫压下去，而是开始把它写下来、拆开看。喝水和步数有起伏，但周末的放松让状态慢慢回到平稳。下周可以继续保留一个小习惯：每天先照顾身体，再处理最需要统筹的那件事。",
      highlights: ["把迎新物资和社团协作的重点整理清楚", "几次安静时间帮助状态回落"],
      careSummary: "步数和喝水有高有低，但你已经开始留意身体给出的提醒。",
      suggestion: "下周给每天安排一个固定补水节点，不需要多，只要稳定。"
    },
    "2026-W24": {
      weekKey: "2026-W24",
      rangeLabel: "6月8日 - 6月14日",
      title: "这一周的状态回顾",
      summary: "这一周的关键词是推进。项目节奏同步和活动复盘让你的注意力多次被拉到细节里，疲惫感也更容易在下午出现。好在你没有一直绷着，几天的冥想和周末的慢节奏让状态有了缓冲。小屿看到你在忙碌里仍然保留了一点自我照顾，这很重要。下周可以把任务拆得再小一点，减少临近截止时的挤压感。",
      highlights: ["完成了几次关键沟通", "周末状态更轻，日记语气也更松"],
      careSummary: "冥想和步数帮助你从紧绷里退出来一点。",
      suggestion: "遇到大任务时，先写下三个最小下一步。"
    },
    "2026-W25": {
      weekKey: "2026-W25",
      rangeLabel: "6月15日 - 6月21日",
      title: "这一周的状态回顾",
      summary: "这一周有预算讨论、志愿者安排，也有一些需要反复确认的细节。你的状态不是一直低落，而是在压力和清楚之间来回切换。几篇日记里能看到你已经学会把复杂事情放回清单里，而不是全部压在心里。喝水有几天偏少，身体提醒比情绪更早出现。下周可以在会议日结束后留十分钟，只做整理，不继续追加新任务。",
      highlights: ["预算和志愿者安排逐渐落到纸面", "能觉察到会议后的消耗感"],
      careSummary: "会议日更容易忘记喝水，晚间需要一点恢复空间。",
      suggestion: "会议结束后先补水，再写三条结论。"
    },
    "2026-W26": {
      weekKey: "2026-W26",
      rangeLabel: "6月22日 - 6月28日",
      title: "这一周的状态回顾",
      summary: "这一周比前面更像收束：宣传内容、月末任务和一些零散沟通慢慢排好顺序。你有几天显得比较专注，也有几天因为坐得久、喝水少而觉得身体变沉。可贵的是，你没有只盯着完成度，也给自己留了安静和走动的空间。下周进入新的安排前，可以先确认哪些事情真的重要，哪些只是暂时显得很急。",
      highlights: ["月末任务逐步收口", "冥想让晚间状态更稳"],
      careSummary: "自我照顾不是额外任务，而是在帮你维持节奏。",
      suggestion: "下周开始前，先列出三件最值得投入的事。"
    },
    "2026-W27": {
      weekKey: "2026-W27",
      rangeLabel: "6月29日 - 7月4日",
      title: "这一周的状态回顾",
      summary: "这一周的重心明显转向下半年活动规划与预算申报。七月初的方案检查和7月4日的周会让信息量集中涌来，你有压力，也有把混乱拆开的整理感。喝水和步数在会议日偏少，说明身体已经在提醒你放慢一点。小屿看到你不是被任务推着走，而是在努力把书画展、迎新、辩论赛和手作活动分清主次。下周可以先处理7月9日前最关键的表格，再给自己留一段不被打扰的恢复时间。",
      highlights: ["活动规划和预算申报方向更清楚", "能把疲惫写下来，而不是硬撑过去"],
      careSummary: "会议日消耗较高，补水、走动和短暂安静都很值得保留。",
      suggestion: "先完成7月9日前的预算表关键项，再安排一次真正的休息。"
    }
  };

  function buildWeeklyReport(weekData) {
    if (!weekData?.length) return null;
    return weeklyReports[weekData[0].weekKey] || null;
  }

  function getRecentDiaryEntries(limit = 5) {
    return Object.values(dailyRecords)
      .sort((a, b) => b.date.localeCompare(a.date))
      .slice(0, limit)
      .map((day) => ({ date: day.date, diary: day.diary, assistantReply: day.assistantReply, mainState: day.mainState }));
  }

  function openDiaryModal(date) {
    return dailyRecords[date] || null;
  }

  function saveDiaryEntry(date, text) {
    const day = dailyRecords[date];
    if (!day) return null;
    day.diary = String(text || "").trim() || day.diary;
    day.assistantReply = buildDiaryAssistantReply(day, day.diary);
    return day;
  }

  function openWeeklyReportModal(weekKey) {
    return weeklyReports[weekKey] || null;
  }

  function renderWeeklyReportHistory() {
    return Object.values(weeklyReports).map((report) => ({
      weekKey: report.weekKey,
      rangeLabel: report.rangeLabel,
      title: report.title,
      summary: report.summary
    }));
  }

  const assistantMemory = {
    currentDate,
    currentDay: dailyRecords[currentDate],
    recentDiary: getRecentDiaryEntries(5),
    currentMeeting: meetings.currentMeeting,
    currentWeeklyReport: weeklyReports["2026-W27"],
    careSuggestion: "今天信息量比较大，先补一杯水，把7月9日前最关键的表格列出来，再给自己留一点不被打扰的安静时间。",
    quickInputs: ["我今天有点累", "帮我整理一下今天", "给我一些放松建议", "记录一下我的情绪"],
    quickReplies: {
      "我今天有点累": "小屿记得你今天处理了活动规划和预算申报，下午也出现了疲惫感。先不用继续把所有事都想完，可以补一杯水，再只选一件最小的待办。",
      "帮我整理一下今天": "今天的重点是下半年活动规划与预算申报周会：书画展、迎新摊点、辩论赛和手作活动都被拆开了，7月9日前要完成策划和预算表。你已经把混乱整理清楚了一些。",
      "给我一些放松建议": "今晚适合做很轻的恢复：先离开屏幕几分钟，喝点水，慢慢走一小圈。如果还想整理任务，只写三条明天最重要的事，不继续扩展。",
      "记录一下我的情绪": "我会把今天记为“有点疲惫，但事情更清楚了”。这不是诊断，只是帮你留住这一刻：信息很多，你也确实在努力把它们放回合适的位置。"
    }
  };

  window.XINYU_PREVIEW_DATA = {
    productName: "心屿",
    assistantName: "小屿",
    currentDate,
    meetings,
    dailyRecords,
    weeklyReports,
    assistantMemory,
    buildDiaryAssistantReply,
    getRecentDiaryEntries,
    openDiaryModal,
    saveDiaryEntry,
    getWeekKey,
    collectWeekData,
    buildWeeklyReport,
    openWeeklyReportModal,
    renderWeeklyReportHistory
  };
})();
