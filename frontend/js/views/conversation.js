// Экран 2. Карточка диалога: транскрипт + результат оценки + подача апелляции.
import { api } from "../api.js";
import { refreshAppealCount } from "../app.js";
import { DEFAULT_GROUP } from "../config.js";
import {
  CHANNELS, answerBadge, answerOptions, answerText, esc, errorBox, fmt, loading, mediaUrl, messageHtml, parseAnswer, prefs, scoreClass,
  secondsFrom, toast,
} from "../ui.js";

export async function renderConversation(root, { id, params }) {
  root.innerHTML = loading("Открываем диалог…");
  const group = params.get("group") || prefs.get("group", DEFAULT_GROUP);

  let conv, evs, ev = null, appeals = [], checklists = [], gt = null;
  try {
    let gts;
    [conv, evs, checklists, gts] = await Promise.all([
      api.conversation(id, group), api.evaluations(id), api.checklists(), api.groundTruth().catch(() => []),
    ]);
    gt = gts.find((g) => g.conversation_id === id) || null;
    const evId = params.get("ev") || evs[0]?.id;
    if (evId) {
      [ev, appeals] = await Promise.all([api.evaluation(evId), api.appeals(null, evId)]);
    }
  } catch (e) {
    root.innerHTML = `<p><a href="#/">← К списку</a></p>` + errorBox(e);
    return;
  }

  const firstTs = conv.transcript[0]?.timestamp;
  const isCall = Boolean(conv.audio_url);
  const currentCl = ev?.checklist_id || prefs.get("checklist", "default");

  root.innerHTML = `
    <p style="margin:0 0 12px"><a href="#/">← К списку диалогов</a></p>
    <div class="page-head">
      <div>
        <h1 class="mono">${esc(conv.conversation_id)}</h1>
        <div class="row small muted">
          <span>${isCall ? "🎧" : "💬"} ${esc(CHANNELS[conv.channel] || conv.channel)}</span>
          <span class="badge na">${esc(conv.language)}</span>
          <span>${conv.transcript.length} реплик</span>
        </div>
      </div>
      <div class="row">
        <select id="cl">${checklists.map((c) =>
          `<option value="${esc(c.id)}" ${c.id === currentCl ? "selected" : ""}>${esc(c.name)} · v${c.version}</option>`).join("")}</select>
        <button class="btn primary" id="run">${ev ? "Оценить заново" : "Оценить диалог"}</button>
      </div>
    </div>
    <div class="split">
      <section class="card transcript-card">
        <div class="transcript-head">
          <h2 style="margin:0">Транскрипт</h2>
          <div class="small muted">Реплики бота и системные события — контекст, оператор за них не оценивается.</div>
          ${isCall ? `<audio id="audio" controls preload="none" src="${esc(api.audioUrl(conv.conversation_id, group))}" style="width:100%;margin-top:10px"></audio>
            <div class="small muted">Нажмите ▶ у реплики, чтобы прослушать с этого места. <a href="${esc(mediaUrl(conv.audio_url))}" target="_blank" rel="noopener">Открыть запись</a></div>` : ""}
        </div>
        <div class="transcript" id="transcript">
          ${conv.transcript.map((m) => messageHtml(m, { firstTs, audio: isCall })).join("")}
        </div>
      </section>
      <section id="result">${ev ? resultHtml(ev, evs, appeals, gt) : emptyResult()}</section>
    </div>`;

  const $ = (s) => root.querySelector(s);
  const audio = $("#audio");

  // ---- переход к реплике / фрагменту ----
  function seekTo(idx) {
    if (!audio) return;
    const sec = secondsFrom(conv.transcript[idx]?.timestamp, firstTs);
    if (sec !== null) { audio.currentTime = sec; audio.play().catch(() => {}); }
  }
  function flash(indices, { seek = false } = {}) {
    const els = indices.map((i) => root.querySelector(`#msg-${i}`)).filter(Boolean);
    if (!els.length) return;
    els[0].scrollIntoView({ behavior: "smooth", block: "center" });
    els.forEach((el) => el.classList.add("flash"));
    setTimeout(() => els.forEach((el) => el.classList.remove("flash")), 2500);
    if (seek) seekTo(indices[0]);
  }
  // Слушатель на .split: элемент пересоздаётся при каждой отрисовке, обработчики не копятся.
  $(".split").addEventListener("click", (e) => {
    const t = e.target.closest("[data-go],[data-range],[data-seek]");
    if (!t) return;
    if (t.dataset.seek !== undefined && audio) {
      audio.currentTime = Number(t.dataset.seek);
      audio.play().catch(() => {});
    } else if (t.dataset.go !== undefined) {
      flash([Number(t.dataset.go)], { seek: isCall });
    } else if (t.dataset.range) {
      const [a, b] = t.dataset.range.split("-").map(Number);
      root.querySelectorAll(".msg.in-range").forEach((el) => el.classList.remove("in-range"));
      const idx = [];
      for (let i = a; i <= b; i++) { idx.push(i); root.querySelector(`#msg-${i}`)?.classList.add("in-range"); }
      flash(idx, { seek: isCall });
    }
  });

  // ---- запуск оценки ----
  $("#run").onclick = async () => {
    const btn = $("#run");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Оцениваем… (10–60 сек)`;
    prefs.set("checklist", $("#cl").value);
    try {
      const res = await api.evaluate(conv.conversation_id, $("#cl").value, group);
      toast(`Готово: ${fmt(res.final.total)} баллов`, "ok");
      location.hash = `#/c/${encodeURIComponent(conv.conversation_id)}?ev=${res.id}`;
    } catch (e) {
      toast(e.message, "bad");
      btn.disabled = false;
      btn.textContent = ev ? "Оценить заново" : "Оценить диалог";
    }
  };

  if (!ev) return;

  // ---- история оценок ----
  $("#history")?.addEventListener("change", (e) => {
    location.hash = `#/c/${encodeURIComponent(conv.conversation_id)}?ev=${e.target.value}`;
  });

  // ---- апелляция ----
  root.querySelectorAll("[data-dispute]").forEach((btn) => {
    btn.onclick = () => {
      const form = root.querySelector(`#appeal-${btn.dataset.dispute}`);
      form.classList.toggle("hidden");
      form.querySelector("textarea").focus();
    };
  });
  // ---- эталонная разметка ----
  $("#gt-save")?.addEventListener("click", async () => {
    const crit = Object.fromEntries(ev.checklist.criteria.map((c) => [c.id, c]));
    const answers = {};
    root.querySelectorAll("[data-gt]").forEach((s) => {
      if (s.value !== "") answers[s.dataset.gt] = parseAnswer(s.value, crit[s.dataset.gt]);
    });
    if (!Object.keys(answers).length) return toast("Выберите ответы хотя бы по одному критерию", "bad");
    const author = $("#gt-author").value.trim();
    prefs.set("gtAuthor", author);
    try {
      await api.saveGroundTruth(conv.conversation_id, answers, author);
      toast(`Эталон сохранён: ${Object.keys(answers).length} критериев`, "ok");
      renderConversation(root, { id, params: new URLSearchParams({ ev: ev.id, group }) });
    } catch (e) { toast(e.message, "bad"); }
  });
  $("#gt-delete")?.addEventListener("click", async () => {
    await api.deleteGroundTruth(conv.conversation_id);
    toast("Эталон удалён");
    renderConversation(root, { id, params: new URLSearchParams({ ev: ev.id, group }) });
  });

  root.querySelectorAll("[data-send-appeal]").forEach((btn) => {
    btn.onclick = async () => {
      const cid = btn.dataset.sendAppeal;
      const text = root.querySelector(`#appeal-${cid} textarea`).value.trim();
      if (!text) { toast("Добавьте комментарий: почему оценка неверна", "bad"); return; }
      btn.disabled = true;
      try {
        await api.createAppeal(ev.id, cid, text);
        toast("Апелляция отправлена проверяющему", "ok");
        refreshAppealCount();
        renderConversation(root, { id, params: new URLSearchParams({ ev: ev.id, group }) });
      } catch (e) { toast(e.message, "bad"); btn.disabled = false; }
    };
  });
}

