// Nicole — Editör cevaplarını sesli okuma. Cevap Gemini yazdıkça cümle cümle okunur (beklemeden).
// 1) Sunucu sesi (☁): Nicole'nin sunucusu Microsoft'un doğal seslerinden mp3 üretir (Emel vb.) —
//    tarayıcıdan bağımsızdır, Brave/Chrome'da da çalışır; internet ister.
// 2) Tarayıcı sesi (💻): internet yoksa ya da sunucu sesi alınamazsa tarayıcının kendi sesi (ör. Tolga).
// Ayar değeri "srv:<ad>" ya da "web:<ad>" biçiminde saklanır.

const NicoleVoice = {
  serverVoices: [],
  defaultServerVoice: "tr-TR-EmelNeural",
  audio: null,       // şu an çalan parça
  seq: 0,            // her stop/yeni okuma artırır: eski okumanın gecikmiş parçaları çalınmaz (üst üste binmesin)
  queue: [],         // sıradaki parçalar: Promise<{url} | {text}> (text = tarayıcı sesiyle okunacak)
  pending: "",       // akıştan gelen, henüz cümlesi tamamlanmamış metin
  firstSent: false,
  playing: false,
  preloaded: null,   // bir sonraki parçanın önceden yüklenen sesi (parçalar arası boşluk olmasın)
  owner: null,       // okumayı başlatan düğme (▶ / 🔊): ses gerçekten çalarken yeşil yanar
  active: false,     // şu an duyulur ses var mı

  setActive(value) {
    this.active = value;
    if (this.owner) this.owner.classList.toggle("speaking", value);
  },

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

  // --- Okuma: hazır metin (speak) ya da Gemini yazdıkça gelen metin (begin → feed... → end) ---
  speak(text, owner = null) {
    this.begin(owner);
    this.feed(text);
    this.end();
  },

  begin(owner = null) {
    this.stop();
    this.owner = owner;
    this.pending = "";
    this.firstSent = false;
  },

  // İlk tam cümle hemen okunur (hızlı başlangıç); sonrakiler ~150 harf birikince (daha az istek, doğal tonlama)
  feed(delta) {
    this.pending += delta;
    const m = this.pending.match(/^[\s\S]*[.!?…](?=\s)/);
    if (!m || (this.firstSent && m[0].length < 150)) return;
    this.enqueue(m[0]);
    this.pending = this.pending.slice(m[0].length);
    this.firstSent = true;
  },

  end() {
    if (this.pending.trim()) this.enqueue(this.pending);
    this.pending = "";
  },

  enqueue(text) {
    const clean = this.clean(text);
    if (!clean) return;
    const choice = this.choice();
    const item = choice.startsWith("srv:")
      ? this.prepareServer(clean, choice.slice(4)).catch((err) => {
          console.warn("[Nicole] Sunucu sesi alınamadı, tarayıcı sesine geçiliyor:", err);
          return { text: clean };
        })
      : Promise.resolve({ text: clean, voice: choice.slice(4) });
    this.queue.push(item);
    if (!this.playing) this.playNext(this.seq);
  },

  async prepareServer(text, voice) {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice, rate: NicoleSettings.get("ttsRate") || 1, pitch: NicoleSettings.get("ttsPitch") || 1 }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { url: (await res.json()).url };
  },

  async playNext(my) {
    if (my !== this.seq) return;
    const item = this.queue.shift();
    if (!item) { this.playing = false; this.audio = null; this.setActive(false); return; }
    this.playing = true;
    const got = await item;
    if (my !== this.seq) return;
    if (got.text) {
      await this.speakWeb(got.text, got.voice || "");
      return this.playNext(my);
    }
    const audio = this.preloaded && this.preloaded.item === item ? this.preloaded.audio : new Audio(got.url);
    this.preloaded = null;
    this.audio = audio;
    let moved = false;
    const next = () => { if (!moved) { moved = true; this.playNext(my); } };
    audio.addEventListener("ended", next, { once: true });
    audio.addEventListener("error", next, { once: true });
    audio.addEventListener("playing", () => { if (my === this.seq) this.setActive(true); this.preloadNext(my); }, { once: true });
    // Ses 10 sn içinde başlamazsa takılı kalmasın: sıradakine geç (yoksa "konuşuyor" sanılıp düğme hep "durdur" olurdu)
    setTimeout(() => { if (my === this.seq && audio.currentTime === 0) next(); }, 10000);
    audio.play().catch(next);
  },

  // Çalan parça başlayınca bir sonrakini indirmeye başla
  async preloadNext(my) {
    const item = this.queue[0];
    if (!item) return;
    const got = await item;
    if (my !== this.seq || !got.url || this.queue[0] !== item) return;
    const audio = new Audio(got.url);
    audio.preload = "auto";
    this.preloaded = { item, audio };
  },

  // Tarayıcı sesi: cümle cümle (bazı sesler uzun metinde ~15 sn sonra kesiliyor); bitince çözülür
  speakWeb(text, name) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) return resolve();
      const voice = this.bestWebVoice(name);
      const parts = text.match(/[^.!?…]+[.!?…]*\s*/g) || [];
      if (!parts.length) return resolve();
      parts.forEach((part, i) => {
        const u = new SpeechSynthesisUtterance(part);
        if (voice) { u.voice = voice; u.lang = voice.lang; } else { u.lang = "tr-TR"; }
        u.rate = NicoleSettings.get("ttsRate") || 1;
        u.pitch = NicoleSettings.get("ttsPitch") || 1;
        if (i === 0) u.onstart = () => this.setActive(true);
        if (i === parts.length - 1) { u.onend = resolve; u.onerror = resolve; }
        speechSynthesis.speak(u);
      });
      // Bazı tarayıcılarda (Brave) bitiş olayı hiç gelmeyebiliyor: tahmini süre dolunca devam et
      setTimeout(resolve, 4000 + text.length * 120);
    });
  },

  stop() {
    this.seq++;
    this.queue = [];
    this.pending = "";
    this.playing = false;
    this.preloaded = null;
    if (this.audio) { this.audio.pause(); this.audio = null; }
    if (window.speechSynthesis) speechSynthesis.cancel();
    this.setActive(false);
    this.owner = null;
  },

  // Sadece Nicole'nin kendi durumu: tarayıcının speechSynthesis.speaking'i Brave'de takılı kalabiliyordu
  speaking() {
    return this.playing;
  },

  // ▶ / 🔊 düğmeleri: bu düğmenin başlattığı okuma sürüyorsa durdur, değilse (başka okuma varsa onu kesip) başlat
  toggle(text, button) {
    if (this.playing && this.owner === button) return this.stop();
    this.speak(text, button);
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

document.getElementById("ttsTestBtn")?.addEventListener("click", (e) => {
  const btn = e.currentTarget;
  const hint = document.getElementById("ttsHint");
  NicoleVoice.toggle("Merhaba, ben Nicole. Hikâyende takıldığın bir yer varsa birlikte bakalım.", btn);
  if (!NicoleVoice.playing) return;
  setTimeout(() => {  // 10 sn'de ses gelmediyse kullanıcı neden olmadığını bilsin
    if (NicoleVoice.owner === btn && !NicoleVoice.active) {
      hint.textContent = "⚠ Ses çalınamadı. İnternet bağlantısını ve tarayıcının bu sayfada ses çalmasına izin verdiğini kontrol et; ya da 💻 bir ses seç.";
    }
  }, 10000);
});
