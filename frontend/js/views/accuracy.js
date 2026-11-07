// Экран 5. Точность: сравнение оценок ИИ с эталонной разметкой людей (Ground Truth).
import { api } from "../api.js";
import { DEFAULT_GROUP } from "../config.js";
import { esc, errorBox, fmt, loading, prefs, toast } from "../ui.js";

const pctClass = (p) => (p === null ? "" : p >= 85 ? "hi" : p >= 65 ? "mid" : "lo");
const barColor = (p) => (p >= 85 ? "var(--ok)" : p >= 65 ? "var(--warn)" : "var(--bad)");

export async function renderAccuracy(root) {
  root.innerHTML = loading("Считаем точность…");
  let acc, cl;
  try {
    [acc, cl] = await Promise.all([api.accuracy(), api.checklist("default")]);
  } catch (e) { root.innerHTML = errorBox(e); return; }

  const g = acc.gross_errors;
  root.innerHTML = `
    <div class="page-head">
      <div>
        <p class="eyebrow">Качество ИИ</p>
        <h1>Точность</h1>
        <div class="muted">Оценки ИИ против эталонной разметки людей — так же, как организаторы сравнят систему с Ground Truth.</div>
      </div>
      <div class="row">
        <button class="btn" id="template" type="button">Скачать шаблон CSV</button>
        <label class="btn" for="import-file">Импорт эталона (CSV / JSON)</label>
        <input type="file" id="import-file" accept=".csv,.json,.txt" class="hidden">
      </div>
    </div>

    <div class="stats">
      <div class="stat"><div class="v">${acc.labeled}</div><div class="k">диалогов размечено</div></div>
      <div class="stat"><div class="v">${acc.compared_dialogs}</div><div class="k">из них оценены ИИ</div></div>
      <div class="stat"><div class="v score ${pctClass(acc.criterion_accuracy)}">${acc.criterion_accuracy === null ? "—" : fmt(acc.criterion_accuracy, 1) + "%"}</div><div class="k">совпадение ответов по критериям</div></div>
      <div class="stat"><div class="v">${acc.total_mae === null ? "—" : fmt(acc.total_mae)}</div><div class="k">средняя ошибка итога, баллов</div></div>
      <div class="stat"><div class="v">${g.tp}/${g.tp + g.fn}</div><div class="k">грубых ошибок найдено${g.fp ? ` · ложных: ${g.fp}` : ""}</div></div>
    </div>

    ${acc.labeled === 0 ? `<div class="card empty">
      <h2>Эталона пока нет</h2>
      <p>Откройте диалог и заполните блок «Эталонная разметка» внизу карточки — или скачайте шаблон CSV,
      разметьте диалоги в Excel по PDF-чек-листу и импортируйте файл.</p>
    </div>` : `
    <div class="card">
      <h2>По критериям</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Критерий</th><th>Совпало</th><th>Точность</th><th></th><th>Соседняя ступень</th></tr></thead>
        <tbody>${acc.per_criterion.map((p) => `<tr style="cursor:default">
          <td>${esc(p.title)}</td>
          <td>${p.match} из ${p.total}</td>
          <td class="score ${pctClass(p.accuracy)}">${p.accuracy === null ? "—" : fmt(p.accuracy, 1) + "%"}</td>
          <td>${p.accuracy === null ? "" : `<div class="acc-bar"><div style="width:${p.accuracy}%;background:${barColor(p.accuracy)}"></div></div>`}</td>
          <td class="muted">${p.close ? `${p.close} промах на 1 ступень` : ""}</td>
        </tr>`).join("")}</tbody>
      </table></div>
    </div>

    <div class="card">
      <h2>По диалогам</h2>
      <div class="small muted" style="margin-bottom:10px">Сверху — диалоги с наибольшим числом расхождений. Клик открывает карточку.</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Диалог</th><th>Итог ИИ</th><th>Итог эталона</th><th>Совпало</th><th>Расхождения (эталон → ИИ)</th></tr></thead>
        <tbody>${acc.dialogs.map((d) => d.evaluated ? `<tr data-id="${esc(d.conversation_id)}" data-ev="${esc(d.evaluation_id)}">
          <td class="mono">${esc(d.conversation_id)}</td>
          <td>${fmt(d.ai_total)}</td>
          <td>${d.gt_total === null ? `<span class="muted">не все критерии</span>` : fmt(d.gt_total)}</td>
          <td>${d.matched} из ${d.compared}</td>
          <td style="white-space:normal">${d.mismatches.length ? d.mismatches.map((m) =>
            `<span class="badge bad" style="margin:2px">${esc(m.title)}: ${esc(m.ground_truth)} → ${esc(m.ai)}</span>`).join("") : `<span class="badge ok">всё совпало</span>`}</td>
        </tr>` : `<tr data-id="${esc(d.conversation_id)}"><td class="mono">${esc(d.conversation_id)}</td>
          <td colspan="4" class="muted">ИИ ещё не оценивал — откройте диалог и нажмите «Оценить»</td></tr>`).join("")}</tbody>
      </table></div>
    </div>`}`;

  root.querySelectorAll("tr[data-id]").forEach((tr) => {
    tr.onclick = () => {
      const ev = tr.dataset.ev ? `?ev=${encodeURIComponent(tr.dataset.ev)}` : "";
      location.hash = `#/c/${encodeURIComponent(tr.dataset.id)}${ev}`;
    };
  });

  const ids = cl.criteria.map((c) => c.id);

  // ---- шаблон для разметки в Excel ----
  root.querySelector("#template").onclick = async () => {
    try {
      const convs = await api.conversations(prefs.get("group", DEFAULT_GROUP));
      const legend = cl.criteria.map((c) => {
        const vals = c.type === "scale" ? c.scale.levels.map((l) => l.value).join("/") : "yes/no";
        return `${c.id}=${c.title} (${vals}${c.na_allowed ? "/na" : ""})`;
      }).join("; ");
      const rows = [["conversation_id", ...ids], ...convs.map((c) => [c.conversation_id, ...ids.map(() => "")])];
      const csv = "﻿" + `# ${legend}\n` + rows.map((r) => r.join(";")).join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      a.download = "ground_truth_template.csv";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { toast(e.message, "bad"); }
  };

  // ---- импорт эталона ----
  root.querySelector("#import-file").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const items = parseGroundTruth(await file.text(), ids);
      if (!items.length) throw new Error("В файле не найдено ни одной размеченной строки");
      const res = await api.importGroundTruth(items);
      toast(`Импортировано: ${res.saved}${res.errors.length ? ` · ошибок: ${res.errors.length} (${res.errors[0]})` : ""}`, res.errors.length ? "bad" : "ok");
      renderAccuracy(root);
    } catch (err) { toast(err.message, "bad"); }
    e.target.value = "";
  };
}

