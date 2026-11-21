// Nicole — API yardımcı katmanı
const API_BASE = "/api";

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