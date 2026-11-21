// Nicole — Uygulama başlatma ve genel bağlantılar

async function initApp() {
  // Aktif proje yoksa doğrudan Romanlar ekranına yönlendir.
  if (!NicoleState.projectId || !NicoleState.chapterId) {
    window.location.href = "projects.html";
    return;
  }

  const project = await apiGet(`/projects/${NicoleState.projectId}`);
  document.getElementById("chapterLabel").textContent = `DİKTE AKIŞI — ${project.title}`;

  const chapters = await apiGet(`/projects/${NicoleState.projectId}/chapters`);
  // localStorage'da başka bir romanın bölümü kalmış olabilir — bu romanın ilk bölümüne dön
  if (!chapters.some((c) => String(c.id) === String(NicoleState.chapterId)) && chapters.length) {
    NicoleState.chapterId = chapters[0].id;
  }
  fillChapterSelect(chapters);

  connectDictationSocket(NicoleState.chapterId);

  const paragraphs = await apiGet(`/chapters/${NicoleState.chapterId}/paragraphs`);
  paragraphs.forEach(renderParagraph);
  updateStats();

  loadChatHistory();
  loadLore();
}

// --- Bölümler ---
const NEW_CHAPTER = "__new__";
const RENAME_CHAPTER = "__rename__";
const DELETE_CHAPTER = "__delete__";

function fillChapterSelect(chapters) {
  const select = document.getElementById("chapterSelect");
  select.innerHTML = "";
  for (const c of chapters) select.add(new Option(c.title, c.id));
  select.add(new Option("✎ Bölüm adını değiştir…", RENAME_CHAPTER));
  select.add(new Option("＋ Yeni bölüm…", NEW_CHAPTER));
  if (chapters.length > 1) select.add(new Option("🗑 Bu bölümü sil…", DELETE_CHAPTER));
  select.value = NicoleState.chapterId;
  select.dataset.count = chapters.length;
}

document.getElementById("chapterSelect").addEventListener("change", async (e) => {
  const select = e.target;
  const choice = select.value;
  select.value = NicoleState.chapterId; // iptal edilirse seçim eski bölümde kalsın

  if (choice === NEW_CHAPTER) {
    const title = prompt("Yeni bölümün adı:", `Bölüm ${Number(select.dataset.count) + 1}`);
    if (!title || !title.trim()) return;
    const chapter = await apiPost(`/projects/${NicoleState.projectId}/chapters`, { title: title.trim() });
    NicoleState.chapterId = chapter.id;
  } else if (choice === RENAME_CHAPTER) {
    const current = select.selectedOptions[0]?.textContent || "";
    const title = prompt("Bölümün yeni adı:", current);
    if (!title || !title.trim() || title.trim() === current) return;
    await apiPut(`/chapters/${NicoleState.chapterId}`, { title: title.trim() });
  } else if (choice === DELETE_CHAPTER) {
    const name = [...select.options].find((o) => o.value === String(NicoleState.chapterId))?.textContent || "";
    const count = document.querySelectorAll(".paragraph-block").length;
    if (!confirm(`"${name}" bölümü ve içindeki ${count} paragraf kalıcı olarak silinsin mi?\nBu işlem geri alınamaz.`)) return;
    const res = await apiDelete(`/chapters/${NicoleState.chapterId}`);
    NicoleState.chapterId = res.next_chapter_id;
  } else {
    NicoleState.chapterId = choice;
  }
  // Dikte soketi, paragraflar ve sayaçlar bölüme bağlı — en güvenlisi sayfayı yeniden yüklemek
  window.location.reload();
});

document.getElementById("projectsBtn").addEventListener("click", () => {
  window.location.href = "projects.html";
});

document.getElementById("exportBtn").addEventListener("click", async () => {
  if (!NicoleState.projectId) return;
  const res = await fetch(`/api/projects/${NicoleState.projectId}/export`);
  if (!res.ok) return alert(`Word dosyası oluşturulamadı (${res.status}).`);
  const pending = Number(res.headers.get("X-Unresolved-Flags") || 0);
  if (pending > 0 && !confirm(`Romanda çözülmemiş ${pending} flag var — bu kelimeler olduğu gibi yazılacak.\nYine de indirilsin mi?`)) return;
  const disposition = res.headers.get("Content-Disposition") || "";
  const name = decodeURIComponent((disposition.match(/filename\*=UTF-8''([^;]+)/) || [, "roman.docx"])[1]);
  const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(await res.blob()), download: name });
  link.click();
  URL.revokeObjectURL(link.href);
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