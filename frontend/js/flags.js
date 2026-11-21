// Nicole — Flag sistemi: paragraf render + Müdahale modalı (4 aksiyon)

let activeFlag = null; // modal açıkken üzerinde çalışılan flag verisi
let editingParagraphId = null; // şu an düzenleme modunda olan paragrafın id'si (varsa)

function statusToClass(status) {
  return { accepted_raw: "blue", accepted_suggestion: "green", manual: "yellow" }[status] || null;
}

function isKeyboardMode() {
  return document.getElementById("inputModeSelect")?.value === "keyboard";
}

function renderParagraph(paragraph) {
  document.getElementById("emptyState")?.remove();

  // Append durumunda (mevcut paragrafın sonuna ekleme) aynı paragraf id'si
  // tekrar gelir — bu durumda yeni bir blok eklemek yerine mevcut bloğu
  // güncelliyoruz, böylece "her cümle ayrı satır" olmadan doğal bir paragraf
  // birikimi oluşur.
  const existing = document.querySelector(`.paragraph-block[data-paragraph-id="${paragraph.id}"]`);
  const block = existing || document.createElement("div");

  if (!existing) {
    block.className = "paragraph-block";
    block.dataset.paragraphId = paragraph.id;
    document.getElementById("transcriptArea").appendChild(block);
  }

  // Ham paragraf verisini (flags dahil) elementin üzerinde saklıyoruz —
  // düzenlemeyi iptal edince veya değişiklik yapılmadan çıkınca backend'e
  // tekrar gitmeden eski render'a hemen dönebilmek için.
  block._paragraphData = paragraph;
  block.innerHTML = `
    <div class="paragraph-text">${buildParagraphHtml(paragraph)}</div>
    <button class="paragraph-delete-btn" title="Paragrafı sil">🗑</button>
  `;
  attachFlagClickHandlers(block, paragraph);
  attachParagraphDeleteHandler(block, paragraph);

  document.getElementById("transcriptArea").scrollTop = 1e9;
}

function buildParagraphHtml(paragraph) {
  const text = paragraph.text;
  const flags = [...paragraph.flags].sort((a, b) => a.position_start - b.position_start);

  let html = "";
  let cursor = 0;
  let markerIndex = 0;

  const plain = (s) => markUncertain(s, paragraph.uncertain_words || []);
  for (const flag of flags) {
    html += plain(text.slice(cursor, flag.position_start));

    if (flag.status === "pending") {
      html += `<span class="flag-badge" data-flag-id="${flag.id}">🚩</span>`; // numara updateStats'ta verilir
    } else {
      const cls = statusToClass(flag.status);
      const shown = flag.final_text || flag.raw_text;
      html += `<span class="resolved-term ${cls}" data-flag-id="${flag.id}">${escapeHtml(shown)}</span>`;
    }
    cursor = flag.position_end;
    markerIndex++;
  }
  html += plain(text.slice(cursor));

  const marker = flags.length ? `<span class="para-marker">${flags.length}</span> ` : "";
  return marker + html;
}

// Whisper'ın emin olmadığı kelimelerin altını soluk çizer (sadece bilgi; tıklanmaz).
function markUncertain(str, words) {
  if (!words.length) return escapeHtml(str);
  const esc = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${words.map(esc).join("|")})(?![\\p{L}\\p{N}])`, "giu");
  let out = "";
  let last = 0;
  for (const m of str.matchAll(re)) {
    out += escapeHtml(str.slice(last, m.index));
    out += `<span class="uncertain-word" title="Whisper bu kelimeden emin değildi">${escapeHtml(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + escapeHtml(str.slice(last));
}

function attachFlagClickHandlers(block, paragraph) {
  block.querySelectorAll("[data-flag-id]").forEach((el) => {
    el.addEventListener("click", async (e) => {
      e.stopPropagation(); // paragrafın düzenleme moduna girmesini engelle
      const flagId = el.dataset.flagId;
      const flag = await apiGet(`/flags/${flagId}`);
      openFlagModal(flag);
    });
  });
}

function attachParagraphDeleteHandler(block, paragraph) {
  const btn = block.querySelector(".paragraph-delete-btn");
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm("Bu paragrafı silmek istediğine emin misin? Bu işlem geri alınamaz.")) return;
    await apiDelete(`/paragraphs/${paragraph.id}`);
    block.remove();
    updateStats();
    if (document.querySelectorAll(".paragraph-block").length === 0) {
      // Son paragraf da silindiyse boş durum mesajını geri getir
      location.reload();
    }
  });
}

