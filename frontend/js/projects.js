// Nicole — Romanlar (çoklu proje) ekranı

async function loadProjects() {
  const projects = await apiGet("/projects");
  const grid = document.getElementById("projectsGrid");
  grid.innerHTML = "";

  if (projects.length === 0) {
    grid.innerHTML = `<div style="color:var(--paper-dim); grid-column:1/-1;">Henüz bir proje yok — yukarıdan yeni bir tane oluştur.</div>`;
    return;
  }

  for (const p of projects) {
    const card = document.createElement("div");
    card.className = "project-card";
    card.innerHTML = `
      <div class="title">${escapeHtml(p.title)}</div>
      <div class="meta">Son güncelleme: ${new Date(p.updated_at).toLocaleDateString("tr-TR")}</div>
    `;
    card.addEventListener("click", () => openProject(p.id));
    grid.appendChild(card);
  }
}

async function openProject(projectId) {
  NicoleState.projectId = projectId;

  // Bu projenin bölümleri var mı diye bak, yoksa bir tane oluştur, sonra editöre dön.
  const chapters = await apiGet(`/projects/${projectId}/chapters`);
  let chapterId;
  if (chapters.length === 0) {
    const chapter = await apiPost(`/projects/${projectId}/chapters`, { title: "Yeni Bölüm" });
    chapterId = chapter.id;
  } else {
    chapterId = chapters[0].id;
  }
  NicoleState.chapterId = chapterId;
  window.location.href = "index.html";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("newProjectBtn").addEventListener("click", async () => {
  const input = document.getElementById("newProjectTitle");
  const title = input.value.trim();
  if (!title) return;
  const project = await apiPost("/projects", { title });
  input.value = "";
  await loadProjects();
  openProject(project.id);
});

loadProjects();
