// Nicole — API yardımcı katmanı
const API_BASE = "/api";

// Tarayıcıdaki JS hataları sessiz kalmasın: sunucuya bildirilir (/api/diag/errors'ta görünür).
function reportClientError(message) {
  try {
    navigator.sendBeacon?.(`${API_BASE}/diag/client-error`,
      new Blob([JSON.stringify({ message: String(message).slice(0, 500), page: location.pathname })], { type: "application/json" }));
  } catch (_) { /* bildirim de başarısızsa yapacak bir şey yok */ }
}
window.addEventListener("error", (e) => reportClientError(`${e.message} @ ${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", (e) => reportClientError(`Promise: ${e.reason && (e.reason.stack || e.reason)}`));

async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`POST ${path} -> ${res.status}`);
  return res.json();
}

async function apiPut(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`PUT ${path} -> ${res.status}`);
  return res.json();
}

async function apiDelete(path) {
  const res = await fetch(API_BASE + path, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path} -> ${res.status}`);
  return res.json();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Aktif proje/bölüm tarayıcı localStorage'ında saklanır, sayfa yenilense de kaybolmaz.
const NicoleState = {
  get projectId() {
    return localStorage.getItem("nicole_project_id");
  },
  set projectId(v) {
    localStorage.setItem("nicole_project_id", v);
  },
  get chapterId() {
    return localStorage.getItem("nicole_chapter_id");
  },
  set chapterId(v) {
    localStorage.setItem("nicole_chapter_id", v);
  },
};