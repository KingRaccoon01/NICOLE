// Nicole — Dikte akışı (mikrofon + WebSocket ile Whisper, ya da klavye modu)

const Dictation = {
  ws: null,
  mediaRecorder: null,
  stream: null,
  recording: false,
  starting: false, // mikrofon açılırken (izin/cihaz bekleniyor) true — çift başlatmayı engeller
  timerInterval: null,
  seconds: 0,
  currentParagraphId: null, // dolu ise, bir sonraki transkript bu paragrafın SONUNA eklenir
};

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// --- Mikrofon listesi ---
// ÖNEMLİ: Eskiden her yenilemede mikrofon açılıp kapatılıyordu (etiketleri görmek için) ve her
// "devicechange" olayı listeyi tekrar yeniliyordu. Bluetooth kulaklıklar mikrofon açılınca
// profil değiştirip (müzik <-> konuşma) kendileri "devicechange" tetiklediği için bu sonsuz bir
// döngü oluşturuyordu (Console'da 5-7-5-7 cihaz, kulaklık adı bir görünüp bir kayboluyordu).
// Artık: mikrofonu SADECE etiketler gizliyse ve sayfa açılışında/kullanıcı tıklayınca bir kez
// açıyoruz; "devicechange"de asla açmıyoruz, liste değişmediyse hiçbir şey yapmıyoruz.
const AudioDevices = { signature: "", busy: false, debounce: null };

function cleanDeviceLabel(text) {
  return text.replace(/\s*\(bağlı değil\)\s*$/, "");
}

function rebuildAudioSelect(select, inputs) {
  const savedId = NicoleSettings.get("audioDeviceId");
  const savedLabel = NicoleSettings.get("audioDeviceLabel");

  select.innerHTML = "";
  inputs.forEach((d, i) => {
    const opt = document.createElement("option");
    opt.value = d.deviceId;
    opt.textContent = d.label || `Mikrofon ${i + 1}`;
    select.appendChild(opt);
  });
  if (inputs.length === 0) {
    select.innerHTML = `<option value="">Mikrofon bulunamadı</option>`;
    return;
  }

  // Kayıtlı mikrofonu geri seç: önce kimliğiyle, bulunamazsa ismiyle (kimlik değişmiş olabilir)
  let chosen = savedId ? inputs.find((d) => d.deviceId === savedId) : null;
  if (!chosen && savedLabel) {
    chosen = inputs.find((d) => d.label && d.label === savedLabel);
    if (chosen) NicoleSettings.set("audioDeviceId", chosen.deviceId);
  }
  if (chosen) {
    select.value = chosen.deviceId;
    return;
  }
  if (savedId) {
    // Kayıtlı mikrofon şu an bağlı değil (örn. Bluetooth kulaklık kapalı) — seçimi kaybetme
    const opt = document.createElement("option");
    opt.value = savedId;
    opt.textContent = `${savedLabel || "Kayıtlı mikrofon"} (bağlı değil)`;
    select.appendChild(opt);
    select.value = savedId;
  }
}

async function populateAudioSources({ askPermission = false, force = false } = {}) {
  if (AudioDevices.busy) return;
  AudioDevices.busy = true;
  const select = document.getElementById("audioSourceSelect");
  try {
    let devices = await navigator.mediaDevices.enumerateDevices();
    let inputs = devices.filter((d) => d.kind === "audioinput");

    // Cihaz isimleri, mikrofon izni verilmeden boş gelir. Sadece o durumda (ve sadece biz
    // istediğimizde) mikrofonu kısa süre açıp kapatarak izin alıyoruz.
    if (askPermission && inputs.length > 0 && inputs.every((d) => !d.label)) {
      const testStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      testStream.getTracks().forEach((t) => t.stop());
      devices = await navigator.mediaDevices.enumerateDevices();
      inputs = devices.filter((d) => d.kind === "audioinput");
    }

    const signature = inputs.map((d) => `${d.deviceId}|${d.label}`).join("\n");
    if (!force && signature === AudioDevices.signature && select.options.length > 0) return; // değişiklik yok
    AudioDevices.signature = signature;

    // Teşhis: F12 > Console'da ses giriş cihazları (sadece liste DEĞİŞTİĞİNDE yazılır)
    console.log(`[Nicole] Ses giriş cihazları (${inputs.length}):`, inputs.map((d) => d.label || "(etiketsiz)"));
    rebuildAudioSelect(select, inputs);
  } catch (err) {
    console.warn("Mikrofon listesi alınamadı:", err);
    let msg = "İzin verilmedi — tıkla, tekrar dene";
    if (err.name === "NotFoundError") msg = "Mikrofon bulunamadı";
    else if (err.name === "NotAllowedError" || err.name === "SecurityError") msg = "İzin verilmedi — tıkla, tekrar dene";
    select.innerHTML = `<option value="">${msg}</option>`;
    AudioDevices.signature = "";
  } finally {
    AudioDevices.busy = false;
  }
}

