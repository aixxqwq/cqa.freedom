# CQA — Conversation Quality Analytics

Прототип для хакатона Freedom Broker: оценка диалогов операторов по настраиваемому чек-листу,
апелляции и пересмотр оценок.

## Как устроено

```
frontend/  (HTML/CSS/JS)  ──fetch──▶  backend/ (FastAPI)
                                                                 │
                    API организаторов (GET ?group_id=) ◀─────────┤ datasource.py
                    Claude API (оценка по критериям)  ◀──────────┤ evaluator.py
                                                                 │ scoring.py  — баллы, веса, N/A, округление
                                                                 │ service.py  — оценки, апелляции, пакеты
                                                                 └ storage.py  — SQLite (data/cqa.db)
```

**ИИ только читает диалог** и по каждому критерию возвращает ответ, объяснение и номера реплик-доказательств.
**Баллы считает код** (`scoring.py`): веса, перераспределение веса при N/A, грубые ошибки и округление.
Поэтому итог детерминирован, а после апелляции пересчитывается без повторного запроса к ИИ.

## Запуск

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows;  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # заполнить CQA_API_URL и ANTHROPIC_API_KEY
python -m uvicorn main:app --reload --port 8000
```

- Документация API с кнопкой «Try it out»: http://localhost:8000/docs
- Если рядом с `backend/` есть папка `frontend/`, сайт открывается на http://localhost:8000/
- Без `.env` всё работает офлайн: тестовые диалоги из `data/mock/DEV.json` и mock-оценщик без ИИ.
- Тесты: `python -m pytest -q tests`

## Фронтенд

Чистый HTML + CSS + JavaScript (ES-модули), без фреймворков и сборки. Открывается на http://localhost:8000/
после запуска бэкенда.

```
frontend/
  index.html            шапка + контейнер #app
  css/styles.css        все цвета/размеры — токены в :root (сюда переносить дизайн из Figma)
  js/config.js          адрес бэкенда (window.CQA_CONFIG.apiBase для Dynamics 365)
  js/api.js             все запросы к API
  js/ui.js              общие помощники: бейджи ответов, реплики, уведомления
  js/app.js             роутер по #hash, CQA.mount(element)
  js/views/list.js          Диалоги: фильтры, статистика, «Оценить все» с прогрессом
  js/views/conversation.js  Карточка: транскрипт, плеер, итог, секции, критерии, доказательства, апелляция
  js/views/appeals.js       Проверяющий: спорная оценка, комментарий, фрагмент, решение
  js/views/checklist.js     Редактор чек-листа: критерии, веса, шкалы, N/A, секции, применение к набору
```

Сценарий защиты: Диалоги → открыть диалог → «Оценить» → клик по «Реплика #N» прокручивает
к реплике (у звонка ещё и перематывает запись) → N/A с причиной → «Оспорить» → Апелляции →
«Изменить оценку» → в карточке видны исходная оценка, решение и новый итог → Чек-лист →
заменить критерий → «Сохранить» → «Применить к набору».

**Dynamics 365:** приложение не зависит от страницы — `CQA.mount(element)` монтирует его в любой
контейнер, адрес API задаётся через `window.CQA_CONFIG = {apiBase: "..."}`, навигация через `#hash`.

## Точность и прозрачность оценки

- **Голосование.** Каждый диалог ИИ оценивает 3 раза независимо и параллельно (`CQA_VOTES`, по умолчанию 3).
  По каждому критерию берётся ответ большинства, для шкалы без большинства — медиана ступеней.
  В карточке у критерия видна уверенность: «✓ 3/3» или «спорно 2/3».
- **«Как получена оценка».** В карточке раскрывается панель: голоса по каждому критерию, время, токены,
  стоимость и сырой JSON-ответ модели на каждый голос — видно, что диалог действительно анализируется.
- **Эталон и точность.** Внизу карточки можно разметить эталон (как оценил бы человек), или импортировать
  разметку CSV/JSON на странице «Точность». Страница считает совпадение по критериям, ошибку итогового
  балла и поиск грубых ошибок — так же, как организаторы сравнят систему с Ground Truth.

## Результаты для организаторов

Файлы по формату «Приложения к гайду» (JSON 1.0):

- `results/dev/results.json` — все 65 диалогов DEV;
- `results/prod/results.json` — 10 контрольных чатов.

Выгрузка: на сайте «Диалоги» → «Команда · выгрузка results.json» → «Сохранить results.json», или из папки `backend`:

```bash
python export_results.py --group DEV --set dev --team "Название команды"
python export_results.py --group FBroker --set prod --team "Название команды" --evaluate --refresh --expect 10
```

Скрипт берёт последнюю оценку ИИ по каждому диалогу, переводит критерии в коды организаторов
(`contact, discovery, identification, consultation, empathy, dialogue, closure, critical`), значения в
`true / false / null / число`, номера реплик — с 1, и проверяет файл: все диалоги ровно по разу, 8 критериев,
допустимые значения, `critical_flag` = `critical`. Неоценённый диалог попадает как `status: "error"`.

## Выбор ИИ

Поддерживаются два облачных провайдера, переключение — в `backend/.env`, код менять не нужно:

| `CQA_EVALUATOR` | Модель | Ключ |
|---|---|---|
| `claude` | `claude-opus-5-5` (или `CQA_MODEL`) | `ANTHROPIC_API_KEY` |
| `deepseek` | `deepseek-flash` (V4.1-Flash, без рассуждений; или `DEEPSEEK_MODEL`) | `DEEPSEEK_API_KEY` |

