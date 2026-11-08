// Все запросы к бэкенду — только здесь. Контракт описан в README.md.
import { API_BASE } from "./config.js";

function errorText(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).filter((x) => x !== "body").join(".")}: ${d.msg}`).join("; ");
  }
  return JSON.stringify(detail);
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Сервер недоступен. Запущен ли бэкенд на " + (API_BASE || location.origin) + "?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data.detail, `${res.status} ${res.statusText}`));
  return data;
}

const q = (params) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  return s.toString() ? "?" + s : "";
};

export const api = {
  conversations: (group_id, refresh = false) => request("GET", `/api/conversations${q({ group_id, refresh })}`),
  conversation: (id, group_id) => request("GET", `/api/conversations/${encodeURIComponent(id)}${q({ group_id })}`),

  // Плеер берёт запись через наш сервер: он достаёт прямую ссылку на mp3 со страницы организаторов.
  audioUrl: (id, group_id) => `${API_BASE}/api/conversations/${encodeURIComponent(id)}/audio${q({ group_id })}`,
  uploadConversation: (payload) => request("POST", "/api/conversations/upload", payload),
  deleteUploaded: (id) => request("DELETE", `/api/conversations/upload/${encodeURIComponent(id)}`),

  checklists: () => request("GET", "/api/checklists"),
  checklist: (id) => request("GET", `/api/checklists/${encodeURIComponent(id)}`),
  checklistVersions: (id) => request("GET", `/api/checklists/${encodeURIComponent(id)}/versions`),
  createChecklist: (cl) => request("POST", "/api/checklists", cl),
  updateChecklist: (cl) => request("PUT", `/api/checklists/${encodeURIComponent(cl.id)}`, cl),

  evaluate: (conversation_id, checklist_id, group_id) =>
    request("POST", "/api/evaluations", { conversation_id, checklist_id, group_id }),
  evaluations: (conversation_id) => request("GET", `/api/evaluations${q({ conversation_id })}`),
  evaluation: (id) => request("GET", `/api/evaluations/${encodeURIComponent(id)}`),

  batch: (group_id, checklist_id, only_missing) => request("POST", "/api/batch", { group_id, checklist_id, only_missing }),
  batchStatus: (job_id) => request("GET", `/api/batch/${encodeURIComponent(job_id)}`),

  exportResults: (group_id, set, team_name) => request("POST", "/api/export", { group_id, set, team_name }),
  groundTruth: () => request("GET", "/api/ground-truth"),
  saveGroundTruth: (id, answers, author) =>
    request("PUT", `/api/ground-truth/${encodeURIComponent(id)}`, { answers, author }),
  deleteGroundTruth: (id) => request("DELETE", `/api/ground-truth/${encodeURIComponent(id)}`),
  importGroundTruth: (items) => request("POST", "/api/ground-truth/import", { items }),
  accuracy: () => request("GET", "/api/accuracy"),

  appeals: (status, evaluation_id) => request("GET", `/api/appeals${q({ status, evaluation_id })}`),
  createAppeal: (evaluation_id, criterion_id, comment) =>
    request("POST", "/api/appeals", { evaluation_id, criterion_id, comment }),
  decide: (appeal_id, action, reason, new_answer) =>
    request("POST", `/api/appeals/${encodeURIComponent(appeal_id)}/decision`, { action, reason, new_answer }),
};
