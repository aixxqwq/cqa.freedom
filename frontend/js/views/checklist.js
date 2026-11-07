// Экран 4. Редактор чек-листа: критерии, веса, типы, шкалы, N/A, секции, правила итога.
import { api } from "../api.js";
import { DEFAULT_GROUP } from "../config.js";
import { esc, errorBox, loading, prefs, toast } from "../ui.js";

const TYPES = { binary: "Да / Нет", scale: "Шкала", flag: "Флаг без баллов" };
let pollTimer = null;

export async function renderChecklist(root) {
  root.innerHTML = loading();
  let all;
  try { all = await api.checklists(); } catch (e) { root.innerHTML = errorBox(e); return; }
  if (!all.length) { root.innerHTML = errorBox("Нет ни одного чек-листа"); return; }

  let currentId = prefs.get("checklist", "default");
  let cl = structuredClone(all.find((c) => c.id === currentId) || all[0]);
  currentId = cl.id;
  let dirty = false;
  let original = JSON.stringify(cl);  // как чек-лист сохранён в базе
  let versions = [];

  function draw() {
    const weightSum = cl.criteria.filter((c) => c.type !== "flag").reduce((s, c) => s + Number(c.weight || 0), 0);
    root.innerHTML = `
      <div class="page-head">
        <div>
          <p class="eyebrow">Настройка оценки</p>
          <h1>Чек-лист</h1>
          <div class="muted">Изменения применяются к новым оценкам. Старые оценки хранят свою версию чек-листа.</div>
        </div>
        <div class="row">
          <select id="pick">${all.map((c) => `<option value="${esc(c.id)}" ${c.id === currentId ? "selected" : ""}>${esc(c.name)} · v${c.version}</option>`).join("")}</select>
        </div>
      </div>

      <div class="card">
        <div class="row" style="align-items:flex-end">
          <div class="field grow"><label>Название</label><input type="text" data-root="name" value="${esc(cl.name)}"></div>
          <div class="field"><label>Округление итога</label>
            <div class="row">
              <select data-root="rounding.decimals">${[0, 1, 2].map((d) => `<option value="${d}" ${cl.rounding.decimals === d ? "selected" : ""}>${d} знака</option>`).join("")}</select>
              <select data-root="rounding.mode">${[["half_up", "математическое"], ["bankers", "банковское"], ["floor", "вниз"], ["ceil", "вверх"]]
                .map(([v, t]) => `<option value="${v}" ${cl.rounding.mode === v ? "selected" : ""}>${t}</option>`).join("")}</select>
            </div>
          </div>
          <div class="field"><label>Грубая ошибка влияет на итог</label>
            <select data-root="critical_policy">${[["flag", "только отметка"], ["zero_section", "обнуляет секцию"], ["zero_total", "обнуляет итог"]]
              .map(([v, t]) => `<option value="${v}" ${cl.critical_policy === v ? "selected" : ""}>${t}</option>`).join("")}</select>
          </div>
        </div>
        <div class="field" style="margin-top:12px"><label>Общие правила оценки (передаются ИИ)</label>
          <textarea data-root="guidance" rows="4">${esc(cl.guidance || "")}</textarea></div>
        <div class="field" style="margin-top:12px"><label>Секции</label>
          <div class="row">
            ${cl.sections.map((s, i) => `<div class="row" style="gap:4px">
              <input type="text" data-sec="${i}" data-k="name" value="${esc(s.name)}" style="width:220px" title="id: ${esc(s.id)}">
              <button class="btn ghost small danger" data-del-sec="${i}" title="Удалить секцию">✕</button></div>`).join("")}
            <button class="btn small" id="add-sec">+ Секция</button>
          </div>
        </div>
      </div>

      <div class="row" style="justify-content:space-between;margin:20px 0 10px">
        <h2 style="margin:0">Критерии (${cl.criteria.length})</h2>
        <div class="small">Сумма весов: <span class="weight-sum ${weightSum === 100 ? "" : "bad"}">${weightSum}</span>
          ${weightSum === 100 ? "" : `<span class="muted"> — не 100, итог всё равно нормируется к 100</span>`}</div>
      </div>
      <div id="criteria">${cl.criteria.map(criterionEditor).join("")}</div>
      <div class="row" style="margin-top:12px"><button class="btn" id="add">+ Добавить критерий</button></div>

      <div class="card save-bar" style="margin-top:20px;position:sticky;bottom:12px">
        <div class="row">
          <button class="btn primary" id="save"></button>
          <button class="btn" id="save-new">Сохранить как новый чек-лист</button>
          <button class="btn ghost" id="reset">Отменить изменения</button>
          <span class="grow"></span>
          <button class="btn" id="apply">Применить к набору ${esc(prefs.get("group", DEFAULT_GROUP))}</button>
          <div class="field hidden" id="apply-box" style="min-width:200px"><span class="small" id="apply-label"></span>
            <div class="progress"><div id="apply-bar" style="width:0"></div></div></div>
        </div>
        <div class="small" id="save-status" style="margin-top:8px"></div>
      </div>

      <div class="card" style="margin-top:16px">
        <h2>История версий</h2>
        <div class="small muted" style="margin-bottom:10px">Каждое сохранение — отдельная версия в базе данных. Старую версию можно открыть в редакторе и сохранить снова.</div>
        <div id="versions">${versionsHtml()}</div>
      </div>`;
    bind();
    updateBar();
  }

  function versionsHtml() {
    if (!versions.length) return `<div class="small muted">Версий пока нет — они появятся после первого сохранения.</div>`;
    return `<div class="table-wrap"><table><thead><tr><th>Версия</th><th>Сохранена</th><th>Название</th><th>Критериев</th><th></th></tr></thead><tbody>
      ${versions.map((v) => `<tr style="cursor:default">
        <td class="mono">v${v.version}${v.version === cl.version ? ` <span class="badge ok">текущая</span>` : ""}</td>
        <td>${v.saved_at ? new Date(v.saved_at).toLocaleString("ru-RU") : "—"}</td>
        <td>${esc(v.name)}</td>
        <td>${v.criteria.length}</td>
        <td>${v.version === cl.version ? "" : `<button class="btn small" data-open-ver="${v.version}">Открыть в редакторе</button>`}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  /** Кнопки и статус внизу отражают, есть ли несохранённые изменения. */
  function updateBar() {
    const save = root.querySelector("#save");
    const reset = root.querySelector("#reset");
    const status = root.querySelector("#save-status");
    if (!save) return;
    save.disabled = !dirty;
    reset.disabled = !dirty;
    save.textContent = dirty ? `Сохранить изменения (v${cl.version} → v${cl.version + 1})` : "Нет изменений";
    status.innerHTML = dirty
      ? `<span style="color:var(--warn)">● Есть несохранённые изменения</span>`
      : `<span style="color:var(--ok)">✓ Всё сохранено в базе</span> <span class="muted">· версия v${cl.version}${cl.saved_at ? ` · ${new Date(cl.saved_at).toLocaleString("ru-RU")}` : ""}</span>`;
  }

  async function loadVersions() {
    try { versions = await api.checklistVersions(currentId); } catch { versions = []; }
    const box = root.querySelector("#versions");
    if (box) { box.innerHTML = versionsHtml(); bindVersions(); }
  }

  function bindVersions() {
    root.querySelectorAll("[data-open-ver]").forEach((b) => b.onclick = () => {
      const v = versions.find((x) => x.version === Number(b.dataset.openVer));
      // Содержимое старой версии, но номер — текущий: при сохранении получится новая версия.
      cl = { ...structuredClone(v), version: cl.version, saved_at: cl.saved_at };
      touch();
      draw();
      toast(`Открыта версия v${v.version}. Нажмите «Сохранить», чтобы сделать её текущей.`);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  function criterionEditor(c, i) {
    const sectionOpts = [`<option value="">— без секции —</option>`]
      .concat(cl.sections.map((s) => `<option value="${esc(s.id)}" ${c.section === s.id ? "selected" : ""}>${esc(s.name)}</option>`)).join("");
    const levels = c.type === "scale" ? `
      <div class="levels">
        <span class="label">Ступени шкалы (верхняя даёт полный вес)</span>
        ${(c.scale?.levels || []).map((l, j) => `<div class="level-row">
          <input type="number" data-i="${i}" data-level="${j}" data-k="value" value="${esc(l.value)}">
          <input type="text" data-i="${i}" data-level="${j}" data-k="label" value="${esc(l.label)}" placeholder="Когда ставится эта ступень">
          <button class="btn ghost small danger" data-del-level="${i}:${j}">✕</button></div>`).join("")}
        <div><button class="btn small" data-add-level="${i}">+ Ступень</button></div>
      </div>` : "";
    return `<div class="crit-edit">
      <div class="row" style="justify-content:space-between;margin-bottom:8px">
        <b>${String(i + 1).padStart(2, "0")} · ${esc(c.title)}</b>
        <div class="row" style="gap:4px">
          <button class="btn ghost small" data-move="${i}:-1" ${i === 0 ? "disabled" : ""} title="Выше">↑</button>
          <button class="btn ghost small" data-move="${i}:1" ${i === cl.criteria.length - 1 ? "disabled" : ""} title="Ниже">↓</button>
          <button class="btn ghost small danger" data-del="${i}">Удалить</button>
        </div>
      </div>
      <div class="grid">
        <div class="field"><label>ID</label><input type="text" class="mono" data-i="${i}" data-k="id" value="${esc(c.id)}"></div>
        <div class="field"><label>Название</label><input type="text" data-i="${i}" data-k="title" value="${esc(c.title)}"></div>
        <div class="field"><label>Секция</label><select data-i="${i}" data-k="section">${sectionOpts}</select></div>
        <div class="field"><label>Тип ответа</label><select data-i="${i}" data-k="type" data-rerender="1">
          ${Object.entries(TYPES).map(([v, t]) => `<option value="${v}" ${c.type === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <div class="field"><label>Вес</label><input type="number" min="0" data-i="${i}" data-k="weight" value="${esc(c.weight)}" ${c.type === "flag" ? "disabled" : ""}></div>
      </div>
      <div class="field" style="margin-top:10px"><label>Формулировка — что значит «Да» / как оценивать</label>
        <textarea data-i="${i}" data-k="description" rows="2">${esc(c.description)}</textarea></div>
      ${c.type !== "flag" ? `<div class="grid2">
        <div class="field">
          <label class="check"><input type="checkbox" data-i="${i}" data-k="na_allowed" data-rerender="1" ${c.na_allowed ? "checked" : ""}> N/A разрешён</label>
          ${c.na_allowed ? `<input type="text" data-i="${i}" data-k="na_rule" value="${esc(c.na_rule)}" placeholder="Когда ставить N/A">` : ""}
        </div>
        <div class="field"><label class="check"><input type="checkbox" data-i="${i}" data-k="critical" ${c.critical ? "checked" : ""}> Критичный: «Нет» = грубая ошибка</label></div>
      </div>` : `<div class="small muted" style="margin-top:8px">Флаг не даёт баллов. «Да» = нарушение выявлено, отмечается отдельно от итога.</div>`}
      ${levels}
    </div>`;
  }

  // Изменения есть, только если чек-лист отличается от сохранённого в базе.
  function touch() { dirty = JSON.stringify(cl) !== original; updateBar(); }

  function setRoot(path, value) {
    const [a, b] = path.split(".");
    if (b) cl[a][b] = a === "rounding" && b === "decimals" ? Number(value) : value;
    else cl[a] = value;
  }

  function bind() {
    const $ = (s) => root.querySelector(s);
    $("#pick").onchange = (e) => {
      if (dirty && !confirm("Несохранённые изменения пропадут. Продолжить?")) { e.target.value = currentId; return; }
      currentId = e.target.value;
      prefs.set("checklist", currentId);
      cl = structuredClone(all.find((c) => c.id === currentId));
      original = JSON.stringify(cl);
      dirty = false;
      draw();
      loadVersions();
    };

    root.querySelectorAll("[data-root]").forEach((el) => {
      el.oninput = el.onchange = () => { setRoot(el.dataset.root, el.value); touch(); };
    });
    root.querySelectorAll("[data-sec]").forEach((el) => {
      el.oninput = () => { cl.sections[el.dataset.sec].name = el.value; touch(); };
    });
    root.querySelectorAll("[data-i][data-k]").forEach((el) => {
      const handler = () => {
        const c = cl.criteria[el.dataset.i];
        const k = el.dataset.k;
        if (el.dataset.level !== undefined) {
          c.scale.levels[el.dataset.level][k] = k === "value" ? Number(el.value) : el.value;
        } else if (el.type === "checkbox") {
          c[k] = el.checked;
        } else if (k === "weight") {
          c[k] = Number(el.value);
        } else if (k === "section") {
          c[k] = el.value || null;
        } else {
          c[k] = el.value;
        }
        if (k === "type") {
          if (c.type === "scale" && !c.scale?.levels?.length) {
            c.scale = { levels: [{ value: 10, label: "" }, { value: 5, label: "" }, { value: 0, label: "" }] };
          }
          if (c.type === "flag") { c.weight = 0; c.na_allowed = false; c.critical = true; }
          else if (!c.weight) c.weight = 10;
        }
        touch();
        if (el.dataset.rerender || k === "weight" || k === "title") {
          if (el.dataset.rerender) draw();
          else updateSum();
        }
      };
      el.oninput = handler;
      if (el.tagName === "SELECT" || el.type === "checkbox") el.onchange = handler;
    });

    root.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
      const c = cl.criteria[b.dataset.del];
      if (!confirm(`Удалить критерий «${c.title}»?`)) return;
      cl.criteria.splice(b.dataset.del, 1); touch(); draw();
    });
    root.querySelectorAll("[data-move]").forEach((b) => b.onclick = () => {
      const [i, d] = b.dataset.move.split(":").map(Number);
      const [item] = cl.criteria.splice(i, 1);
      cl.criteria.splice(i + d, 0, item); touch(); draw();
    });
    root.querySelectorAll("[data-add-level]").forEach((b) => b.onclick = () => {
      cl.criteria[b.dataset.addLevel].scale.levels.push({ value: 0, label: "" }); touch(); draw();
    });
    root.querySelectorAll("[data-del-level]").forEach((b) => b.onclick = () => {
      const [i, j] = b.dataset.delLevel.split(":").map(Number);
      cl.criteria[i].scale.levels.splice(j, 1); touch(); draw();
    });
    root.querySelectorAll("[data-del-sec]").forEach((b) => b.onclick = () => {
      const s = cl.sections[b.dataset.delSec];
      cl.sections.splice(b.dataset.delSec, 1);
      cl.criteria.forEach((c) => { if (c.section === s.id) c.section = null; });
      touch(); draw();
    });
    $("#add-sec").onclick = () => {
      cl.sections.push({ id: "s" + Date.now().toString(36), name: "Новая секция" }); touch(); draw();
    };
    $("#add").onclick = () => {
      let n = cl.criteria.length + 1;
      while (cl.criteria.some((c) => c.id === `criterion_${n}`)) n++;
      cl.criteria.push({
        id: `criterion_${n}`, title: "Новый критерий", description: "", section: cl.sections[0]?.id ?? null,
        weight: 10, type: "binary", na_allowed: false, na_rule: "", critical: false,
      });
      touch(); draw();
      root.querySelector(".crit-edit:last-child")?.scrollIntoView({ behavior: "smooth" });
    };
    $("#reset").onclick = () => {
      if (!dirty) return;
      cl = JSON.parse(original); dirty = false; draw();
      toast("Изменения отменены — вернулась сохранённая версия v" + cl.version);
    };

    $("#save").onclick = async () => {
      if (!dirty) return;
      const btn = $("#save");
      btn.disabled = true;
      btn.innerHTML = `<span class="spinner"></span> Сохраняем…`;
      try {
        const saved = await api.updateChecklist(cl);
        toast(`Сохранено в базе: «${saved.name}» v${saved.version}`, "ok");
        await afterSave(saved);
      } catch (e) {
        toast("Не сохранено: " + e.message, "bad");
        updateBar();
      }
    };
    bindVersions();
    $("#save-new").onclick = async () => {
      const name = prompt("Название нового чек-листа", cl.name + " (копия)");
      if (!name) return;
      const copy = structuredClone(cl);
      delete copy.id; delete copy.version;
      copy.name = name;
      try {
        const saved = await api.createChecklist(copy);
        toast(`Создан чек-лист «${saved.name}»`, "ok");
        await afterSave(saved);
      } catch (e) { toast("Не сохранено: " + e.message, "bad"); }
    };
    $("#apply").onclick = async () => {
      if (dirty) return toast("Сначала сохраните изменения", "bad");
      const group = prefs.get("group", DEFAULT_GROUP);
      if (!confirm(`Оценить все диалоги набора ${group} чек-листом «${cl.name}» v${cl.version}?`)) return;
      try {
        const job = await api.batch(group, cl.id, false);
        watch(job.id);
      } catch (e) { toast(e.message, "bad"); }
    };
  }

  function updateSum() {
    const sum = cl.criteria.filter((c) => c.type !== "flag").reduce((s, c) => s + Number(c.weight || 0), 0);
    const el = root.querySelector(".weight-sum");
    el.textContent = sum;
    el.classList.toggle("bad", sum !== 100);
  }

  async function afterSave(saved) {
    all = await api.checklists();
    currentId = saved.id;
    prefs.set("checklist", currentId);
    cl = structuredClone(saved);
    original = JSON.stringify(cl);
    dirty = false;
    draw();
    loadVersions();
  }

  function watch(jobId) {
    const box = root.querySelector("#apply-box");
    box.classList.remove("hidden");
    root.querySelector("#apply").disabled = true;
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const job = await api.batchStatus(jobId);
        root.querySelector("#apply-bar").style.width = `${(job.done / job.total) * 100}%`;
        root.querySelector("#apply-label").textContent = `Оценено ${job.done} из ${job.total}`;
        if (job.status === "finished") {
          clearInterval(pollTimer);
          root.querySelector("#apply").disabled = false;
          toast(`Чек-лист применён к ${job.done - job.failed.length} диалогам${job.failed.length ? `, ошибок: ${job.failed.length}` : ""}`, job.failed.length ? "bad" : "ok");
        }
      } catch (e) { clearInterval(pollTimer); toast(e.message, "bad"); }
    }, 1500);
  }

  window.onbeforeunload = () => (dirty ? true : undefined);
  draw();
  loadVersions();
  return () => { clearInterval(pollTimer); window.onbeforeunload = null; };
}
