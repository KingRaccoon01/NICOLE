// Nicole — Uygulama başlatma ve genel bağlantılar

async function initApp() {
  // Aktif proje yoksa doğrudan Romanlar ekranına yönlendir.
  if (!NicoleState.projectId || !NicoleState.chapterId) {
    window.location.href = "projects.html";
    return;
  }

  const project = await apiGet(`/projects/${NicoleState.projectId}`);
  document.getElementById("chapterLabel").textContent = `DİKTE AKIŞI — ${project.title}`;

  connectDictationSocket(NicoleState.chapterId);

  const paragraphs = await apiGet(`/chapters/${NicoleState.chapterId}/paragraphs`);
  paragraphs.forEach(renderParagraph);
  updateStats();

  loadChatHistory();
  loadLore();
}

document.getElementById("projectsBtn").addEventListener("click", () => {
  window.location.href = "projects.html";
});

document.getElementById("exportBtn").addEventListener("click", () => {
  if (!NicoleState.chapterId) return;
  window.location.href = `/api/chapters/${NicoleState.chapterId}/export`;
});

document.querySelectorAll(".side-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".side-tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".side-tab-content").forEach((c) => c.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");

    // Lore Sözlüğü sekmesine her geçişte listeyi tazele — flag çözümlemesi
    // yeni terim eklemiş olabilir, sekme her açıldığında güncel görünsün.
    if (tab.dataset.tab === "lore") {
      loadLore();
    }
  });
});

// --- İnce scrollbar: sadece scroll edilirken görünür, durunca kaybolur ---
// event delegation kullanıyoruz (capture: true) ki sonradan eklenen .thin-scroll
// elemanları (örn. yeni sohbet mesajları) için ayrıca dinleyici eklemeye gerek kalmasın.
const _scrollHideTimers = new WeakMap();
document.addEventListener(
  "scroll",
  (e) => {
    const el = e.target;
    if (!el.classList || !el.classList.contains("thin-scroll")) return;
    el.classList.add("scrolling");
    clearTimeout(_scrollHideTimers.get(el));
    _scrollHideTimers.set(el, setTimeout(() => el.classList.remove("scrolling"), 800));
  },
  true
);

initApp();