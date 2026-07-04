(function (global) {
  "use strict";

  const KEYS = Object.freeze({
    data: "xinyu.selfcare.v1",
    provider: "xinyu.selfcare.provider",
    demoMode: "xinyu.selfcare.demoMode",
  });

  const pad = value => String(value).padStart(2, "0");
  const dateKey = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const clone = value => JSON.parse(JSON.stringify(value));

  function demoDay(date, index) {
    const steps = [4260, 6180, 7350, 5820, 8100, 6940, 7680][index];
    const water = [5, 6, 7, 5, 8, 6, 6][index];
    const exercise = index === 4
      ? [{type: "walk", label: "傍晚散步", minutes: 24, at: `${dateKey(date)}T18:20:00`, source: "demo"}]
      : [];
    const breathing = index % 3 === 0
      ? [{minutes: 3, at: `${dateKey(date)}T21:10:00`, source: "demo"}]
      : [];
    return {
      date: dateKey(date),
      steps: {value: steps, goal: 8000, source: "demo"},
      water: {cups: water, goal: 8, source: "demo"},
      exercise: {sessions: exercise, source: "demo"},
      breathing: {sessions: breathing, source: "demo"},
      source: "demo",
    };
  }

  class DemoSelfCareProvider {
    getWeek(anchor = new Date()) {
      const days = [];
      for (let offset = 6; offset >= 0; offset -= 1) {
        const date = new Date(anchor);
        date.setHours(12, 0, 0, 0);
        date.setDate(date.getDate() - offset);
        days.push(demoDay(date, 6 - offset));
      }
      return days;
    }

    getDay(key, anchor = new Date()) {
      return this.getWeek(anchor).find(day => day.date === key) || null;
    }
  }

  class LocalStorageSelfCareProvider {
    constructor(storage = global.localStorage) {
      this.storage = storage;
    }

    readAll() {
      try {
        const value = JSON.parse(this.storage.getItem(KEYS.data) || "{}");
        return value && typeof value === "object" ? value : {};
      } catch (_error) {
        return {};
      }
    }

    writeAll(value) {
      this.storage.setItem(KEYS.data, JSON.stringify(value));
      return value;
    }

    getDay(key) {
      return clone(this.readAll()[key] || null);
    }

    updateDay(key, updater) {
      const all = this.readAll();
      const current = all[key] || {date: key};
      all[key] = updater(clone(current));
      all[key].date = key;
      all[key].source = "local";
      this.writeAll(all);
      return clone(all[key]);
    }

    setSteps(key, value, goal = 8000) {
      return this.updateDay(key, day => ({
        ...day,
        steps: {value: Math.max(0, Number(value) || 0), goal, source: "local"},
      }));
    }

    setWater(key, cups, goal = 8) {
      return this.updateDay(key, day => ({
        ...day,
        water: {cups: Math.max(0, Number(cups) || 0), goal, source: "local"},
      }));
    }

    recordExercise(key, session) {
      return this.updateDay(key, day => ({
        ...day,
        exercise: {
          sessions: [...(day.exercise?.sessions || []), {...session, source: "local"}],
          source: "local",
        },
      }));
    }

    recordBreathing(key, session) {
      return this.updateDay(key, day => ({
        ...day,
        breathing: {
          sessions: [...(day.breathing?.sessions || []), {...session, source: "local"}],
          source: "local",
        },
      }));
    }
  }

  class NativeBridgeSelfCareProvider {
    isAvailable() {
      return Boolean(global.XinyuNativeBridge?.selfCare);
    }

    getDay(key) {
      if (!this.isAvailable()) return null;
      try {
        return global.XinyuNativeBridge.selfCare.getDay?.(key) || null;
      } catch (_error) {
        return null;
      }
    }
  }

  class HealthConnectProvider {
    isAvailable() { return false; }
    getSteps() { return null; }
  }

  class StepCounterProvider {
    isAvailable() { return false; }
    getSteps() { return null; }
  }

  class SelfCareRepository {
    constructor(options = {}) {
      this.demo = options.demo || new DemoSelfCareProvider();
      this.local = options.local || new LocalStorageSelfCareProvider(options.storage);
      this.native = options.native || new NativeBridgeSelfCareProvider();
      this.healthConnect = options.healthConnect || new HealthConnectProvider();
      this.stepCounter = options.stepCounter || new StepCounterProvider();
    }

    providerName() {
      try {
        return global.localStorage.getItem(KEYS.provider) || "auto";
      } catch (_error) {
        return "auto";
      }
    }

    getWeek(anchor = new Date()) {
      const demoDays = this.demo.getWeek(anchor);
      return demoDays.map(demo => this.mergeDay(demo, this.local.getDay(demo.date)));
    }

    getToday(anchor = new Date()) {
      const key = dateKey(anchor);
      const demo = this.demo.getDay(key, anchor) || demoDay(anchor, 6);
      const nativeDay = this.native.getDay(key);
      const localDay = this.local.getDay(key);
      return this.mergeDay(this.mergeDay(demo, nativeDay), localDay);
    }

    mergeDay(base, override) {
      if (!override) return clone(base);
      return {
        ...clone(base),
        ...clone(override),
        steps: override.steps || base.steps,
        water: override.water || base.water,
        exercise: override.exercise || base.exercise,
        breathing: override.breathing || base.breathing,
        source: override.source || base.source,
      };
    }

    incrementWater(anchor = new Date()) {
      const today = this.getToday(anchor);
      this.local.setWater(today.date, Number(today.water?.cups || 0) + 1, today.water?.goal || 8);
      return this.getToday(anchor);
    }

    recordWalk(anchor = new Date(), minutes = 20) {
      const today = this.getToday(anchor);
      this.local.recordExercise(today.date, {
        type: "walk", label: "散步", minutes, at: new Date().toISOString(),
      });
      return this.getToday(anchor);
    }

    recordBreathing(anchor = new Date(), minutes = 3) {
      const today = this.getToday(anchor);
      this.local.recordBreathing(today.date, {minutes, at: new Date().toISOString()});
      return this.getToday(anchor);
    }
  }

  global.XinyuSelfCare = Object.freeze({
    KEYS,
    SelfCareRepository,
    DemoSelfCareProvider,
    LocalStorageSelfCareProvider,
    NativeBridgeSelfCareProvider,
    HealthConnectProvider,
    StepCounterProvider,
  });
})(typeof window !== "undefined" ? window : globalThis);
