// Экран 1. Список диалогов + пакетная оценка группы.
import { api } from "../api.js";
import { DEFAULT_GROUP } from "../config.js";
import { CHANNELS, esc, errorBox, fmt, loading, prefs, scoreClass, toast } from "../ui.js";

const filters = { lang: "all", channel: "all", status: "all", search: "" };
let pollTimer = null;

export async function renderList(root) {
  const group = prefs.get("group", DEFAULT_GROUP);
  root.innerHTML = `
    <div class="page-head">
      <div>
        <p class="eyebrow">Контроль качества</p>
        <h1>Диалоги</h1>
        <div class="muted">Выберите диалог, чтобы открыть транскрипт и результат оценки.</div>
      </div>
      <button class="btn" id="open-upload" type="button">＋ Загрузить свой диалог</button>
    </div>
    <div class="card hidden" id="upload-card" style="margin-bottom:16px">
      <h2>Свой диалог</h2>
      <div class="small muted" style="margin-bottom:12px">Вставьте транскрипт: одна реплика на строку, в начале роль. Время в скобках — по желанию.
        Диалог попадёт в набор <span class="mono">UPLOAD</span> и оценивается так же, как диалоги организаторов.</div>
      <div class="row" style="align-items:flex-end">
        <div class="field"><label for="up-id">ID (необязательно)</label><input type="text" id="up-id" placeholder="my-kz-call-01" style="width:180px"></div>
        <div class="field"><label for="up-lang">Язык</label><select id="up-lang"><option>RU</option><option>KZ</option><option>EN</option></select></div>
        <div class="field"><label for="up-channel">Канал</label><select id="up-channel"><option value="agent chat">Чат</option><option value="voice call">Звонок</option></select></div>
        <div class="field grow"><label for="up-audio">Запись звонка (mp3, wav, m4a, ogg — необязательно)</label><input type="file" id="up-audio" accept=".mp3,.wav,.ogg,.m4a,.webm,.aac,audio/*"></div>
      </div>
      <div class="field" style="margin-top:12px"><label for="up-text">Транскрипт</label>
        <textarea id="up-text" rows="9" placeholder="[00:00] Оператор: Здравствуйте! Freedom Broker, меня зовут Алина. Чем могу помочь?
[00:06] Клиент: Хочу пополнить счёт с карты.
[00:15] Оператор: Откройте в приложении раздел «Пополнение»…
Бот: …   Система: …   (роли: Оператор, Клиент, Бот, Система или operator, client, bot, system)"></textarea></div>
      <div class="row" style="margin-top:12px">
        <button class="btn primary" id="up-eval" type="button">Загрузить и оценить</button>
        <button class="btn" id="up-only" type="button">Только загрузить</button>
        <button class="btn ghost" id="up-cancel" type="button">Отмена</button>
      </div>
    </div>
    <div class="card" style="margin-bottom:16px">
      <div class="row">
        <div class="field"><label>Набор данных (group_id)</label>
          <div class="row"><input type="text" id="group" value="${esc(group)}" style="width:140px">
          <button class="btn" id="load">Загрузить</button></div>
        </div>
        <div class="field"><label>Чек-лист</label><select id="checklist"></select></div>
        <div class="field"><label>&nbsp;</label>
          <div class="row">
            <button class="btn primary" id="batch">Оценить все</button>
            <label class="check small"><input type="checkbox" id="only-missing" checked> только неоценённые</label>
          </div>
        </div>
        <div class="field grow hidden" id="batch-box"><label id="batch-label">Оценка…</label>
          <div class="progress"><div id="batch-bar" style="width:0"></div></div>
        </div>
      </div>
    </div>
    <div id="stats" class="stats"></div>
    <div class="card">
      <div class="row" style="margin-bottom:12px">
        <div class="seg" data-filter="lang">
          <button data-v="all">Все языки</button><button data-v="RU">RU</button><button data-v="KZ">KZ</button><button data-v="EN">EN</button>
        </div>
        <div class="seg" data-filter="channel">
          <button data-v="all">Все каналы</button><button data-v="agent chat">Чаты</button><button data-v="voice call">Звонки</button>
        </div>
        <div class="seg" data-filter="status">
          <button data-v="all">Все</button><button data-v="done">Оценены</button><button data-v="todo">Не оценены</button><button data-v="critical">Грубые ошибки</button><button data-v="appeal">С апелляцией</button>
        </div>
        <input type="text" id="search" class="grow" placeholder="Поиск по ID диалога" value="${esc(filters.search)}">
      </div>
      <div id="table">${loading()}</div>
    </div>`;

  const $ = (s) => root.querySelector(s);
  let rows = [];

  // чек-листы
  try {
    const cls = await api.checklists();
    const current = prefs.get("checklist", "default");
    $("#checklist").innerHTML = cls
      .map((c) => `<option value="${esc(c.id)}" ${c.id === current ? "selected" : ""}>${esc(c.name)} · v${c.version}</option>`)
      .join("");
  } catch (e) { toast(e.message, "bad"); }
  $("#checklist").onchange = (e) => prefs.set("checklist", e.target.value);

  async function load(refresh = false) {
    const g = $("#group").value.trim() || DEFAULT_GROUP;
    prefs.set("group", g);
    $("#table").innerHTML = loading("Загружаем диалоги…");
    try {
      rows = await api.conversations(g, refresh);
      draw();
    } catch (e) {
      $("#table").innerHTML = errorBox(e);
      $("#stats").innerHTML = "";
    }
  }

  function draw() {
    const scored = rows.filter((r) => r.total !== null);
    const avg = scored.length ? scored.reduce((s, r) => s + r.total, 0) / scored.length : null;
    $("#stats").innerHTML = `
      <div class="stat"><div class="v">${rows.length}</div><div class="k">диалогов в наборе</div></div>
      <div class="stat"><div class="v">${rows.filter((r) => r.evaluation_id).length}</div><div class="k">оценено</div></div>
      <div class="stat"><div class="v score ${scoreClass(avg)}">${fmt(avg)}</div><div class="k">средний балл</div></div>
      <div class="stat"><div class="v" style="color:var(--bad)">${rows.filter((r) => r.critical).length}</div><div class="k">с грубыми ошибками</div></div>
      <div class="stat"><div class="v">${rows.reduce((s, r) => s + r.open_appeals, 0)}</div><div class="k">открытых апелляций</div></div>`;

    const s = filters.search.toLowerCase();
    const list = rows.filter((r) =>
      (filters.lang === "all" || r.language === filters.lang) &&
      (filters.channel === "all" || r.channel === filters.channel) &&
      (filters.status === "all" ||
        (filters.status === "done" && r.evaluation_id) ||
        (filters.status === "todo" && !r.evaluation_id) ||
        (filters.status === "critical" && r.critical) ||
        (filters.status === "appeal" && r.open_appeals > 0)) &&
      (!s || r.conversation_id.toLowerCase().includes(s)));

    root.querySelectorAll(".seg").forEach((seg) =>
      seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", filters[seg.dataset.filter] === b.dataset.v)));

    if (!list.length) {
      $("#table").innerHTML = `<div class="empty">Ничего не найдено</div>`;
      return;
    }
    // На телефоне строки таблицы превращаются в карточки (см. .list-table в styles.css)
    $("#table").innerHTML = `<div class="table-wrap"><table class="list-table">
      <thead><tr><th>ID диалога</th><th>Канал</th><th>Язык</th><th>Реплик</th><th>Итог</th><th>Грубые ошибки</th><th>Апелляции</th></tr></thead>
      <tbody>${list.map((r) => `
        <tr data-id="${esc(r.conversation_id)}">
          <td class="mono c-id">${esc(r.conversation_id)}</td>
          <td class="c-ch">${r.has_audio ? "🎧 " : "💬 "}${esc(CHANNELS[r.channel] || r.channel)}</td>
          <td class="c-lang"><span class="badge na">${esc(r.language)}</span></td>
          <td class="c-msgs" data-label="Реплик">${r.messages}</td>
          <td class="c-total">${r.evaluation_id ? `<span class="score ${scoreClass(r.total)}">${fmt(r.total)}</span>` : `<span class="muted">не оценён</span>`}</td>
          <td class="c-crit">${r.critical ? `<span class="badge bad">⚠ грубая ошибка</span>` : r.evaluation_id ? `<span class="muted">нет</span>` : ""}</td>
          <td class="c-ap">${r.open_appeals ? `<span class="badge warn">${r.open_appeals} апелл.</span>` : ""}</td>
        </tr>`).join("")}</tbody></table></div>`;
    root.querySelectorAll("tbody tr").forEach((tr) => {
      tr.onclick = () => (location.hash = `#/c/${encodeURIComponent(tr.dataset.id)}`);
    });
  }

  root.querySelectorAll(".seg").forEach((seg) => {
    seg.onclick = (e) => {
      const v = e.target.dataset.v;
      if (!v) return;
      filters[seg.dataset.filter] = v;
      draw();
    };
  });
  $("#search").oninput = (e) => { filters.search = e.target.value; draw(); };
  $("#load").onclick = () => load(true);
  $("#group").onkeydown = (e) => { if (e.key === "Enter") load(true); };

  // ---- загрузка своего диалога ----
  $("#open-upload").onclick = () => { $("#upload-card").classList.toggle("hidden"); $("#up-text").focus(); };
  $("#up-cancel").onclick = () => $("#upload-card").classList.add("hidden");
  const readFile = (file) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(new Error("Не удалось прочитать аудиофайл"));
    r.readAsDataURL(file);
  });
  async function doUpload(evaluateAfter) {
    const text = $("#up-text").value.trim();
    if (!text) return toast("Вставьте транскрипт диалога", "bad");
    const buttons = [$("#up-eval"), $("#up-only")];
    buttons.forEach((b) => (b.disabled = true));
    try {
      const file = $("#up-audio").files[0];
      const payload = {
        transcript: text,
        language: $("#up-lang").value,
        channel: $("#up-channel").value,
        conversation_id: $("#up-id").value.trim() || null,
      };
      if (file) { payload.audio_base64 = await readFile(file); payload.audio_filename = file.name; }
      const rec = await api.uploadConversation(payload);
      toast(`Загружен диалог ${rec.conversation_id}: ${rec.transcript.length} реплик`, "ok");
      if (evaluateAfter) {
        $("#up-eval").innerHTML = `<span class="spinner"></span> Оцениваем…`;
        const ev = await api.evaluate(rec.conversation_id, $("#checklist").value, "UPLOAD");
        location.hash = `#/c/${encodeURIComponent(rec.conversation_id)}?group=UPLOAD&ev=${ev.id}`;
      } else {
        $("#group").value = "UPLOAD";
        $("#upload-card").classList.add("hidden");
        load();
      }
    } catch (e) {
      toast(e.message, "bad");
    } finally {
      buttons.forEach((b) => (b.disabled = false));
      $("#up-eval").textContent = "Загрузить и оценить";
    }
  }
  $("#up-eval").onclick = () => doUpload(true);
  $("#up-only").onclick = () => doUpload(false);

  $("#batch").onclick = async () => {
    const g = $("#group").value.trim() || DEFAULT_GROUP;
    try {
      const job = await api.batch(g, $("#checklist").value, $("#only-missing").checked);
      if (!job.total) { toast("Все диалоги уже оценены этим чек-листом", "ok"); return; }
      watchJob(job.id);
    } catch (e) { toast(e.message, "bad"); }
  };

  function watchJob(id) {
    $("#batch-box").classList.remove("hidden");
    $("#batch").disabled = true;
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const job = await api.batchStatus(id);
        $("#batch-bar").style.width = `${(job.done / job.total) * 100}%`;
        $("#batch-label").textContent = `Оценено ${job.done} из ${job.total}${job.failed.length ? ` · ошибок: ${job.failed.length}` : ""}`;
        if (job.status === "finished") {
          clearInterval(pollTimer);
          $("#batch").disabled = false;
          toast(job.failed.length ? `Готово, но ${job.failed.length} с ошибкой: ${job.failed[0].error}` : "Пакетная оценка завершена", job.failed.length ? "bad" : "ok");
          load();
        } else if (job.done % 3 === 0) {
          // промежуточно обновляем таблицу, чтобы было видно прогресс
          rows = await api.conversations($("#group").value.trim() || DEFAULT_GROUP);
          draw();
        }
      } catch (e) {
        clearInterval(pollTimer);
        $("#batch").disabled = false;
        toast(e.message, "bad");
      }
    }, 1500);
  }

  await load();
  return () => clearInterval(pollTimer);
}
