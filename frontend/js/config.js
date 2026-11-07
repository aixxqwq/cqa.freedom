// Адрес бэкенда.
// - Если страницу отдаёт сам бэкенд (http://localhost:8000) — пустая строка (тот же адрес).
// - Если фронтенд открыт отдельно (Live Server, file://, Dynamics 365) — полный адрес бэкенда.
// Переопределить можно до загрузки app.js:  <script>window.CQA_CONFIG = {apiBase: "https://..."}</script>
const fromWindow = window.CQA_CONFIG && window.CQA_CONFIG.apiBase;
// Страница открыта не бэкендом: файл с диска или отдельный dev-сервер (Live Server и т.п.)
const standalone = location.protocol === "file:" || ["5500", "5501", "3000", "5173", "8080"].includes(location.port);

export const API_BASE = fromWindow ?? (standalone ? "http://localhost:8000" : "");
export const DEFAULT_GROUP = "DEV";