Промпт, чек-лист, голосование и подсчёт баллов одинаковые для обоих. Модель записывается в каждую оценку.
DeepSeek ≈ $0,002 за один голос (по цене в часы пик, $0,30 / $1,20 за 1M токенов).

## Модель и стоимость

Модель: `claude-opus-5-5` (настраивается через `CQA_MODEL`). Одна оценка — один запрос к API.
Стоимость каждой оценки считается по фактическим токенам и сохраняется в `usage.cost_usd` оценки.
По прогону всего набора DEV (65 диалогов, 109 оценок, чек-лист v16):

| | |
|---|---|
| Модель | `claude-opus-5-5`, effort `medium`, 1 вызов на оценку |
| Токены на оценку | в среднем ~4 000 входных, ~1 750 выходных |
| Стоимость одной оценки | **≈ $0,05** (от $0,04 до $0,08; цена $4 / $20 за 1M токенов) |
| Весь набор DEV (65 диалогов) | ≈ $3,3 |

## API для фронтенда

Все ответы — JSON. Ошибки: `{"detail": "..."}` с кодом 400 или 404.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/api/conversations?group_id=DEV` | список диалогов + последний итог |
| GET | `/api/conversations/{id}?group_id=DEV` | диалог целиком |
| POST | `/api/conversations/upload` | свой диалог: `{transcript, language, channel, conversation_id?, audio_base64?, audio_filename?}` → набор `UPLOAD` |
| DELETE | `/api/conversations/upload/{id}` | удалить загруженный диалог |
| GET | `/api/conversations/{id}/audio?group_id=` | редирект на mp3 записи (у организаторов ссылка ведёт на HTML-плеер, сервер достаёт из него файл) |
| GET | `/api/checklists` | все чек-листы |
| GET | `/api/checklists/{id}/versions` | история сохранённых версий |
| GET / PUT | `/api/checklists/{id}` | получить / сохранить (version +1) |
| POST | `/api/checklists` | создать новый чек-лист |
| POST | `/api/evaluations` | `{conversation_id, checklist_id, group_id}` → оценка |
| GET | `/api/evaluations?conversation_id=` | история оценок |
| GET | `/api/evaluations/{id}` | оценка целиком |
| POST | `/api/batch` | `{group_id, checklist_id, only_missing}` → оценить всю группу в фоне |
| GET | `/api/batch/{job_id}` | прогресс `{done, total, failed, status}` |
| POST | `/api/appeals` | `{evaluation_id, criterion_id, comment}` |
| GET | `/api/appeals?status=open` | очередь проверяющего |
| POST | `/api/appeals/{id}/decision` | `{action: "confirm"\|"change", new_answer, reason}` |

### Диалог

`transcript[i].idx` — номер реплики. Делайте у каждой реплики `id="msg-<idx>"`:
ссылка на доказательство → `document.getElementById('msg-' + idx).scrollIntoView()`.
У звонков `audio_url` не пустой, а `timestamp` в формате ISO 8601 — по нему ставится позиция плеера.

### Оценка (главное — поле `view`)

```jsonc
{
  "id": "ev_...", "conversation_id": "...", "checklist_version": 1, "model": "...",
  "usage": {"cost_usd": 0.03},
  "original": {"total": 85, "sections": [...], "critical_errors": []},   // исходная оценка ИИ
  "final":    {"total": 90, "sections": [{"id": "open", "name": "...", "score": 100}], "critical_errors": []},
  "view": [{                                   // по одному объекту на критерий — рисуйте таблицу из него
    "criterion_id": "greeting", "title": "Приветствие", "section": "open",
    "answer": "yes",                           // yes | no | na | число шкалы (ответ ИИ)
    "explanation": "Оператор поздоровался в реплике [3]",
    "evidence": [3],                           // номера реплик → ссылки на #msg-3
    "checked_range": null,                     // {from, to}, если действие пропущено
    "na_reason": null,                         // причина, если N/A
    "original": {"points": 10, "max_points": 10, "weight": 10, "effective_weight": 10, "critical_error": false},
    "final":    {...},                         // то же после решения проверяющего
    "override": null                           // {answer, reason, reviewer}, если изменено по апелляции
  }]
}
```

### Апелляция

```jsonc
{
  "id": "ap_...", "status": "open" | "resolved", "criterion_title": "...", "comment": "...",
  "disputed": {"answer": "no", "explanation": "...", "evidence": [5]},
  "fragment": [ /* реплики вокруг доказательства — показать проверяющему */ ],
  "decision": {"action": "change", "new_answer": "yes", "reason": "..."},
  "total_before": 80, "total_after": 90
}
```

## Чек-лист

`backend/checklists/default.json` — «Чек-лист качества чатов» из PDF организаторов: 8 критериев,
4 секции, шкалы со ступенями (0/5/10/15/20 и 0/4/7/10), N/A с перераспределением веса,
округление итога до 2 знаков. «Грубые ошибки» — флаг без баллов (`type: "flag"`, «Да» = нарушение),
в итог не входит и показывается отдельно в `final.critical_errors`.

Типы критериев для редактора: `binary` (Да/Нет), `scale` (`scale.levels: [{value, label}]`), `flag`.
Общие правила оценки (памятка со стр. 3) лежат в поле `guidance` и целиком уходят в промпт ИИ.

## Открыть в VS Code

`File → Open Folder` → папка `cqa-hackathon-project`. Интерпретатор (`backend/.venv`) подхватится сам.
F5 → «CQA backend (uvicorn)» запускает сервер, «CQA tests (pytest)» — тесты.