/** CSV (; или ,) со столбцами conversation_id + id критериев, либо JSON
 *  [{conversation_id, answers:{...}}] или {conversation_id: {criterion: answer}}. */
function parseGroundTruth(text, ids) {
  const t = text.replace(/^﻿/, "").trim();
  if (t.startsWith("[") || t.startsWith("{")) {
    const data = JSON.parse(t);
    if (Array.isArray(data)) return data.map((d) => ({ conversation_id: d.conversation_id, answers: d.answers || d }));
    return Object.entries(data).map(([conversation_id, answers]) => ({ conversation_id, answers }));
  }
  const lines = t.split(/\r?\n/).filter((l) => l.trim() && !l.startsWith("#"));
  const sep = (lines[0].match(/;/g) || []).length >= (lines[0].match(/,/g) || []).length ? ";" : ",";
  const head = lines[0].split(sep).map((h) => h.trim());
  if (head[0] !== "conversation_id") throw new Error("Первый столбец должен называться conversation_id");
  return lines.slice(1).map((l) => {
    const cells = l.split(sep).map((c) => c.trim());
    const answers = {};
    head.slice(1).forEach((h, i) => {
      const v = (cells[i + 1] || "").toLowerCase().replace(",", ".");
      if (!v || !ids.includes(h)) return;
      answers[h] = ({ "да": "yes", "нет": "no", "n/a": "na", "н/д": "na" })[v] || v;
    });
    return { conversation_id: cells[0], answers };
  }).filter((r) => r.conversation_id && Object.keys(r.answers).length);
}
