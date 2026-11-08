"""CQA API. Запуск:  uvicorn main:app --reload --port 8000
Документация с кнопкой «Try it out»: http://localhost:8000/docs
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional, Union

import httpx
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402

from cqa import datasource, export, service, storage, upload  # noqa: E402

@asynccontextmanager
async def lifespan(_: FastAPI):
    service.ensure_default_checklist()
    yield


app = FastAPI(title="CQA — Conversation Quality Analytics", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """Браузер каждый раз сверяет файлы сайта с сервером, чтобы после правок
    не работала старая версия JS из кеша (неизменённые файлы приходят как 304)."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(service.NotFound)
def _not_found(_: Request, exc: service.NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
def _no_data(_: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": f"Нет данных для группы: {exc}"})


@app.exception_handler(httpx.HTTPError)
def _upstream(_: Request, exc: httpx.HTTPError):
    return JSONResponse(status_code=502, content={"detail": f"API организаторов недоступно: {exc}"})


@app.exception_handler(ValueError)
def _bad_value(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
def _invalid(_: Request, exc: ValidationError):
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    return JSONResponse(status_code=400, content={"detail": errors})


# ---------- диалоги ----------

@app.get("/api/conversations")
def conversations(group_id: str = "DEV", refresh: bool = False):
    """Список диалогов группы + последний итог оценки по каждому."""
    return service.list_conversations(group_id, refresh)


class UploadRequest(BaseModel):
    transcript: str                      # текст «Роль: реплика» или JSON
    language: str = "RU"
    channel: str = "agent chat"
    conversation_id: Optional[str] = None
    audio_base64: Optional[str] = None   # файл записи (data URL или base64)
    audio_filename: Optional[str] = None
    audio_url: Optional[str] = None      # или ссылка на запись


@app.post("/api/conversations/upload")
def upload_conversation(req: UploadRequest):
    """Загрузить свой диалог в набор UPLOAD."""
    return upload.create(req.transcript, req.language, req.channel, req.conversation_id,
                         req.audio_base64, req.audio_filename, req.audio_url)


@app.delete("/api/conversations/upload/{conversation_id}")
def delete_uploaded(conversation_id: str):
    upload.delete(conversation_id)
    return {"ok": True}


@app.get("/api/conversations/{conversation_id}/audio")
def conversation_audio(conversation_id: str, group_id: str = "DEV"):
    """Перенаправляет плеер на сам аудиофайл звонка (свежая ссылка при каждом запросе)."""
    conv = service.get_conversation(group_id, conversation_id)
    url = conv.get("audio_url")
    if not url:
        raise HTTPException(404, "У диалога нет записи")
    if url.startswith("/uploads/"):
        return RedirectResponse(url)
    try:
        return RedirectResponse(datasource.resolve_audio(url))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось получить запись: {e}")


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, group_id: str = "DEV"):
    """Полный диалог: transcript[i].idx — номер реплики (якорь #msg-<idx>)."""
    return service.get_conversation(group_id, conversation_id)


# ---------- чек-листы ----------

@app.get("/api/checklists")
def checklists():
    return storage.list_all("checklists")


@app.get("/api/checklists/{checklist_id}")
def checklist(checklist_id: str):
    return service.get_checklist(checklist_id).model_dump()


@app.get("/api/checklists/{checklist_id}/versions")
def checklist_versions(checklist_id: str):
    """Все сохранённые версии чек-листа, новые сверху (с полем saved_at)."""
    return service.list_checklist_versions(checklist_id)


@app.post("/api/checklists")
def create_checklist(body: dict[str, Any]):
    """Новый чек-лист (id можно не указывать)."""
    body.pop("version", None)
    return service.save_checklist(body, bump_version=False)


@app.put("/api/checklists/{checklist_id}")
def update_checklist(checklist_id: str, body: dict[str, Any]):
    """Сохранить изменения — версия увеличится на 1."""
    body["id"] = checklist_id
    return service.save_checklist(body)


# ---------- оценки ----------

class EvaluateRequest(BaseModel):
    conversation_id: str
    checklist_id: str = "default"
    group_id: str = "DEV"


@app.post("/api/evaluations")
def evaluate(req: EvaluateRequest):
    try:
        return service.run_evaluation(req.group_id, req.conversation_id, req.checklist_id)
    except (service.NotFound, ValueError, FileNotFoundError):
        raise
    except Exception as e:  # noqa: BLE001 — ошибки ИИ показываем пользователю текстом
        raise HTTPException(502, f"Оценка не удалась: {e}")


@app.get("/api/evaluations")
def evaluations(conversation_id: Optional[str] = None):
    return service.list_evaluations(conversation_id)


@app.get("/api/evaluations/{evaluation_id}")
def evaluation(evaluation_id: str):
    return service.get_evaluation(evaluation_id)


class BatchRequest(BaseModel):
    group_id: str = "DEV"
    checklist_id: str = "default"
    only_missing: bool = False


@app.post("/api/batch")
def batch(req: BatchRequest):
    """Оценить все диалоги группы в фоне. Прогресс: GET /api/batch/{job_id}."""
    return service.start_batch(req.group_id, req.checklist_id, req.only_missing)


@app.get("/api/batch/{job_id}")
def batch_status(job_id: str):
    return service.get_job(job_id)


# ---------- выгрузка results.json для организаторов ----------

class ExportRequest(BaseModel):
    group_id: str
    set: str            # dev | prod
    team_name: str
    mode: str = "external_api"


@app.post("/api/export")
def export_results(req: ExportRequest):
    """Пишет results/<set>/results.json в репозиторий и проверяет формат по приложению к гайду."""
    if req.set not in ("dev", "prod"):
        raise HTTPException(400, "set: dev или prod")
    if not req.team_name.strip():
        raise HTTPException(400, "Укажите название команды")
    data = export.build_results(req.group_id, req.team_name.strip(), req.mode)
    path = Path(__file__).resolve().parent.parent / "results" / req.set / "results.json"
    export.write(data, path)
    expected = 10 if req.set == "prod" else None
    return {
        "path": f"results/{req.set}/results.json",
        "total": len(data["results"]),
        "ok": sum(r["status"] == "ok" for r in data["results"]),
        "errors": [r["conversation_id"] for r in data["results"] if r["status"] == "error"],
        "model": data["model"]["name"],
        "problems": export.validate(data, expected),
    }


# ---------- эталон и точность ----------

class GroundTruthRequest(BaseModel):
    answers: dict[str, Union[float, str, None]]
    author: str = ""
    checklist_id: str = "default"


class GroundTruthImport(BaseModel):
    items: list[dict[str, Any]]   # [{conversation_id, answers: {criterion_id: answer}}]
    checklist_id: str = "default"


@app.get("/api/ground-truth")
def ground_truth_list():
    return service.list_ground_truth()


@app.put("/api/ground-truth/{conversation_id}")
def ground_truth_save(conversation_id: str, req: GroundTruthRequest):
    """Эталонная разметка диалога (ответы человека-проверяющего)."""
    return service.save_ground_truth(conversation_id, req.answers, req.author, req.checklist_id)


@app.delete("/api/ground-truth/{conversation_id}")
def ground_truth_delete(conversation_id: str):
    service.delete_ground_truth(conversation_id)
    return {"ok": True}


@app.post("/api/ground-truth/import")
def ground_truth_import(req: GroundTruthImport):
    saved, errors = 0, []
    for it in req.items:
        try:
            service.save_ground_truth(str(it["conversation_id"]), it.get("answers", {}), it.get("author", "импорт"), req.checklist_id)
            saved += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{it.get('conversation_id')}: {e}")
    return {"saved": saved, "errors": errors}


@app.get("/api/accuracy")
def accuracy(checklist_id: str = "default"):
    """Точность ИИ относительно эталона: по критериям, по диалогам, ошибка итогового балла."""
    return service.accuracy(checklist_id)


# ---------- апелляции ----------

class AppealRequest(BaseModel):
    evaluation_id: str
    criterion_id: str
    comment: str
    author: str = "оператор"


class DecisionRequest(BaseModel):
    action: str  # confirm | change
    reason: str
    new_answer: Union[float, str, None] = None
    reviewer: str = "проверяющий"


@app.post("/api/appeals")
def create_appeal(req: AppealRequest):
    return service.create_appeal(req.evaluation_id, req.criterion_id, req.comment, req.author)


@app.get("/api/appeals")
def appeals(status: Optional[str] = None, evaluation_id: Optional[str] = None):
    return service.list_appeals(status, evaluation_id)


@app.get("/api/appeals/{appeal_id}")
def appeal(appeal_id: str):
    a = storage.get("appeals", appeal_id)
    if not a:
        raise HTTPException(404, "апелляция не найдена")
    return a


@app.post("/api/appeals/{appeal_id}/decision")
def decide(appeal_id: str, req: DecisionRequest):
    return service.decide_appeal(appeal_id, req.action, req.reason, req.new_answer, req.reviewer)


# ---------- фронтенд (если папка frontend есть рядом) ----------

upload.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=upload.UPLOAD_DIR), name="uploads")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
