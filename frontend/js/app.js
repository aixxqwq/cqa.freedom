// Роутер по hash (#/...). Работает без сервера статики и внутри Dynamics 365 web resource.
import { api } from "./api.js";
import { renderList } from "./views/list.js";
import { renderConversation } from "./views/conversation.js";
import { renderAppeals } from "./views/appeals.js";
import { renderChecklist } from "./views/checklist.js";
import { renderAccuracy } from "./views/accuracy.js";

const routes = [
  { re: /^#?\/?$/, name: "list", view: renderList },
  { re: /^#\/c\/([^?]+)/, name: "list", view: renderConversation },
  { re: /^#\/appeals/, name: "appeals", view: renderAppeals },
  { re: /^#\/checklist/, name: "checklist", view: renderChecklist },
  { re: /^#\/accuracy/, name: "accuracy", view: renderAccuracy },
];

let root;
let cleanup = null;

export function hashParams() {
  const i = location.hash.indexOf("?");
  return new URLSearchParams(i >= 0 ? location.hash.slice(i + 1) : "");
}

async function route() {
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  const hash = location.hash || "#/";
  const r = routes.find((x) => x.re.test(hash)) || routes[0];
  const match = hash.match(r.re);
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === r.name));
  window.scrollTo(0, 0);
  cleanup = await r.view(root, { id: match?.[1] ? decodeURIComponent(match[1]) : null, params: hashParams() });
  refreshAppealCount();
}

export async function refreshAppealCount() {
  const el = document.getElementById("appeal-count");
  if (!el) return;
  try {
    const open = await api.appeals("open");
    el.textContent = open.length;
    el.classList.toggle("hidden", open.length === 0);
  } catch { el.classList.add("hidden"); }
}

// ---- светлая / тёмная тема ----
function setupThemeToggle() {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const paint = () => {
    const light = document.documentElement.dataset.theme === "light";
    btn.textContent = light ? "☾" : "☀";
    btn.title = light ? "Тёмная тема" : "Светлая тема";
  };
  btn.onclick = () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("cqa.theme", next); } catch { /* приватный режим */ }
    paint();
  };
  paint();
}

/** Монтирует приложение в элемент (для встраивания, например в Dynamics 365). */
export function mount(el) {
  root = el;
  if (!document.documentElement.dataset.theme) document.documentElement.dataset.theme = "dark";
  setupThemeToggle();
  window.addEventListener("hashchange", route);
  route();
}

window.CQA = { mount };
mount(document.getElementById("app"));
