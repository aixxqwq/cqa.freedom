// Общие помощники интерфейса.
import { API_BASE } from "./config.js";

/** Ссылка на запись: свои загрузки лежат на нашем сервере (/uploads/...). */
export const mediaUrl = (u) => (u && u.startsWith("/") ? API_BASE + u : u);

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function toast(text, kind = "") {
  const box = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  box.appendChild(el);
  setTimeout(() => el.remove(), kind === "bad" ? 7000 : 3500);
}

// Небольшое хранилище настроек пользователя (группа, чек-лист). Работает и без localStorage.
const memory = {};
export const prefs = {
  get(key, def) {
    try { return localStorage.getItem("cqa." + key) ?? memory[key] ?? def; } catch { return memory[key] ?? def; }
  },
  set(key, value) {
    memory[key] = value;
    try { localStorage.setItem("cqa." + key, value); } catch { /* приватный режим */ }
  },
};

export const fmt = (n, digits = 2) =>
  n === null || n === undefined ? "—" : Number(n).toLocaleString("ru-RU", { minimumFractionDigits: 0, maximumFractionDigits: digits });

export const scoreClass = (n) => (n === null || n === undefined ? "" : n >= 85 ? "hi" : n >= 65 ? "mid" : "lo");

export const SPEAKERS = { operator: "Оператор", client: "Клиент", bot: "Бот", system: "Система" };
export const CHANNELS = { "agent chat": "Чат", "voice call": "Звонок" };

/** Бейдж ответа по типу критерия. */
export function answerBadge(answer, criterion) {
  if (answer === "na") return `<span class="badge na">N/A</span>`;
  if (!criterion) return `<span class="badge info">${esc(answer)}</span>`;
  if (criterion.type === "flag") {
    return answer === "yes"
      ? `<span class="badge bad">⚠ Нарушение выявлено</span>`
      : `<span class="badge ok">Нарушений нет</span>`;
  }
  if (criterion.type === "binary") {
    return answer === "yes" ? `<span class="badge ok">Да</span>` : `<span class="badge bad">Нет</span>`;
  }
  const max = criterion.scale?.max ?? 0;
  const min = criterion.scale?.min ?? 0;
  const ratio = (Number(answer) - min) / (max - min || 1);
  const kind = ratio >= 0.99 ? "ok" : ratio >= 0.5 ? "warn" : "bad";
  return `<span class="badge ${kind}">${esc(answer)} из ${esc(max)}</span>`;
}

/** Текст ответа без оформления — для истории изменений. */
export function answerText(answer, criterion) {
  if (answer === "na") return "N/A";
  if (criterion?.type === "flag") return answer === "yes" ? "нарушение" : "нет нарушений";
  if (criterion?.type === "binary") return answer === "yes" ? "Да" : "Нет";
  return `${answer} из ${criterion?.scale?.max ?? "?"}`;
}

/** Варианты ответа для формы (решение проверяющего). */
export function answerOptions(criterion) {
  const opts = [];
  if (criterion.type === "binary") opts.push(["yes", "Да"], ["no", "Нет"]);
  if (criterion.type === "flag") opts.push(["yes", "Нарушение выявлено"], ["no", "Нарушений нет"]);
  if (criterion.type === "scale") {
    const levels = criterion.scale.levels?.length
      ? criterion.scale.levels
      : Array.from({ length: criterion.scale.max - criterion.scale.min + 1 }, (_, i) => ({ value: criterion.scale.max - i, label: "" }));
    for (const l of levels) opts.push([String(l.value), `${l.value}${l.label ? " — " + l.label : ""}`]);
  }
  if (criterion.na_allowed) opts.push(["na", "N/A — " + (criterion.na_rule || "неприменимо")]);
  return opts;
}

export function parseAnswer(value, criterion) {
  if (criterion.type === "scale" && value !== "na") return Number(value);
  return value;
}

/** Время реплики звонка в секундах от начала записи (ISO 8601) или от 00:00 (MM:SS). */
export function secondsFrom(ts, firstTs) {
  if (!ts) return null;
  if (/^\d{1,2}:\d{2}(:\d{2})?$/.test(ts)) {
    return ts.split(":").map(Number).reduce((acc, n) => acc * 60 + n, 0);
  }
  const t = Date.parse(ts);
  const t0 = Date.parse(firstTs);
  if (Number.isNaN(t) || Number.isNaN(t0)) return null;
  return Math.max(0, (t - t0) / 1000);
}

export function shortTime(ts) {
  if (!ts) return "";
  const m = String(ts).match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : ts;
}

export function messageHtml(m, { firstTs, audio, inRange = false, evidence = false } = {}) {
  const role = esc(m.speaker);
  const sec = audio ? secondsFrom(m.timestamp, firstTs) : null;
  const time = sec !== null
    ? `<span class="ts-link" data-seek="${sec}" title="Прослушать с этого места">▶ ${esc(shortTime(m.timestamp))}</span>`
    : `<span>${esc(shortTime(m.timestamp))}</span>`;
  return `<div class="msg ${role}${inRange ? " in-range" : ""}${evidence ? " flash-static" : ""}" id="msg-${m.idx}" data-idx="${m.idx}">
    <div class="meta"><b>${SPEAKERS[m.speaker] || role}</b>${time}<span>#${m.idx}</span></div>
    <div>${esc(m.text)}</div>
  </div>`;
}

export const loading = (text = "Загрузка…") => `<div class="empty"><span class="spinner"></span> ${esc(text)}</div>`;
export const errorBox = (e) => `<div class="card"><div class="alert bad">${esc(e.message || e)}</div></div>`;
