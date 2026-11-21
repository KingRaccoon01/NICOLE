// Nicole — AI Editör Sohbeti (sağ panel, Gemini destekli)

const ChatState = { sending: false };

async function loadChatHistory() {
  if (!NicoleState.projectId) return;
  const messages = await apiGet(`/projects/${NicoleState.projectId}/chat`);
  const container = document.getElementById("chatMessages");
  container.innerHTML = "";
  messages.forEach((m) => renderChatMessage(m));
}

// Nicole Editör metni değiştirdiğinde (veya geri aldığında) sol paneli sıfırdan çizer.
async function reloadTranscript() {
  if (!NicoleState.chapterId) return;
  document.querySelectorAll(".paragraph-block").forEach((b) => b.remove());
  Dictation.currentParagraphId = null; // dikte, silinmiş/değişmiş bir paragrafın sonuna eklemeye çalışmasın
  const paragraphs = await apiGet(`/chapters/${NicoleState.chapterId}/paragraphs`);
  if (paragraphs.length === 0) {
    location.reload(); // boş durum ekranını geri getirmenin en basit yolu
    return;
  }
  paragraphs.forEach(renderParagraph);
  updateStats();
}

function snippet(text, max = 110) {
  const t = (text || "").replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max) + "…" : t;
}

// Editör'ün bir mesajda yaptığı düzenlemeleri gösteren kart + "Geri al" butonu.
function buildEditsCard(msg) {
  const card = document.createElement("div");
  card.className = "edit-card";

  const header = document.createElement("div");
  header.className = "edit-card-header";
  card.appendChild(header);

  const labels = { replace: "✎ Değiştirildi", insert_after: "＋ Eklendi", delete: "🗑 Silindi" };
  msg.edits.forEach((e) => {
    const row = document.createElement("div");
    row.className = "edit-row";
    const label = document.createElement("span");
    label.className = "edit-label";
    label.textContent = labels[e.action] || e.action;
    const text = document.createElement("span");
    text.className = "edit-text";
    text.textContent = e.action === "delete" ? snippet(e.before_text) : snippet(e.after_text);
    row.append(label, text);
    card.appendChild(row);
  });

  const undoBtn = document.createElement("button");
  undoBtn.className = "edit-undo-btn";
  card.appendChild(undoBtn);

  const refreshHeader = (allUndone) => {
    header.textContent = allUndone
      ? `↩ Nicole'nin ${msg.edits.length} düzenlemesi geri alındı`
      : `Nicole metne ${msg.edits.length} düzenleme uyguladı`;
    undoBtn.style.display = allUndone ? "none" : "";
    undoBtn.textContent = "↩ Geri al";
  };
  refreshHeader(msg.edits.every((e) => e.undone));

  undoBtn.addEventListener("click", async () => {
    undoBtn.disabled = true;
    undoBtn.textContent = "Geri alınıyor...";
    try {
      const res = await apiPost(`/projects/${NicoleState.projectId}/chat/${msg.id}/undo`);
      await reloadTranscript();
      if (res.errors && res.errors.length) {
        header.textContent = `⚠ Kısmen geri alındı: ${res.errors.join(" ")}`;
        undoBtn.style.display = "none";
      } else {
        refreshHeader(true);
      }
    } catch (err) {
      header.textContent = `⚠ Geri alınamadı: ${err.message}`;
      undoBtn.disabled = false;
      undoBtn.textContent = "↩ Tekrar dene";
    }
  });
  return card;
}

