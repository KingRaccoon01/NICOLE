// Nicole — Kalıcı ayarlar + kısayol tuşları + mikrofon yardımcıları
//
// Sayfadaki tüm kutucuklar/seçimler (bağlam, düzenleme izni, sesle direkt gönder, giriş modu,
// seçili mikrofon, kısayol tuşları ve "basılı tut" seçenekleri) tarayıcıda (localStorage)
// saklanır; sayfa yenilense de geri gelir. Bu dosya dictation.js ve editor_chat.js'ten ÖNCE yüklenir.

const NicoleSettings = (() => {
  const STORAGE_KEY = "nicole_settings_v1";
  const DEFAULTS = {
    includeContext: true,   // Nicole Editör: hikâye bağlamını ekle
    allowEdits: false,      // Nicole Editör: metne doğrudan müdahale izni
    autoSendVoice: false,   // Nicole Editör: sesle konuşunca direkt gönder
    inputMode: "whisper",   // Dikte giriş modu: "whisper" | "keyboard"
    audioDeviceId: "",      // Seçili mikrofon
    audioDeviceLabel: "",   // (cihaz kimliği değişirse ismiyle bulmak için)
    dictationKey: "Space",  // Dikte kayıt tuşu
    dictationHold: false,   // true: basılı tut-konuş, false: bas-çek (aç/kapa)
    chatKey: "KeyN",        // Nicole Editör mikrofon tuşu
    chatHold: false,
    flagEngine: "",         // Flag taraması: "" (.env), "ollama" | "gemini"
  };
  const data = { ...DEFAULTS };

  function load() {
    let stored = {};
    try { stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") || {}; } catch (_) { /* bozuksa varsayılanlar */ }
    Object.keys(DEFAULTS).forEach((k) => {
      if (k in stored && typeof stored[k] === typeof DEFAULTS[k]) data[k] = stored[k];
    });
    // Eski sürümde sadece "sesle direkt gönder" ayrı bir anahtarda saklanıyordu — oradan taşı.
    if (!("autoSendVoice" in stored)) {
      const legacy = localStorage.getItem("nicole_chat_auto_send_voice");
      if (legacy !== null) data.autoSendVoice = legacy === "true";
    }
  }

  function save() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch (_) { /* depolama kapalıysa sessizce geç */ }
  }

  load();
  return {
    get: (key) => data[key],
    set: (key, value) => { data[key] = value; save(); },
    capturingKey: false, // bir kısayol tuşu atanırken true — o sırada kısayollar devre dışı
  };
})();

// Kutucuğu (checkbox) ya da seçim kutusunu (select) bir ayara bağlar: açılışta kayıtlı değeri
// yükler, her değişiklikte kaydeder.
function bindSetting(elementId, key) {
  const el = document.getElementById(elementId);
  if (!el) return null;
  if (el.type === "checkbox") {
    el.checked = !!NicoleSettings.get(key);
    el.addEventListener("change", () => NicoleSettings.set(key, el.checked));
  } else {
    const saved = NicoleSettings.get(key);
    if (saved && [...el.options].some((o) => o.value === saved)) el.value = saved;
    el.addEventListener("change", () => NicoleSettings.set(key, el.value));
  }
  return el;
}

