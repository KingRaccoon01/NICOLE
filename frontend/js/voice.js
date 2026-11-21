// Nicole — Editör cevaplarını sesli okuma (tarayıcının kendi seslendirmesi: ücretsiz, kurulum yok).
// En doğal Türkçe ses Edge'deki "Microsoft Emel Online (Natural)"; Chrome'da genelde sadece Tolga var.

const NicoleVoice = {
  voices: [],

  load() {
    this.voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    return this.voices;
  },

  // Kayıtlı ses yoksa: Türkçe doğal kadın sesi (Emel) > herhangi bir Türkçe ses > tarayıcı varsayılanı
  pick() {
    const saved = NicoleSettings.get("ttsVoice");
    return (
      this.voices.find((v) => v.name === saved) ||
      this.voices.find((v) => v.lang.startsWith("tr") && /emel/i.test(v.name)) ||
      this.voices.find((v) => v.lang.startsWith("tr")) ||
      null
    );
  },

  // Markdown işaretlerini ve emojileri okumasın; uzun metni cümlelere böl (bazı sesler ~15 sn sonra kesiliyor)
  chunks(text) {
    const clean = text
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/[*_#>`~|]/g, " ")
      .replace(/\[(.*?)\]\(.*?\)/g, "$1")
      .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
    return clean.match(/[^.!?…]+[.!?…]*\s*/g) || [];
  },

  speak(text) {
    if (!window.speechSynthesis) return;
    this.stop();
    const voice = this.pick();
    for (const part of this.chunks(text)) {
      const u = new SpeechSynthesisUtterance(part);
      if (voice) { u.voice = voice; u.lang = voice.lang; } else { u.lang = "tr-TR"; }
      u.rate = NicoleSettings.get("ttsRate") || 1;
      u.pitch = NicoleSettings.get("ttsPitch") || 1;
      speechSynthesis.speak(u);
    }
  },

  stop() {
    if (window.speechSynthesis) speechSynthesis.cancel();
  },

  speaking() {
    return !!(window.speechSynthesis && speechSynthesis.speaking);
  },
};

// --- Ayarlar paneli ---
function fillVoiceSelect() {
  const select = document.getElementById("ttsVoiceSelect");
  if (!select) return;
  const voices = NicoleVoice.load();
  if (!voices.length) return;
  // Edge yüzlerce dil getiriyor; sadece Türkçe (üstte), Japonca ve İngilizce sesler listelenir
  const wanted = voices.filter((v) => /^(tr|ja|en)/i.test(v.lang) || v.name === NicoleSettings.get("ttsVoice"));
  const sorted = [...wanted].sort((a, b) => (b.lang.startsWith("tr") - a.lang.startsWith("tr")) || a.name.localeCompare(b.name));
  select.innerHTML = "";
  for (const v of sorted) select.add(new Option(`${v.name.replace(/^Microsoft /, "")} (${v.lang})`, v.name));
  const current = NicoleVoice.pick();
  if (current) select.value = current.name;
  const hasNatural = voices.some((v) => v.lang.startsWith("tr") && /natural/i.test(v.name));
  document.getElementById("ttsHint").textContent = hasNatural
    ? ""
    : "Bu tarayıcıda doğal Türkçe ses yok. Microsoft Edge'de 'Emel (Natural)' sesi var — çok daha doğal.";
}

if (window.speechSynthesis) {
  speechSynthesis.addEventListener?.("voiceschanged", fillVoiceSelect);
  fillVoiceSelect();
} else {
  const hint = document.getElementById("ttsHint");
  if (hint) hint.textContent = "Bu tarayıcı sesli okumayı desteklemiyor.";
}

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
  NicoleVoice.speak("Merhaba, ben Nicole. Hikâyende takıldığın bir yer varsa birlikte bakalım."));