function renderChatMessage(msg, opts = {}) {
  const container = document.getElementById("chatMessages");
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${msg.role === "user" ? "user" : "assistant"}${opts.pending ? " pending" : ""}`;

  const body = document.createElement("div");
  body.textContent = msg.content;
  bubble.appendChild(body);

  if (msg.edits && msg.edits.length) {
    bubble.appendChild(buildEditsCard(msg));
  }
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

async function sendChatMessage() {
  const input = document.getElementById("chatInput");
  const text = input.value.trim();
  if (!text || !NicoleState.projectId) return;
  if (ChatState.sending) return; // cevap beklerken ikinci gönderimi engelle

  renderChatMessage({ role: "user", content: text });
  input.value = "";

  const includeContext = document.getElementById("includeContext").checked;
  const allowEdits = document.getElementById("allowEdits")?.checked || false;
  const thinking = renderChatMessage({ role: "assistant", content: "Nicole düşünüyor…" }, { pending: true });

  ChatState.sending = true;
  try {
    const reply = await apiPost(`/projects/${NicoleState.projectId}/chat`, {
      content: text,
      include_context: includeContext,
      allow_edits: allowEdits,
    });
    thinking.remove();
    renderChatMessage(reply);
    if (reply.id === 0 && !input.value) input.value = text; // hata: mesaj kaydedilmedi, tekrar denemek için geri koy
    if (reply.edits && reply.edits.length) {
      await reloadTranscript(); // Editör metne dokunduysa sol panel hemen güncellensin
    }
  } catch (err) {
    thinking.remove();
    renderChatMessage({ role: "assistant", content: `⚠ Mesaj gönderilemedi: ${err.message}` });
  } finally {
    ChatState.sending = false;
  }
}

document.getElementById("chatSendBtn").addEventListener("click", sendChatMessage);
document.getElementById("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendChatMessage();
});
document.getElementById("clearChatBtn").addEventListener("click", async (e) => {
  e.preventDefault();
  if (!NicoleState.projectId) return;
  await apiDelete(`/projects/${NicoleState.projectId}/chat`);
  document.getElementById("chatMessages").innerHTML = "";
});

// --- Kutucuk ayarları (bağlam, düzenleme izni, "sesle konuşunca direkt gönder") ---
// Hepsi settings.js tarafından localStorage'a kaydedilir ve sayfa yenilenince geri yüklenir.
// "Sesle direkt gönder" kapalıyken (varsayılan): mikrofonla söylenen metin chatInput'a yazılır,
// kullanıcı isterse düzenler, Enter'la kendi gönderir. Açıkken: transkript hazır olur olmaz
// otomatik olarak Gemini'ye gönderilir.

// --- Nicole Editör'e mikrofonla mesaj yazma ---
// Dikte akışındaki (dictation.js) WebSocket + flag sistemine hiç dokunmuyor —
// tek yaptığı, kısa bir ses kaydını alıp /api/transcribe'a gönderip dönen
// metni sohbet kutusuna yazmak (ya da "sesle direkt gönder" açıksa otomatik
// göndermek). Kullanıcı isterse düzenler, Enter'la gönderir.
//
// Her adımda görünür bir durum yazısı (#chatMicStatus) güncellenir, dikte
// panelindeki "Ses kaynağı" dropdown'ında seçili AYNI mikrofon kullanılır,
// ve F12 Console'da her adım loglanır.
const ChatMic = {
  mediaRecorder: null,
  stream: null,
  recording: false,
  starting: false, // mikrofon açılırken true
  busy: false,     // kayıt bitti, ses metne çevriliyor (bu sırada yeni kayıt başlatılmaz)
  chunks: [],
  modelLoaded: false, // Whisper bellekte mi? (true ise "model yükleniyor" uyarısı gösterilmez)
};

// Whisper modeli bellekte mi diye sunucuya sorar. Başarısız olursa sessizce geçer.
async function refreshWhisperStatus() {
  try {
    const res = await fetch(`${API_BASE}/transcribe/status`);
    if (res.ok) ChatMic.modelLoaded = !!(await res.json()).loaded;
  } catch (_) { /* önemli değil — sadece uyarı metnini etkiler */ }
}
refreshWhisperStatus();

function setChatMicStatus(text, kind) {
  const el = document.getElementById("chatMicStatus");
  if (!el) return;
  el.textContent = text || "";
  el.classList.remove("error", "active");
  if (kind) el.classList.add(kind);
}

async function toggleChatMic() {
  if (ChatMic.starting || ChatMic.busy) return;
  if (ChatMic.recording) {
    stopChatRecording();
  } else {
    await startChatRecording();
  }
}

async function startChatRecording() {
  if (ChatMic.recording || ChatMic.starting || ChatMic.busy) return;
  if (typeof Dictation !== "undefined" && (Dictation.recording || Dictation.starting)) {
    setChatMicStatus("⚠ Dikte mikrofonu açık — önce dikteyi durdur.", "error");
    return;
  }
  const btn = document.getElementById("chatMicBtn");

  const selectedDeviceId = document.getElementById("audioSourceSelect")?.value || "";
  console.log("[Nicole/ChatMic] Kayıt başlatılıyor, kullanılan cihaz id:", selectedDeviceId || "(tarayıcı varsayılanı)");

  refreshWhisperStatus(); // beklemeden: kayıt bitene kadar cevap gelmiş olur
  setChatMicStatus("🎙 Mikrofon izni isteniyor...", "active");
  ChatMic.starting = true;
  try {
    ChatMic.stream = await getMicStream(selectedDeviceId, (msg) => setChatMicStatus(msg, "active"));
  } catch (err) {
    ChatMic.starting = false;
    console.error("[Nicole/ChatMic] Mikrofon izni alınamadı:", err);
    setChatMicStatus(`⚠ Mikrofona erişilemedi (${err.name}) — tarayıcı izinlerini kontrol et.`, "error");
    return;
  }
  console.log("[Nicole/ChatMic] Mikrofon akışı alındı, track sayısı:", ChatMic.stream.getAudioTracks().length);
  console.log("[Nicole/ChatMic] Kullanılan gerçek cihaz:", ChatMic.stream.getAudioTracks()[0]?.label || "(etiketsiz)");

  const preferredMime = "audio/webm;codecs=opus";
  const mimeType = MediaRecorder.isTypeSupported(preferredMime) ? preferredMime : "";
  ChatMic.mediaRecorder = mimeType
    ? new MediaRecorder(ChatMic.stream, { mimeType })
    : new MediaRecorder(ChatMic.stream);

  ChatMic.chunks = [];
  ChatMic.mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) ChatMic.chunks.push(e.data);
    console.log("[Nicole/ChatMic] Ses parçası alındı, boyut:", e.data.size, "bayt");
  };

  ChatMic.mediaRecorder.onstop = async () => {
    const blob = new Blob(ChatMic.chunks, { type: ChatMic.mediaRecorder.mimeType || "audio/webm" });
    console.log("[Nicole/ChatMic] Kayıt durdu, toplam ses boyutu:", blob.size, "bayt");
    ChatMic.chunks = [];
    if (ChatMic.stream) ChatMic.stream.getTracks().forEach((t) => t.stop());

    btn.classList.remove("recording");

    if (blob.size === 0) {
      console.warn("[Nicole/ChatMic] Ses boş geldi — hiç konuşulmamış ya da mikrofon veri vermemiş olabilir.");
      setChatMicStatus("⚠ Ses algılanamadı (boş kayıt) — mikrofonu kontrol et.", "error");
      return;
    }

    btn.disabled = true;
    ChatMic.busy = true;
    setChatMicStatus(
      ChatMic.modelLoaded
        ? "⏳ Ses metne çevriliyor..."
        : "⏳ Whisper modeli ilk kez yükleniyor (sunucu açıldıktan sonra bir kez olur, uzun sürebilir)...",
      "active"
    );

    try {
      const formData = new FormData();
      formData.append("file", blob, "chat_audio.webm");
      // Hangi romanın Lore terimlerinin Whisper'a ipucu olarak verileceğini söyler (özel isimler daha doğru tanınır)
      if (NicoleState.projectId) formData.append("project_id", NicoleState.projectId);
      console.log("[Nicole/ChatMic] /api/transcribe isteği gönderiliyor...");
      const res = await fetch(`${API_BASE}/transcribe`, { method: "POST", body: formData });
      console.log("[Nicole/ChatMic] Yanıt durum kodu:", res.status);
      if (!res.ok) {
        const errText = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status} — ${errText.slice(0, 200)}`);
      }
      const data = await res.json();
      ChatMic.modelLoaded = true; // artık bellekte — sonraki konuşmalarda uyarı çıkmaz
      console.log("[Nicole/ChatMic] Dönen metin:", data.text);
      const newText = (data.text || "").trim();
      const input = document.getElementById("chatInput");

      if (newText) {
        input.value = input.value ? `${input.value} ${newText}` : newText;

        const autoSend = document.getElementById("autoSendVoice")?.checked;
        if (autoSend) {
          setChatMicStatus(`✓ Gönderildi: "${newText.slice(0, 40)}${newText.length > 40 ? "..." : ""}"`, null);
          console.log("[Nicole/ChatMic] 'Sesle direkt gönder' açık — mesaj otomatik gönderiliyor.");
          await sendChatMessage();
        } else {
          setChatMicStatus(`✓ Metne çevrildi: "${newText.slice(0, 40)}${newText.length > 40 ? "..." : ""}"`, null);
        }
      } else {
        setChatMicStatus("⚠ Ses metne çevrildi ama sonuç boş çıktı — daha net konuşmayı dene.", "error");
      }
      input.focus();
    } catch (err) {
      console.error("[Nicole/ChatMic] Transkripsiyon hatası:", err);
      setChatMicStatus(`⚠ Ses metne çevrilemedi: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
      ChatMic.busy = false;
    }
  };

  ChatMic.mediaRecorder.start();
  ChatMic.recording = true;
  ChatMic.starting = false;
  btn.classList.add("recording");
  setChatMicStatus(
    NicoleSettings.get("chatHold")
      ? "🔴 Kaydediliyor... (tuşu bırakınca durur)"
      : `🔴 Kaydediliyor... (durdurmak için tekrar tıkla veya ${keyLabel(NicoleSettings.get("chatKey"))})`,
    "active"
  );
  console.log("[Nicole/ChatMic] Kayıt başladı.");
}

function stopChatRecording() {
  console.log("[Nicole/ChatMic] Kayıt durduruluyor...");
  if (ChatMic.mediaRecorder && ChatMic.mediaRecorder.state !== "inactive") {
    ChatMic.mediaRecorder.stop();
  }
  ChatMic.recording = false;
}

document.getElementById("chatMicBtn")?.addEventListener("click", toggleChatMic);

// --- Kısayol: varsayılan N (Ayarlardan değiştirilebilir), bas-çek ya da basılı tut ---
attachHotkey({
  getCode: () => NicoleSettings.get("chatKey"),
  isHold: () => NicoleSettings.get("chatHold"),
  enabled: () => !!document.getElementById("chatMicBtn") && !document.getElementById("chatMicBtn").disabled,
  isRecording: () => ChatMic.recording,
  isStarting: () => ChatMic.starting || ChatMic.busy,
  start: startChatRecording,
  stop: stopChatRecording,
});

bindKeyButton("chatKeyBtn", "chatKey", ["dictationKey"], () => {
  const btn = document.getElementById("chatMicBtn");
  if (btn) btn.title = `Sesle yaz (${keyLabel(NicoleSettings.get("chatKey"))})`;
});
{
  const btn = document.getElementById("chatMicBtn");
  if (btn) btn.title = `Sesle yaz (${keyLabel(NicoleSettings.get("chatKey"))})`;
}