// --- Klavye modunda paragrafı yerinde düzenleme ---
// Sadece klavye modu seçiliyken aktif: paragrafın boş bir yerine tıklamak
// (bir flag'e veya sil butonuna değil) onu düzenlenebilir bir metin kutusuna
// çevirir. Enter (Shift+Enter hariç) veya kutudan çıkmak (blur) kaydeder,
// Escape iptal eder.
document.getElementById("transcriptArea").addEventListener("click", (e) => {
  if (!isKeyboardMode()) return;
  if (editingParagraphId !== null) return;
  if (e.target.closest("[data-flag-id]")) return;
  if (e.target.closest(".paragraph-delete-btn")) return;

  const block = e.target.closest(".paragraph-block");
  if (!block) return;

  enterParagraphEditMode(block);
});

function autoResizeTextarea(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = textarea.scrollHeight + "px";
}

function enterParagraphEditMode(block) {
  const paragraph = block._paragraphData;
  if (!paragraph) return;

  editingParagraphId = paragraph.id;
  const originalText = paragraph.text;

  const textarea = document.createElement("textarea");
  textarea.className = "paragraph-edit-textarea";
  textarea.value = originalText;

  block.innerHTML = "";
  block.appendChild(textarea);
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  autoResizeTextarea(textarea);
  textarea.addEventListener("input", () => autoResizeTextarea(textarea));

  let finished = false;
  const finishEdit = async (save) => {
    if (finished) return; // blur + Enter aynı anda iki kez tetiklenmesin
    finished = true;
    editingParagraphId = null;

    if (!save) {
      renderParagraph(paragraph); // değişiklik yok, eski render'a dön
      return;
    }

    const newText = textarea.value.trim();

    if (!newText) {
      if (confirm("Paragraf boş bırakıldı, silinsin mi?")) {
        await apiDelete(`/paragraphs/${paragraph.id}`);
        block.remove();
      } else {
        renderParagraph(paragraph);
      }
      updateStats();
      return;
    }

    if (newText === originalText) {
      renderParagraph(paragraph); // gerçek bir değişiklik yok
      return;
    }

    const updated = await apiPut(`/paragraphs/${paragraph.id}`, { text: newText });
    renderParagraph(updated);
    updateStats();
  };

  textarea.addEventListener("blur", () => finishEdit(true));
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      finishEdit(false);
    } else if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      textarea.blur(); // blur -> finishEdit(true) tetiklenir
    }
  });
}

function openFlagModal(flag) {
  activeFlag = flag;
  document.getElementById("modalFlagIndex").textContent = `#${flag.id}`;
  const resolved = flag.status !== "pending";
  document.getElementById("modalTermText").textContent = resolved
    ? `"${flag.raw_text}" → "${flag.final_text}" olarak çözülmüş. İstersen değiştir ya da geri al.`
    : `"${flag.raw_text}" — kontrol et: yanlış duyulmuş olabilir ya da sözlükte olmayan bir ad.`;
  document.getElementById("reopenCard").style.display = resolved ? "" : "none";
  document.getElementById("rawTermDesc").textContent = `"${flag.raw_text}" kelimesi olduğu gibi kalsın`;
  document.getElementById("suggestionDesc").textContent = flag.suggested_text
    ? `"${flag.suggested_text}" ile değiştir`
    : "Bu terim için AI önerisi yok";

  const suggestionCard = document.querySelector('.action-card[data-action="accept_suggestion"]');
  suggestionCard.style.opacity = flag.suggested_text ? "1" : "0.4";
  suggestionCard.style.pointerEvents = flag.suggested_text ? "auto" : "none";

  document.getElementById("manualInputRow").classList.remove("open");
  document.getElementById("manualInputText").value = "";
  document.getElementById("addToLoreCheck").checked = false;

  document.getElementById("modalOverlay").classList.add("open");
}