// Seçimi hatırla: sayfa yenilenince aynı mikrofon seçili gelir
document.getElementById("audioSourceSelect").addEventListener("change", (e) => {
  const select = e.target;
  const opt = select.selectedOptions[0];
  NicoleSettings.set("audioDeviceId", select.value);
  NicoleSettings.set("audioDeviceLabel", opt ? cleanDeviceLabel(opt.textContent) : "");
});

// Dropdown açılırken liste değiştiyse tazele (değişmediyse seçenekler yeniden çizilmez)
document.getElementById("audioSourceSelect").addEventListener("mousedown", () => {
  populateAudioSources({ askPermission: true });
});

document.getElementById("refreshAudioBtn")?.addEventListener("click", () => {
  populateAudioSources({ askPermission: true, force: true });
});

// Bluetooth kulaklık gibi cihazlar sayfa açıkken bağlanıp ayrılabilir. Olaylar sık ve art arda
// geldiği için biraz bekleyip TEK SEFER yeniliyoruz — ve mikrofonu açmadan (bkz. yukarıdaki not).
if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
  navigator.mediaDevices.addEventListener("devicechange", () => {
    clearTimeout(AudioDevices.debounce);
    AudioDevices.debounce = setTimeout(() => populateAudioSources(), 600);
  });
}

function connectDictationSocket(chapterId) {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const engine = NicoleSettings.get("flagEngine");
  const query = engine ? `?flag_engine=${encodeURIComponent(engine)}` : "";
  Dictation.ws = new WebSocket(`${protocol}://${location.host}/ws/dictate/${chapterId}${query}`);

  Dictation.ws.onopen = () => setConnStatus(true);
  Dictation.ws.onclose = () => setConnStatus(false);
  Dictation.ws.onerror = () => setConnStatus(false);

  Dictation.ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "paragraph") {
      renderParagraph(msg.data);
      updateStats();
      if (msg.data.warning) setRecordStatus(`⚠ ${msg.data.warning}`, true);
      else setIdleStatus();
      // Bir sonraki "kayıt durdur" da (kullanıcı "Yeni Paragraf" demediği
      // sürece) bu paragrafın devamı olarak eklensin.
      Dictation.currentParagraphId = msg.data.id;
    } else if (msg.type === "error") {
      showDictationError(msg.message);
    } else if (msg.type === "status") {
      setRecordStatus(msg.message, false);
    }
  };
}

// Boştaki durum yazısı: kısayol ayarına göre ("tıkla veya Space" / "basılı tut: Space")
function setIdleStatus(prefix = "Hazır") {
  setRecordStatus(`${prefix} · ${hotkeyHint("dictationKey", "dictationHold")}`, false);
}

function setRecordStatus(text, isError) {
  const el = document.getElementById("recordStatusText");
  el.textContent = text;
  el.style.color = isError ? "var(--vermil)" : "";
}

function showDictationError(message) {
  setRecordStatus("⚠ " + message, true);
  setTimeout(() => { if (!Dictation.recording) setIdleStatus(); }, 8000);
}

function setConnStatus(connected) {
  const dot = document.getElementById("connDot");
  const text = document.getElementById("connText");
  dot.classList.toggle("connected", connected);
  text.textContent = connected ? "bağlandı" : "bağlantı yok";
}

