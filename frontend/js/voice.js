// Nicole — Editör cevaplarını sesli okuma.
// 1) Sunucu sesi (☁): Nicole'nin sunucusu Microsoft'un doğal seslerinden mp3 üretir (Emel vb.) —
//    tarayıcıdan bağımsızdır, Brave/Chrome'da da çalışır; internet ister.
// 2) Tarayıcı sesi (💻): internet yoksa ya da sunucu sesi alınamazsa tarayıcının kendi sesi (ör. Tolga).
// Ayar değeri "srv:<ad>" ya da "web:<ad>" biçiminde saklanır.

const NicoleVoice = {
  serverVoices: [],
  defaultServerVoice: "tr-TR-EmelNeural",
  audio: null,

  webVoices() {
    return window.speechSynthesis ? speechSynthesis.getVoices() : [];
  },

  choice() {
    const saved = NicoleSettings.get("ttsVoice");  // eski sürümde önek olmadan saklanıyordu
    return /^(srv|web):/.test(saved) ? saved : `srv:${this.defaultServerVoice}`;
  },

  bestWebVoice(name) {
    const voices = this.webVoices();
    return voices.find((v) => v.name === name) || voices.find((v) => v.lang.startsWith("tr")) || null;
  },

  // Markdown işaretlerini ve emojileri okumasın
  clean(text) {
    return text
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/\[(.*?)\]\(.*?\)/g, "$1")
      .replace(/[*_#>`~|]/g, " ")
      .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
  },

  async speak(text) {
    this.stop();
    const clean = this.clean(text);
    if (!clean) return;
    const choice = this.choice();
    if (choice.startsWith("srv:")) {
      try {
        const res = await fetch("/api/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: clean, voice: choice.slice(4),
            rate: NicoleSettings.get("ttsRate") || 1, pitch: NicoleSettings.get("ttsPitch") || 1,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const url = URL.createObjectURL(await res.blob());
        this.audio = new Audio(url);
        this.audio.addEventListener("ended", () => URL.revokeObjectURL(url));
        await this.audio.play();
        return;
      } catch (err) {
        console.warn("[Nicole] Sunucu sesi alınamadı, tarayıcı sesine geçiliyor:", err);
      }
    }
    this.speakWeb(clean, choice.startsWith("web:") ? choice.slice(4) : "");
  },

  // Tarayıcı sesi: cümle cümle (bazı sesler uzun metinde ~15 sn sonra kesiliyor)
  speakWeb(text, name) {
    if (!window.speechSynthesis) return;
    const voice = this.bestWebVoice(name);
    for (const part of text.match(/[^.!?…]+[.!?…]*\s*/g) || []) {
      const u = new SpeechSynthesisUtterance(part);
      if (voice) { u.voice = voice; u.lang = voice.lang; } else { u.lang = "tr-TR"; }
      u.rate = NicoleSettings.get("ttsRate") || 1;
      u.pitch = NicoleSettings.get("ttsPitch") || 1;
      speechSynthesis.speak(u);
    }
  },

  stop() {
    if (this.audio) { this.audio.pause(); this.audio = null; }
    if (window.speechSynthesis) speechSynthesis.cancel();
  },

  speaking() {
    return !!((this.audio && !this.audio.paused) || (window.speechSynthesis && speechSynthesis.speaking));
  },
};

// --- Ayarlar paneli ---
function fillVoiceSelect() {
  const select = document.getElementById("ttsVoiceSelect");
  if (!select) return;
  select.innerHTML = "";
  if (NicoleVoice.serverVoices.length) {
    const group = document.createElement("optgroup");
    group.label = "☁ Doğal sesler (internet gerekir)";
    for (const v of NicoleVoice.serverVoices) {
      const label = v.name.replace(/Neural$/, "").replace(/^[a-z]{2}-[A-Z]{2}-/, "");
      const lang = v.locale.startsWith("tr") ? "Türkçe" : "çok dilli";
      group.append(new Option(`${label} (${lang}, ${v.gender === "Female" ? "kadın" : "erkek"})`, `srv:${v.name}`));
    }
    select.append(group);
  }
  const web = NicoleVoice.webVoices().filter((v) => /^(tr|ja|en)/i.test(v.lang));
  if (web.length) {
    const group = document.createElement("optgroup");
    group.label = "💻 Tarayıcının sesleri (internetsiz)";
    for (const v of web) group.append(new Option(`${v.name.replace(/^Microsoft /, "")} (${v.lang})`, `web:${v.name}`));
    select.append(group);
  }
  select.value = NicoleVoice.choice();
  if (!select.value && select.options.length) select.selectedIndex = 0;
  document.getElementById("ttsHint").textContent = NicoleVoice.serverVoices.length
    ? "☁ sesler her tarayıcıda çalışır; internet yoksa otomatik olarak tarayıcının sesine geçilir."
    : "Doğal sesler alınamadı (internet yok mu?) — tarayıcının sesi kullanılacak.";
}

(async function loadVoices() {
  try {
    const data = await apiGet("/tts/voices");
    NicoleVoice.serverVoices = data.voices;
    NicoleVoice.defaultServerVoice = data.default;
  } catch (_) { /* internet yoksa sadece tarayıcı sesleri */ }
  fillVoiceSelect();
})();
if (window.speechSynthesis) speechSynthesis.addEventListener?.("voiceschanged", fillVoiceSelect);

bindSetting("ttsEnabled", "ttsEnabled");
document.getElementById("ttsVoiceSelect")?.addEventListener("change", (e) => NicoleSettings.set("ttsVoice", e.target.value));

for (const [id, key] of [["ttsRateRange", "ttsRate"], ["ttsPitchRange", "ttsPitch"]]) {
  const range = document.getElementById(id);
  if (!range) continue;
  const label = document.getElementById(`${id}Value`);
  range.value = NicoleSettings.get(key);
  label.textContent = Number(range.value).toFixed(1);
  range.addEventListener("input", () => (label.textContent = Number(range.value).toFixed(1)));
  range.addEventListener("change", () => NicoleSettings.set(key, Number(range.value)));
}

document.getElementById("ttsTestBtn")?.addEventListener("click", () =>
  NicoleVoice.speaking()
    ? NicoleVoice.stop()
    : NicoleVoice.speak("Merhaba, ben Nicole. Hikâyende takıldığın bir yer varsa birlikte bakalım."));
