// Экран 3. Очередь проверяющего: спорная оценка, комментарий, фрагмент диалога, решение.
import { api } from "../api.js";
import { refreshAppealCount } from "../app.js";
import { answerBadge, answerOptions, answerText, esc, errorBox, fmt, loading, mediaUrl, messageHtml, parseAnswer, toast } from "../ui.js";

let tab = "open";
const evalCache = new Map();

async function criterionFor(appeal) {
  if (!evalCache.has(appeal.evaluation_id)) evalCache.set(appeal.evaluation_id, api.evaluation(appeal.evaluation_id));
  const ev = await evalCache.get(appeal.evaluation_id);
  return ev.checklist.criteria.find((c) => c.id === appeal.criterion_id);
}

export async function renderAppeals(root) {
  root.innerHTML = `
    <div class="page-head">
      <div>
        <p class="eyebrow">Проверяющий</p>
        <h1>Апелляции</h1>
        <div class="muted">Рабочее место проверяющего: изучите доказательства и подтвердите или измените оценку.</div>
      </div>
      <div class="seg" id="tabs"><button data-v="open">На рассмотрении</button><button data-v="resolved">Рассмотренные</button></div>
    </div>
    <div id="list">${loading()}</div>`;
  root.querySelectorAll("#tabs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.v === tab);
    b.onclick = () => { tab = b.dataset.v; renderAppeals(root); };
  });

  let appeals;
  try {
    appeals = await api.appeals(tab);
  } catch (e) {
    root.querySelector("#list").innerHTML = errorBox(e);
    return;
  }
  if (!appeals.length) {
    root.querySelector("#list").innerHTML = `<div class="card empty">${tab === "open" ? "Нет апелляций на рассмотрении 🎉" : "Рассмотренных апелляций пока нет"}</div>`;
    return;
  }

  evalCache.clear();
  const criteria = await Promise.all(appeals.map((a) => criterionFor(a).catch(() => null)));
  root.querySelector("#list").innerHTML = appeals.map((a, i) => appealHtml(a, criteria[i])).join("");

  // показать/скрыть выбор нового ответа
  root.querySelectorAll("input[name^=action-]").forEach((r) => {
    r.onchange = () => {
      const box = root.querySelector(`#new-${r.name.slice(7)}`);
      box.classList.toggle("hidden", r.value !== "change");
    };
  });

  root.querySelectorAll("[data-decide]").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.decide;
      const card = root.querySelector(`#ap-${id}`);
      const action = card.querySelector(`input[name=action-${id}]:checked`)?.value;
      const reason = card.querySelector("textarea").value.trim();
      const crit = criteria[appeals.findIndex((a) => a.id === id)];
      if (!action) return toast("Выберите решение", "bad");
      if (!reason) return toast("Укажите причину решения", "bad");
      let newAnswer = null;
      if (action === "change") {
        newAnswer = parseAnswer(card.querySelector("select").value, crit);
      }
      btn.disabled = true;
      try {
        const res = await api.decide(id, action, reason, newAnswer);
        toast(`Решение сохранено. Итог: ${fmt(res.total_before)} → ${fmt(res.total_after)}`, "ok");
        refreshAppealCount();
        renderAppeals(root);
      } catch (e) { toast(e.message, "bad"); btn.disabled = false; }
    };
  });
}

function appealHtml(a, c) {
  const d = a.disputed;
  const evidence = new Set(d.evidence);
  const range = d.checked_range;
  const firstTs = a.fragment[0]?.timestamp;
  const link = `#/c/${encodeURIComponent(a.conversation_id)}?ev=${encodeURIComponent(a.evaluation_id)}`;

  const decision = a.status === "resolved"
    ? `<div class="alert ${a.decision.action === "change" ? "info" : "warn"}">
         <b>${a.decision.action === "change" ? `Оценка изменена: ${esc(answerText(d.answer, c))} → ${esc(answerText(a.decision.new_answer, c))}` : "Оценка подтверждена"}</b><br>
         Причина: ${esc(a.decision.reason)} <span class="muted">— ${esc(a.decision.reviewer)}, ${new Date(a.decision.decided_at).toLocaleString("ru-RU")}</span><br>
         Итог диалога: <b>${fmt(a.total_before)} → ${fmt(a.total_after)}</b>
       </div>`
    : `<div class="field" style="margin-top:12px">
         <span class="label">Решение</span>
         <label class="check"><input type="radio" name="action-${a.id}" value="confirm"> Подтвердить оценку</label>
         <label class="check"><input type="radio" name="action-${a.id}" value="change"> Изменить оценку</label>
         <div class="hidden field" id="new-${a.id}" style="margin-top:6px">
           <label>Новый ответ</label>
           <select>${c ? answerOptions(c).map(([v, t]) => `<option value="${esc(v)}" ${String(d.answer) === v ? "disabled" : ""}>${esc(t)}</option>`).join("") : ""}</select>
         </div>
         <label style="margin-top:8px">Причина решения</label>
         <textarea placeholder="Обоснование для оператора"></textarea>
         <div class="row"><button class="btn primary" data-decide="${a.id}">Сохранить решение</button></div>
       </div>`;

  return `<div class="card" id="ap-${a.id}">
    <div class="row" style="justify-content:space-between">
      <h2 style="margin:0">${esc(a.criterion_title)}</h2>
      <a href="${link}">Открыть диалог ${esc(a.conversation_id)} →</a>
    </div>
    <div class="appeal-grid" style="margin-top:12px">
      <div>
        <dl class="kv">
          <dt>Спорная оценка</dt><dd>${answerBadge(d.answer, c)}</dd>
          <dt>Обоснование ИИ</dt><dd>${esc(d.explanation)}</dd>
          <dt>Комментарий</dt><dd><b>${esc(a.comment)}</b><div class="small muted">${esc(a.author)}, ${new Date(a.created_at).toLocaleString("ru-RU")}</div></dd>
          <dt>Итог сейчас</dt><dd class="score">${fmt(a.total_before)}</dd>
        </dl>
        ${c ? `<div class="small muted" style="margin-top:8px"><b>Критерий:</b> ${esc(c.description)}${c.na_allowed ? ` N/A — ${esc(c.na_rule)}` : ""}</div>` : ""}
        ${decision}
      </div>
      <div>
        <div class="label" style="margin-bottom:6px">Фрагмент диалога (жёлтым — доказательства)</div>
        <div class="fragment">
          ${a.fragment.map((m) => messageHtml(m, { firstTs, audio: false, inRange: range && m.idx >= range.from && m.idx <= range.to })
              .replace('class="msg', evidence.has(m.idx) ? 'style="background:var(--highlight)" class="msg' : 'class="msg')).join("")}
        </div>
        ${a.audio_url ? `<audio controls preload="none" src="${esc(api.audioUrl(a.conversation_id, a.group_id || "DEV"))}" style="width:100%;margin-top:8px"></audio>` : ""}
      </div>
    </div>
  </div>`;
}