async function toggleRecording() {
  const mode = document.getElementById("inputModeSelect").value;
  if (mode === "keyboard") {
    alert("Klavye modundasın — dikte için önce üstteki dropdown'dan 'Whisper (Sesli)' seç.");
    return;
  }

  if (Dictation.starting) return;
  if (Dictation.recording) {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  if (Dictation.recording || Dictation.starting) return;
  if (typeof ChatMic !== "undefined" && (ChatMic.recording || ChatMic.starting)) {
    showDictationError("Nicole'nin mikrofonu açık — önce onu durdur.");
    return;
  }

  Dictation.starting = true;
  try {
    Dictation.stream = await getMicStream(
      document.getElementById("audioSourceSelect").value,
      (msg) => setRecordStatus(msg, false)
    );
  } catch (err) {
    Dictation.starting = false;
    showDictationError(`Mikrofona erişilemedi (${err.name || "hata"}) — tarayıcı izinlerini kontrol et.`);
    return;
  }

  const preferredMime = "audio/webm;codecs=opus";
  const mimeType = MediaRecorder.isTypeSupported(preferredMime) ? preferredMime : "";
  Dictation.mediaRecorder = mimeType
    ? new MediaRecorder(Dictation.stream, { mimeType })
    : new MediaRecorder(Dictation.stream);

  let pendingSends = 0;

  Dictation.mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0 && Dictation.ws && Dictation.ws.readyState === WebSocket.OPEN) {
      pendingSends++;
      e.data.arrayBuffer().then((buf) => {
        Dictation.ws.send(buf);
        pendingSends--;
      });
    }
  };

  Dictation.mediaRecorder.onstop = () => {
    // Tarayıcının son ses parçasını (dataavailable) gönderdiğinden emin olmadan
    // sinyal göndermek yarım/bozuk veri yüzünden Whisper'da "Invalid data
    // found" hatasına yol açabiliyordu — bu yüzden son parçanın WebSocket'e
    // gönderilmesini burada bekliyoruz.
    const waitAndSignal = () => {
      if (pendingSends > 0) {
        setTimeout(waitAndSignal, 30);
        return;
      }
      if (Dictation.ws && Dictation.ws.readyState === WebSocket.OPEN) {
        // append_to doluysa, transkript YENİ bir paragraf açmak yerine mevcut
        // paragrafın SONUNA eklenir — kullanıcı "Yeni Paragraf" demediği sürece
        // ardışık kayıtlar aynı paragrafın devamı olarak birikir.
        Dictation.ws.send(JSON.stringify({ type: "end_utterance", append_to: Dictation.currentParagraphId }));
        setRecordStatus("Kayıt durduruldu, ses gönderiliyor...", false);
      }
    };
    waitAndSignal();
  };

  // Her 250ms'de bir chunk gönder — düşük gecikmeli "canlı" his için
  Dictation.mediaRecorder.start(250);
  Dictation.recording = true;
  Dictation.starting = false;

  const btn = document.getElementById("recordBtn");
  btn.classList.add("recording");
  setRecordStatus(
    NicoleSettings.get("dictationHold")
      ? "Kaydediliyor... (tuşu bırakınca durur)"
      : `Kaydediliyor... (durdurmak için tıkla veya ${keyLabel(NicoleSettings.get("dictationKey"))})`,
    false
  );

  Dictation.seconds = 0;
  Dictation.timerInterval = setInterval(() => {
    Dictation.seconds++;
    document.getElementById("recordTime").textContent = fmtTime(Dictation.seconds);
  }, 1000);
}

function stopRecording() {
  if (Dictation.mediaRecorder && Dictation.mediaRecorder.state !== "inactive") {
    Dictation.mediaRecorder.stop(); // __END_UTTERANCE__ artık onstop içinde gönderiliyor
  }
  if (Dictation.stream) {
    Dictation.stream.getTracks().forEach((t) => t.stop());
  }

  Dictation.recording = false;
  clearInterval(Dictation.timerInterval);

  const btn = document.getElementById("recordBtn");
  btn.classList.remove("recording");
}