function closeFlagModal() {
  document.getElementById("modalOverlay").classList.remove("open");
  activeFlag = null;
}

async function resolveActiveFlag(action, extra) {
  if (!activeFlag) return;
  const body = { action, ...(extra || {}) };
  const updated = await apiPost(`/flags/${activeFlag.id}/resolve`, body);
  closeFlagModal();

  // Paragrafı backend'den TAZE çekip yeniden render ediyoruz — sadece DOM'u
  // yamalamak (patch) yeterli değildi: block._paragraphData (paragraf
  // düzenleme/iptal akışının kullandığı önbellek) güncel kalmıyordu, bu da
  // kullanıcı flag'i çözüp sonra paragrafı düzenleyip iptal ederse, flag'in
  // yanlışlıkla eski (pending/kırmızı) haline dönmesine yol açıyordu.
  const freshParagraph = await apiGet(`/paragraphs/${updated.paragraph_id}`);
  renderParagraph(freshParagraph);
  updateStats();

  // "accept_raw"/"accept_suggestion" otomatik, "manual"+add_to_lore isteğe bağlı
  // olarak Lore Sözlüğü'ne yeni terim ekleyebilir — ama sağ panel bunu otomatik
  // görmüyordu çünkü sadece sayfa ilk açıldığında bir kez yükleniyordu. Flag her
  // çözüldüğünde listeyi tazeliyoruz ki sekmeye geçtiğinde güncel görün.
  if (action !== "cancel" && typeof loadLore === "function") {
    loadLore();
  }
}

function updateStats() {
  const blocks = document.querySelectorAll(".paragraph-block");
  const flagBadges = document.querySelectorAll(".flag-badge");
  // Numaralar her seferinde sayfadaki sıraya göre baştan verilir. Eskiden bir sayaç her yeniden
  // çizimde (flag çözme, dikte ekleme, düzenleme iptali) artıyordu: 12 flag "🚩 117" görünüyordu.
  flagBadges.forEach((b, i) => (b.textContent = `🚩 ${i + 1}`));
  document.getElementById("paraCount").textContent = `${blocks.length} paragraf`;

  // Sadece ".paragraph-text" içeriğini sayıyoruz — sil butonu (🗑) veya
  // düzenleme kutusu (textarea) yanlışlıkla kelime sayısına dahil olmasın.
  const wordCount = [...document.querySelectorAll(".paragraph-text")].reduce(
    (sum, el) => sum + el.innerText.trim().split(/\s+/).filter(Boolean).length,
    0
  );
  document.getElementById("wordCount").textContent = `${wordCount} kelime`;
  document.getElementById("flagCount").textContent = `${flagBadges.length} flag`;
}

// --- Modal event listener'ları ---
document.getElementById("modalCloseBtn").addEventListener("click", closeFlagModal);
document.getElementById("modalOverlay").addEventListener("click", (e) => {
  if (e.target.id === "modalOverlay") closeFlagModal();
});

document.querySelectorAll(".action-card").forEach((card) => {
  card.addEventListener("click", () => {
    const action = card.dataset.action;
    if (action === "manual") {
      document.getElementById("manualInputRow").classList.add("open");
      document.getElementById("manualInputText").focus();
      return;
    }
    resolveActiveFlag(action);
  });
});

document.getElementById("manualConfirmBtn").addEventListener("click", () => {
  const text = document.getElementById("manualInputText").value.trim();
  if (!text) return;
  const addToLore = document.getElementById("addToLoreCheck").checked;
  resolveActiveFlag("manual", { manual_text: text, add_to_lore: addToLore });
});