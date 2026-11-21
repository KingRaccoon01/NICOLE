// Nicole — Lore Sözlüğü paneli (her romanın kendi sözlüğü vardır)

const loreUrl = (suffix = "") => `/projects/${NicoleState.projectId}/lore${suffix}`;

async function loadLore(query) {
  const list = document.getElementById("loreList");
  let entries;
  try {
    entries = await apiGet(loreUrl(query ? `?q=${encodeURIComponent(query)}` : ""));
  } catch (err) {
    // Sessizce boş görünmesin: sunucu/dosya uyumsuzluğu gibi sorunlar burada görünür
    list.innerHTML = `<div style="color:var(--vermil); font-size:13px;">Sözlük yüklenemedi: ${escapeHtml(err.message)}</div>`;
    console.error("[Nicole] Lore Sözlüğü yüklenemedi:", err);
    return;
  }
  list.innerHTML = "";

  if (entries.length === 0) {
    list.innerHTML = `<div style="color:var(--paper-dim); font-size:13px;">Bu romanın sözlüğünde henüz terim yok.</div>`;
    return;
  }

  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "lore-entry-row";
    row.innerHTML = `
      <div class="lore-entry-info">
        <span class="term">${escapeHtml(entry.term)}</span><span class="source-tag">${entry.source === "auto" ? "otomatik" : "manuel"}</span>
        ${entry.definition ? `<div class="def">${escapeHtml(entry.definition)}</div>` : ""}
      </div>
      <button class="lore-delete-btn" data-id="${entry.id}" data-term="${escapeHtml(entry.term)}" title="Terimi sil">🗑</button>
    `;
    list.appendChild(row);
  }

  list.querySelectorAll(".lore-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      requestDeleteLoreEntry(btn.dataset.id, btn.dataset.term);
    });
  });
}

document.getElementById("loreSearch").addEventListener("input", (e) => {
  loadLore(e.target.value);
});

document.getElementById("addLoreBtn").addEventListener("click", async () => {
  const input = document.getElementById("newLoreTerm");
  const term = input.value.trim();
  if (!term) return;
  await apiPost(loreUrl(), { term });
  input.value = "";
  loadLore();
});

// --- Silme onayı ---
// "Bir daha gösterme" tercihi tarayıcıda kalıcı olarak saklanır (localStorage).
const SKIP_DELETE_CONFIRM_KEY = "nicole_skip_lore_delete_confirm";

function requestDeleteLoreEntry(entryId, term) {
  if (localStorage.getItem(SKIP_DELETE_CONFIRM_KEY) === "true") {
    performDeleteLoreEntry(entryId);
    return;
  }
  openDeleteLoreModal(entryId, term);
}

function openDeleteLoreModal(entryId, term) {
  document.getElementById("deleteLoreTermName").textContent = term;
  document.getElementById("deleteLoreDontAskAgain").checked = false;
  const overlay = document.getElementById("deleteLoreModalOverlay");
  overlay.dataset.entryId = entryId;
  overlay.classList.add("open");
}

function closeDeleteLoreModal() {
  document.getElementById("deleteLoreModalOverlay").classList.remove("open");
}

async function performDeleteLoreEntry(entryId) {
  await apiDelete(loreUrl(`/${entryId}`));
  loadLore(document.getElementById("loreSearch").value);
}

document.getElementById("deleteLoreCancelBtn").addEventListener("click", closeDeleteLoreModal);
document.getElementById("deleteLoreCancelBtnX").addEventListener("click", closeDeleteLoreModal);
document.getElementById("deleteLoreModalOverlay").addEventListener("click", (e) => {
  if (e.target.id === "deleteLoreModalOverlay") closeDeleteLoreModal();
});

document.getElementById("deleteLoreConfirmBtn").addEventListener("click", async () => {
  const overlay = document.getElementById("deleteLoreModalOverlay");
  const entryId = overlay.dataset.entryId;
  if (document.getElementById("deleteLoreDontAskAgain").checked) {
    localStorage.setItem(SKIP_DELETE_CONFIRM_KEY, "true");
  }
  closeDeleteLoreModal();
  await performDeleteLoreEntry(entryId);
});