function emptyResult() {
  return `<div class="card empty">
    <h2>Диалог ещё не оценён</h2>
    <p>Выберите чек-лист и нажмите «Оценить диалог». ИИ проверит каждый критерий,<br>а баллы, веса и N/A посчитает система.</p>
  </div>`;
}

/** Делает [3] и #3 в тексте объяснения кликабельными ссылками на реплики. */
function linkify(text) {
  return esc(text).replace(/(\[|#)(\d+)\]?/g, (m, _p, n) => `<button class="chip" data-go="${n}">#${n}</button>`);
}

function resultHtml(ev, evs, appeals, gt) {
  const cl = ev.checklist;
  const crit = Object.fromEntries(cl.criteria.map((c) => [c.id, c]));
  const fin = ev.final;
  const changed = ev.original.total !== fin.total;

  const sections = fin.sections.filter((s) => s.score !== null).map((s) => `
    <div class="section-score">
      <div class="small muted">${esc(s.name)}</div>
      <div class="score ${scoreClass(s.score)}">${fmt(s.score)}${s.critical_error ? ` <span class="badge bad">⚠</span>` : ""}</div>
      <div class="bar"><div style="width:${s.score}%"></div></div>
    </div>`).join("");

  const critical = fin.critical_errors.map((cid) => {
    const v = ev.view.find((x) => x.criterion_id === cid);
    return `<b>${esc(crit[cid]?.title)}</b>: ${linkify(v?.explanation || "")}`;
  });

  // группируем критерии по секциям в порядке чек-листа
  const groups = [...cl.sections.map((s) => ({ ...s, items: [] })), { id: null, name: "Без секции", items: [] }];
  for (const v of ev.view) (groups.find((g) => g.id === v.section) || groups.at(-1)).items.push(v);

  return `
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <div class="total-box">
          <div>
            <div class="small muted">Итоговый балл</div>
            <div class="total-num score ${scoreClass(fin.total)}">${fin.total === null ? "—" : fmt(fin.total)}<small> / 100</small></div>
          </div>
          ${changed ? `<div><div class="small muted">Исходная оценка</div><div class="score" style="font-size:20px">${fmt(ev.original.total)}</div><div class="small muted">изменена по апелляции</div></div>` : ""}
        </div>
        <div class="field">
          <label>История оценок</label>
          <select id="history">${evs.map((e) => `<option value="${esc(e.id)}" ${e.id === ev.id ? "selected" : ""}>
            ${new Date(e.created_at).toLocaleString("ru-RU")} · ${fmt(e.total)} · v${e.checklist_version}</option>`).join("")}</select>
        </div>
      </div>
      ${fin.total === null ? `<div class="alert warn">Итог не рассчитывается: нет применимых критериев с весом.</div>` : ""}
      ${critical.length ? `<div class="alert bad">⚠ <b>Грубая ошибка.</b> ${critical.join("<br>")}</div>` : ""}
      <div class="sections">${sections}</div>
      <div class="small muted" style="margin-top:12px">
        Чек-лист «${esc(cl.name)}» v${ev.checklist_version} · модель ${esc(ev.model)}
        ${ev.votes > 1 ? ` · голосование ${ev.votes} оценок` : ""}
        ${ev.usage?.cost_usd ? ` · стоимость оценки $${ev.usage.cost_usd}` : ""} · ${new Date(ev.created_at).toLocaleString("ru-RU")}
      </div>
      ${disputedSummary(ev)}
      ${processHtml(ev)}
    </div>
    <div class="card">
      <h2>Результат по критериям</h2>
      ${groups.filter((g) => g.items.length).map((g) => `
        <div class="section-title">${esc(g.name)}</div>
        ${g.items.map((v) => criterionHtml(v, crit[v.criterion_id], appeals.filter((a) => a.criterion_id === v.criterion_id), gt)).join("")}
      `).join("")}
    </div>
    ${groundTruthHtml(ev, gt)}`;
}

/** Сколько критериев ИИ оценил неуверенно (голоса разошлись). */
function disputedSummary(ev) {
  const shaky = ev.view.filter((v) => v.agreement && v.agreement.split("/")[0] !== v.agreement.split("/")[1]);
  if (!shaky.length || (ev.votes || 1) < 2) return "";
  return `<div class="alert warn">Спорных критериев: ${shaky.length} — голоса ИИ разошлись
    (${shaky.map((v) => esc(v.title)).join(", ")}). Их стоит проверить вручную.</div>`;
}

/** Панель «Как получена оценка»: голоса, время, токены и сырые ответы модели. */
function processHtml(ev) {
  const runs = ev.runs || [];
  if (!runs.length) return "";
  const crit = ev.checklist.criteria;
  return `<details class="process" style="margin-top:12px">
    <summary>Как получена оценка</summary>
    <div class="small" style="margin-top:10px">
      Диалог оценён <b>${runs.length}</b> раз${runs.length > 1 ? "а независимо, параллельно" : ""} моделью <span class="mono">${esc(ev.model)}</span>.
      По каждому критерию берётся ответ большинства, для шкалы без большинства — медиана. Баллы, веса, N/A и округление
      считает код по правилам чек-листа, а не ИИ.
      ${ev.duration_s != null ? `Время: ${ev.duration_s} с.` : ""}
      Токены: ${ev.usage.input_tokens} входных, ${ev.usage.output_tokens} выходных.
    </div>
    <div class="table-wrap" style="margin-top:10px"><table>
      <thead><tr><th>Критерий</th>${runs.map((_, i) => `<th>Голос ${i + 1}</th>`).join("")}<th>Итог</th></tr></thead>
      <tbody>${crit.map((c) => {
        const v = ev.items.find((x) => x.criterion_id === c.id);
        return `<tr style="cursor:default"><td>${esc(c.title)}</td>${runs.map((r) => `<td class="mono">${esc(r.answers[c.id])}</td>`).join("")}
          <td class="mono"><b>${esc(v?.answer)}</b> <span class="muted">${esc(v?.agreement || "")}</span></td></tr>`;
      }).join("")}</tbody></table></div>
    ${runs.map((r, i) => `<details style="margin-top:8px"><summary class="small">Сырой ответ модели, голос ${i + 1}
      ${r.duration_s != null ? `· ${r.duration_s} с` : ""} · $${r.usage?.cost_usd ?? 0}</summary>
      <pre class="raw">${esc(prettyJson(r.raw))}</pre></details>`).join("")}
  </details>`;
}

function prettyJson(s) {
  try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s || ""; }
}

/** Эталонная разметка: как оценил бы человек. Нужна для страницы «Точность». */
function groundTruthHtml(ev, gt) {
  const options = (c) => {
    const opts = answerOptions(c);
    const cur = gt?.answers?.[c.id];
    return `<option value="">— не размечено —</option>` + opts.map(([v, t]) =>
      `<option value="${esc(v)}" ${cur !== undefined && String(cur) === v ? "selected" : ""}>${esc(t.length > 70 ? t.slice(0, 70) + "…" : t)}</option>`).join("");
  };
  return `<details class="card gt-card" ${gt ? "open" : ""}>
    <summary><b>Эталонная разметка</b> <span class="small muted">— как оценил бы человек-проверяющий (для проверки точности ИИ)</span>
      ${gt ? `<span class="badge ok">размечено</span>` : ""}</summary>
    <div class="small muted" style="margin:10px 0">Оценивайте сами по PDF-чек-листу. Совпадения и расхождения с ИИ видны на странице «Точность».</div>
    <div class="gt-grid">
      ${ev.checklist.criteria.map((c) => `<div class="field"><label for="gt-${esc(c.id)}">${esc(c.title)}</label>
        <select id="gt-${esc(c.id)}" data-gt="${esc(c.id)}">${options(c)}</select></div>`).join("")}
    </div>
    <div class="row" style="margin-top:12px">
      <input type="text" id="gt-author" placeholder="Кто размечал" value="${esc(gt?.author || prefs.get("gtAuthor", ""))}" style="width:180px">
      <button class="btn primary" id="gt-save" type="button">Сохранить эталон</button>
      ${gt ? `<button class="btn ghost danger" id="gt-delete" type="button">Удалить эталон</button>` : ""}
      <a href="#/accuracy">Открыть «Точность» →</a>
    </div>
  </details>`;
}

function confidenceBadge(v) {
  if (!v.agreement) return "";
  const [k, n] = v.agreement.split("/").map(Number);
  if (n < 2) return "";
  if (k === n) return `<span class="badge na" title="Все ${n} голоса ИИ совпали">✓ ${k}/${n}</span>`;
  return `<span class="badge warn" title="Голоса ИИ: ${esc(v.votes.join(", "))}">спорно ${k}/${n}</span>`;
}

function gtBadge(v, c, gt) {
  const g = gt?.answers?.[v.criterion_id];
  if (g === undefined) return "";
  const ai = v.override ? v.override.answer : v.answer;
  return String(g) === String(ai)
    ? `<span class="badge ok" title="Совпадает с эталоном">= эталон</span>`
    : `<span class="badge bad" title="Эталон: ${esc(answerText(g, c))}">≠ эталон: ${esc(answerText(g, c))}</span>`;
}

function criterionHtml(v, c, appeals, gt) {
  const f = v.final;
  const finalAnswer = v.override ? v.override.answer : v.answer;
  const open = appeals.find((a) => a.status === "open");
  const resolved = appeals.filter((a) => a.status === "resolved");

  let points;
  if (c.type === "flag") points = `<span class="small muted">без баллов</span>`;
  else if (f.is_na) points = `<span class="small muted">вес ${fmt(c.weight)} перераспределён</span>`;
  else points = `<span class="points">${fmt(f.points)}</span><span class="muted"> / ${fmt(f.max_points)}</span>`;

  const weightNote = c.type !== "flag" && !f.is_na
    ? `<span class="small muted">вес ${fmt(c.weight)}${Math.abs(f.effective_weight - c.weight) > 0.001 ? ` → ${fmt(f.effective_weight)} после N/A` : ""}</span>`
    : "";

  const evidence = v.evidence.map((i) => `<button class="chip" data-go="${i}">Реплика #${i}</button>`).join("");
  // Проверенный участок нужен, когда действие пропущено или выполнено не полностью.
  const missed = v.answer === "no" || (c.type === "scale" && v.answer !== "na" && Number(v.answer) < c.scale.max);
  const range = v.checked_range && missed
    ? `<button class="chip" data-range="${v.checked_range.from}-${v.checked_range.to}">Проверенный участок #${v.checked_range.from}–#${v.checked_range.to}</button>`
    : "";

  return `<div class="crit" id="crit-${esc(v.criterion_id)}">
    <div class="crit-head">
      <h3>${esc(v.title)}</h3>
      ${answerBadge(finalAnswer, c)}
      ${confidenceBadge(v)}
      ${gtBadge(v, c, gt)}
      <div style="min-width:110px;text-align:right">${points}</div>
    </div>
    <div class="row small">${weightNote}${c.critical && c.type !== "flag" ? `<span class="badge bad">критичный</span>` : ""}</div>
    <div class="crit-body">${linkify(v.explanation)}</div>
    ${v.answer === "na" && v.na_reason ? `<div class="small" style="margin-top:4px"><b>Почему N/A:</b> ${esc(v.na_reason)}</div>` : ""}
    ${evidence || range ? `<div class="chips">${evidence}${range}</div>` : ""}
    ${v.override ? `<div class="override"><b>Изменено проверяющим:</b> ${esc(answerText(v.answer, c))} → ${esc(answerText(v.override.answer, c))}
        (${fmt(v.original.points)} → ${fmt(f.points)} б.). Причина: ${esc(v.override.reason)} <span class="muted">— ${esc(v.override.reviewer)}</span></div>` : ""}
    ${resolved.filter((a) => a.decision.action === "confirm").map((a) =>
      `<div class="override"><b>Апелляция отклонена, оценка подтверждена.</b> ${esc(a.decision.reason)}</div>`).join("")}
    <div class="row" style="margin-top:8px">
      ${open
        ? `<span class="badge warn">Апелляция на рассмотрении</span><span class="small muted">«${esc(open.comment)}»</span>`
        : `<button class="btn small" data-dispute="${esc(v.criterion_id)}">Оспорить</button>`}
    </div>
    <div class="appeal-form hidden" id="appeal-${esc(v.criterion_id)}">
      <label class="label">Почему оценка неверна?</label>
      <textarea placeholder="Например: оператор представился в реплике #3, ИИ это не учёл"></textarea>
      <div class="row"><button class="btn primary small" data-send-appeal="${esc(v.criterion_id)}">Отправить апелляцию</button></div>
    </div>
  </div>`;
}