// --- Klavye modu: manuel paragraf ekleme ---
async function addManualParagraph(text) {
  if (!NicoleState.chapterId || !text.trim()) return;
  const paragraph = await apiPost(`/chapters/${NicoleState.chapterId}/paragraphs`, { text });
  renderParagraph(paragraph);
  updateStats();
}

document.getElementById("recordBtn").addEventListener("click", toggleRecording);

// Kısayol: varsayılan Space (Ayarlardan değiştirilebilir), bas-çek ya da basılı tut.
attachHotkey({
  getCode: () => NicoleSettings.get("dictationKey"),
  isHold: () => NicoleSettings.get("dictationHold"),
  enabled: () => document.getElementById("inputModeSelect").value !== "keyboard",
  isRecording: () => Dictation.recording,
  isStarting: () => Dictation.starting,
  start: startRecording,
  stop: stopRecording,
});

// Kısayol tuşu / mod değişince boştaki durum yazısını güncelle
// Flag motoru soket açılırken bildirilir — ayar değişince soketi yeni ayarla yeniden aç
document.getElementById("flagEngineSelect")?.addEventListener("change", () => {
  if (!Dictation.ws || !NicoleState.chapterId) return;
  Dictation.ws.onclose = null;
  Dictation.ws.close();
  connectDictationSocket(NicoleState.chapterId);
});

bindKeyButton("dictationKeyBtn", "dictationKey", ["chatKey"], () => { if (!Dictation.recording) setIdleStatus(); });
document.getElementById("dictationHold")?.addEventListener("change", () => { if (!Dictation.recording) setIdleStatus(); });

document.getElementById("inputModeSelect").addEventListener("change", (e) => {
  const isKeyboard = e.target.value === "keyboard";
  document.getElementById("recordBtn").disabled = isKeyboard;
  document.getElementById("audioSourceSelect").disabled = isKeyboard;
  document.getElementById("keyboardInputRow").style.display = isKeyboard ? "flex" : "none";
  // "keyboard-mode" class'ı, dikte akışındaki mevcut paragrafların tıklanıp
  // yerinde düzenlenebilir/silinebilir olmasını (flags.js) ve CSS'te bunun
  // görsel ipucunu (hover efekti, sil butonu) göstermesini sağlar.
  document.body.classList.toggle("keyboard-mode", isKeyboard);
  if (isKeyboard) document.getElementById("keyboardTextarea").focus();
});

// Sayfa ilk açıldığında seçili moda göre klavye kutusunu göster/gizle
// (Kayıtlı giriş modu settings.js tarafından zaten yüklendi — butonların açık/kapalı durumunu da uygula.)
const _initialIsKeyboard = document.getElementById("inputModeSelect").value === "keyboard";
document.getElementById("keyboardInputRow").style.display = _initialIsKeyboard ? "flex" : "none";
document.body.classList.toggle("keyboard-mode", _initialIsKeyboard);
document.getElementById("recordBtn").disabled = _initialIsKeyboard;
document.getElementById("audioSourceSelect").disabled = _initialIsKeyboard;

document.getElementById("keyboardSendBtn").addEventListener("click", sendKeyboardParagraph);
document.getElementById("keyboardTextarea").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendKeyboardParagraph();
  }
});

function sendKeyboardParagraph() {
  const ta = document.getElementById("keyboardTextarea");
  const text = ta.value.trim();
  if (!text) return;
  addManualParagraph(text);
  ta.value = "";
  ta.focus();
}

// --- "Yeni Paragraf" — kullanıcı kontrolünde paragraf sınırı ---
// Varsayılan davranış: mikrofonu kaç kez aç/kapatırsan kapat, transkript hep
// AYNI paragrafın devamı olarak birikir (doğal konuşma akışı). Kullanıcı
// gerçekten yeni bir paragrafa geçmek istediğinde bu butona basar/kısayolu
// kullanır — bir sonraki kayıt YENİ bir paragraf olarak başlar.
function startNewParagraph() {
  Dictation.currentParagraphId = null;
  setIdleStatus("Yeni paragraf başlayacak");
}

document.getElementById("newParagraphBtn")?.addEventListener("click", startNewParagraph);

setIdleStatus();
populateAudioSources({ askPermission: true });