// --- Tuş etiketleri / atama ---
function keyLabel(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit\d$/.test(code)) return code.slice(5);
  if (/^Numpad\d$/.test(code)) return "Num " + code.slice(6);
  const names = {
    ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", Backquote: "`", Minus: "-",
    Equal: "=", BracketLeft: "[", BracketRight: "]", Semicolon: ";", Quote: "'", Comma: ",",
    Period: ".", Slash: "/", Backslash: "\\", CapsLock: "Caps Lock", NumpadAdd: "Num +",
    NumpadSubtract: "Num -", NumpadMultiply: "Num *", NumpadDivide: "Num /", NumpadEnter: "Num Enter",
  };
  return names[code] || code;
}

const _MODIFIER_CODES = ["ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight"];
const _RESERVED_CODES = ["Tab", "Enter"]; // sayfada gezinme/gönderme için ayrılmış, atanamaz

// "Tuşu değiştir" düğmesi: tıkla, sonra yeni tuşa bas. Esc iptal eder. Aynı tuş iki kısayola atanamaz.
function bindKeyButton(buttonId, settingKey, otherSettingKeys, onChange) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  const render = () => { btn.textContent = keyLabel(NicoleSettings.get(settingKey)); btn.classList.remove("capturing"); };
  render();

  btn.addEventListener("click", () => {
    if (NicoleSettings.capturingKey) return;
    NicoleSettings.capturingKey = true;
    btn.textContent = "Bir tuşa bas… (Esc: iptal)";
    btn.classList.add("capturing");

    const finish = () => {
      NicoleSettings.capturingKey = false;
      window.removeEventListener("keydown", onKey, true);
      btn.removeEventListener("blur", onBlur);
    };
    const onBlur = () => { finish(); render(); };
    const onKey = (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (_MODIFIER_CODES.includes(e.code)) return; // sadece Shift/Ctrl/Alt'a basıldı, asıl tuşu bekle
      finish();
      if (e.code === "Escape") { render(); return; }
      if (_RESERVED_CODES.includes(e.code)) {
        btn.textContent = `${keyLabel(e.code)} atanamaz`;
        setTimeout(render, 1200);
        return;
      }
      if (otherSettingKeys.some((k) => NicoleSettings.get(k) === e.code)) {
        btn.textContent = `${keyLabel(e.code)} başka kısayolda`;
        setTimeout(render, 1400);
        return;
      }
      NicoleSettings.set(settingKey, e.code);
      render();
      if (onChange) onChange();
    };
    window.addEventListener("keydown", onKey, true);
    btn.addEventListener("blur", onBlur);
  });
}

// "Dikte": tıkla veya Space  /  basılı tut: Space  gibi kısa ipucu metni
function hotkeyHint(codeKey, holdKey) {
  const label = keyLabel(NicoleSettings.get(codeKey));
  return NicoleSettings.get(holdKey) ? `basılı tut: ${label}` : `tıkla veya ${label}`;
}

// --- Kısayol altyapısı ---
function _isTypingTarget(el) {
  if (!el) return false;
  if (el.isContentEditable || el.tagName === "TEXTAREA") return true;
  if (el.tagName === "INPUT") {
    const type = (el.type || "text").toLowerCase();
    return !["checkbox", "radio", "button", "submit", "range", "color", "file"].includes(type);
  }
  return false;
}

function _isModalOpen() {
  return !!document.querySelector(".modal-overlay.open");
}

// opts: { getCode, isHold, enabled, isRecording, isStarting, start, stop }
//  - bas-çek: tuşa basınca kayıt başlar, tekrar basınca durur
//  - basılı tut: tuş basılıyken kayıt sürer, bırakınca durur
// Yazı yazılan alanlarda, açık pencerelerde ve Ctrl/Alt/Meta ile birlikte tetiklenmez.
function attachHotkey(opts) {
  let handledDown = false;
  let held = false;

  document.addEventListener("keydown", async (e) => {
    if (NicoleSettings.capturingKey || e.code !== opts.getCode()) return;
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    if (_isTypingTarget(e.target) || _isModalOpen() || !opts.enabled()) return;

    e.preventDefault();
    handledDown = true;
    // Odaktaki düğme/seçim kutusu Space ile ayrıca tıklanmasın / harfle seçim değiştirmesin
    if (e.target && e.target !== document.body && typeof e.target.blur === "function") e.target.blur();
    if (e.repeat) return;

    if (opts.isHold()) {
      held = true;
      if (!opts.isRecording() && !opts.isStarting()) {
        await opts.start();
        if (!held && opts.isRecording()) opts.stop(); // tuş, kayıt başlarken bırakılmıştı
      }
    } else if (!opts.isStarting()) {
      if (opts.isRecording()) opts.stop();
      else await opts.start();
    }
  });

  document.addEventListener("keyup", (e) => {
    if (e.code !== opts.getCode() || !handledDown) return;
    e.preventDefault();
    handledDown = false;
    if (opts.isHold()) {
      held = false;
      if (opts.isRecording()) opts.stop();
    }
  });

  // Pencereden çıkılırsa (Alt+Tab) bırakma olayı gelmez — basılı tut kaydı takılı kalmasın
  window.addEventListener("blur", () => {
    if (opts.isHold() && held) {
      held = false;
      if (opts.isRecording()) opts.stop();
    }
  });
}

// --- Mikrofon ---
// Seçili mikrofonu açar. Cihaz artık bağlı değilse (örn. Bluetooth kulaklık kapandı) hata vermek
// yerine varsayılan mikrofona düşer ve bunu bildirir. Gerçekte kullanılan cihazı ve ses
// ayarlarını F12 Console'a yazar (ses kalitesi sorunlarını teşhis etmek için).
async function getMicStream(deviceId, onFallback) {
  let stream = null;
  if (deviceId) {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: deviceId } } });
    } catch (err) {
      if (err.name !== "OverconstrainedError" && err.name !== "NotFoundError") throw err;
      console.warn("[Nicole] Seçili mikrofon bulunamadı, varsayılana düşülüyor:", err.name);
      if (onFallback) onFallback("Seçili mikrofon bağlı değil — varsayılan mikrofon kullanılıyor.");
    }
  }
  if (!stream) stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  const track = stream.getAudioTracks()[0];
  if (track) {
    const s = track.getSettings ? track.getSettings() : {};
    console.log("[Nicole] Mikrofon açıldı:", track.label || "(etiketsiz)", {
      sampleRate: s.sampleRate, channelCount: s.channelCount,
      echoCancellation: s.echoCancellation, noiseSuppression: s.noiseSuppression, autoGainControl: s.autoGainControl,
    });
  }
  return stream;
}

// --- Sayfa açılışında ayar kutularını bağla ---
bindSetting("includeContext", "includeContext");
bindSetting("allowEdits", "allowEdits");
bindSetting("autoSendVoice", "autoSendVoice");
bindSetting("inputModeSelect", "inputMode");
bindSetting("dictationHold", "dictationHold");
bindSetting("chatHold", "chatHold");
bindSetting("flagEngineSelect", "flagEngine");

document.getElementById("settingsBtn")?.addEventListener("click", () =>
  document.getElementById("settingsOverlay").classList.toggle("open"));
document.addEventListener("keydown", (e) => {
  // Kısayol tuşu atanırken Esc atamayı iptal eder; paneli kapatmasın
  if (e.key === "Escape" && !NicoleSettings.capturingKey) document.getElementById("settingsOverlay")?.classList.remove("open");
});
document.getElementById("settingsCloseBtn")?.addEventListener("click", () =>
  document.getElementById("settingsOverlay").classList.remove("open"));
document.getElementById("settingsOverlay")?.addEventListener("click", (e) => {
  if (e.target.id === "settingsOverlay") e.target.classList.remove("open");
});