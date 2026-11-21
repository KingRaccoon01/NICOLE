# NICOLE — Proje Planı
*(eski ismi: ENSŌ — eski kod tabanı bozulduğu için sıfırdan yeniden yapılıyor)*

> Bu doküman, verdiğin 3 ekran görüntüsü + sözlü anlatımına dayanarak hazırlanmıştır. **Görseller sadece tasarım/görsel referanstır** — akış ve fonksiyon mantığı tamamen senin yazılı anlatımına göre kurgulanmıştır. Proje sıfırdan (yeni bir kod tabanıyla) inşa edilecek. Tüm bileşenler **ücretsiz** araç/servislerle çalışacak şekilde seçildi. Kesin olmayan tek nokta en sonda işaretlendi.

---

## 1. Proje Özeti

**Nicole**, manga/roman yazarları için geliştirilen, sesle dikte edilen metni gerçek zamanlı olarak yazıya döken, yazım sırasında dünya tutarlılığını (lore) otomatik kontrol eden ve yazarla birlikte hikâyeyi tartışabilen bir AI destekli yazma masaüstü uygulamasıdır.

Üç temel yetenek üzerine kurulu:
1. **Dikte** — mikrofonla konuşulanı canlı metne çevirmek
2. **Tutarlılık Kontrolü (Flag Sistemi)** — metinde geçen ama sözlükte tanımlı olmayan özel terimleri ("chidori", "rasengan", "bankai" gibi kurgusal/jargon kelimeleri) yakalayıp yazara sormak
3. **AI Editör Sohbeti** — yazdığın hikâye bağlamını bilen, karakter/plot/dünya soruları sorulabilen bir sohbet asistanı

Ek olarak: çoklu proje (roman) yönetimi, Word (.docx) olarak dışa aktarma.

---

## 2. Teknoloji Yığını (Stack) Önerisi

Senin "Python + HTML, yerel sunucu" fikrin doğru yönde — bunu netleştirip önerelim:

| Katman | Teknoloji | Neden |
|---|---|---|
| Backend | **FastAPI** (Python) + WebSocket | Hem REST hem gerçek zamanlı ses akışı için tek framework, hafif, hızlı |
| Konuşma → Metin (STT) | **faster-whisper** (yerel, GPU) | RTX 4060'ın var — ücretsiz, internete ihtiyaç duymadan, düşük gecikmeyle çalışır. Görseldeki "Whisper" seçeneğiyle birebir örtüşüyor |
| Lore/Flag Tespiti | **Ollama + llama3** (yerel LLM) | Ücretsiz, sınırsız, hızlı; "bu kelime sözlükte yok mu, flag'le" gibi basit sınıflandırma işine internet gerektiren pahalı bir modelden daha uygun |
| AI Editör Sohbeti | **Google Gemini API (ücretsiz katman)** | Senin de belirttiğin gibi; daha "yaratıcı" ve bağlam takibi gereken sohbet kısmı için Gemini, basit kelime tespiti için Ollama — ikisini ayırmak hem ücretsiz kalmanı sağlar hem de her işi en uygun modele verir |
| Veritabanı | **SQLite + aiosqlite (veya SQLAlchemy 2.0+)** | Yerel, dosya tabanlı, kuruluma gerek yok, çoklu proje/bölüm/lore kaydı için yeterli |
| Word Dışa Aktarım | **python-docx** | Bitmiş bölümü biçimlendirilmiş .docx olarak indirme |
| Frontend | **Tailwind CSS + Vanilla JS** (veya hafif bir framework istersen Alpine.js) | Görseldeki koyu/minimal arayüzü kod karmaşası olmadan üretmeye yeter; React gibi ağır bir şeye gerek yok |
| Ses yakalama | Tarayıcı `MediaRecorder API` → WebSocket ile backend'e ham ses akışı | Gerçek zamanlı "konuş-yazıya-dök" hissi için |

**Not:** ENSŌ'da kurduğun altyapı bozulduğu için Nicole, aynı teknoloji seçimleriyle ama **sıfırdan, temiz bir kod tabanıyla** kurulacak. Yığındaki her bileşen ücretsizdir:
- faster-whisper → yerel, açık kaynak, ücretsiz
- Ollama/llama3 → yerel, açık kaynak, ücretsiz
- Google Gemini API → ücretsiz katman (rate limit'e dikkat edilecek, aşımda kullanıcıya bilgi verilecek)
- FastAPI, SQLite, python-docx, Tailwind, Vanilla JS → hepsi açık kaynak/ücretsiz
- Hiçbir ücretli abonelik veya API anahtarı gerektirmez.

---

## 3. Sistem Mimarisi (Yüksek Seviye)

```
┌─────────────────────────────┐
│         TARAYICI (UI)        │
│  Dikte Paneli │ Editör Sohbet │
│  Flag Rozeti  │ Müdahale Modal│
└───────────┬───────────────────┘
            │ WebSocket (ses akışı + canlı metin)
            │ REST (proje/bölüm/lore CRUD, docx export)
┌───────────▼───────────────────┐
│         FASTAPI BACKEND        │
│                                │
│  ┌─────────────┐  ┌──────────┐│
│  │ faster-whisper│  │ Ollama   ││
│  │ (STT)         │  │ llama3   ││
│  │               │  │ (flag    ││
│  │               │  │ tespiti) ││
│  └─────────────┘  └──────────┘│
│                                │
│  ┌──────────────────────────┐ │
│  │ Gemini API (Editör Sohbet)│ │
│  └──────────────────────────┘ │
│                                │
│  ┌──────────────────────────┐ │
│  │ SQLite (projeler, bölümler,│ │
│  │ lore sözlüğü, flag geçmişi)│ │
│  └──────────────────────────┘ │
│                                │
│  ┌──────────────────────────┐ │
│  │ python-docx (export)      │ │
│  └──────────────────────────┘ │
└────────────────────────────────┘
```

---

## 4. Veri Modeli (Veritabanı Şeması)

```
Project (Roman)
├── id, title, created_at, updated_at

Chapter (Bölüm)
├── id, project_id (FK), title, order, word_count, paragraph_count

Paragraph (Paragraf/Dikte parçası)
├── id, chapter_id (FK), text, order, created_at

FlaggedTerm (Tespit edilen/işaretlenen terim)
├── id, paragraph_id (FK), raw_text ("chidori")
├── status: pending | accepted_raw (mavi) | accepted_suggestion (yeşil) | manual (sarı)
├── suggested_text ("çakı")     ← AI'nin önerdiği anlamlı karşılık
├── final_text                  ← kullanıcının onayladığı/yazdığı nihai değer
├── position_start, position_end  (paragraf içindeki konumu)
├── resolved_at, created_at

LoreEntry (Lore Sözlüğü kaydı — TÜM PROJELER ARASINDA PAYLAŞILIR, project_id YOK)
├── id, term ("chidori"), definition/kategori (opsiyonel)
├── source: auto | manual
├── created_at

ChatMessage (ENSO/Nicole Editör sohbeti)
├── id, project_id (FK), role (user/assistant), content, created_at
```

**Netleşti:** Lore Sözlüğü tüm projeler arasında **paylaşılan tek bir sözlük** olacak — bir projede "chidori" tanımlanınca, başka bir romanda da aynı terim otomatik tanınacak.

---

## 5. Flag Sistemi — Detaylı Akış

Bu, projenin en kritik parçası. Anlattıklarını adım adım netleştirdim:

### 5.1 Tespit
- Dikte sırasında Ollama/llama3, gelen metni tarar.
- Sözlükte (LoreEntry) karşılığı **olmayan** özel/kurgusal bir terim tespit edilirse (örn. "chidori"), kelime ekranda **yazılmaz** — yerine kırmızı bir 🚩 rozet gösterilir (Görsel 2'deki gibi, "🚩 2" formatında sırayla numaralanır).
- Sözlükte zaten **varsa**, terim direkt normal, işaretlenmemiş şekilde yazılır.

### 5.2 Rozete Tıklanınca — Müdahale Modalı (Görsel 3)
**Karar:** Modalde ayrı bir 4. buton olacak — **"Orijinal Terimi Kabul Et"**. Kullanıcı bu butona bastığında "chidori" kelimesi hiç değiştirilmeden, öneriyle karşılaştırılmadan kalır.

| Buton | Ne yapar | Sonuç rengi | Lore sözlüğüne eklenir mi? |
|---|---|---|---|
| **Orijinal Terimi Kabul Et** | "chidori" kelimesi olduğu gibi kalır, öneriyle değiştirilmez | 🔵 Mavi | Evet, otomatik |
| **Öneriyi Kabul Et** | AI'nin önerdiği anlamlı karşılığı kullan (örn. "çakı") | 🟢 Yeşil | Evet, otomatik |
| **Manuel Düzelt** | Kullanıcı kendi terimini/düzeltmesini yazar | 🟡 Sarı | Hayır — ayrı bir "Lore Sözlüğüne Ekle" butonuyla **isteğe bağlı** |
| **İptal (✕)** | Hiçbir değişiklik yapılmaz | 🔴 Kırmızı flag kalır | Hayır |

### 5.3 Renk Kodu Özeti (nihai)
- 🔴 **Kırmızı** — henüz karar verilmemiş, bekleyen flag
- 🔵 **Mavi** — orijinal/algılanan terim aynen kabul edildi
- 🟢 **Yeşil** — AI önerisi kabul edildi
- 🟡 **Sarı** — kullanıcı manuel düzeltme yaptı

### 5.4 Paragraf Üstü Sayaç
Görsel 2'deki gibi her paragrafın üstünde işaretleyici sayılar (1, 2, 3, 4...) o paragraftaki flag/lore referanslarının sırasını gösteriyor — bunu paragraf meta verisinde tutup UI'da otomatik numaralandıracağız.

---

## 6. Dikte Akışı (Sol Panel)

Görsel 1'e göre:
- Üstte kelime/paragraf/flag sayaçları (canlı güncellenir)
- Ortada boş/dolu durum göstergesi ("Mikrofonu başlatın ve dikte etmeye başlayın")
- Altta: kayıt butonu + süre, **1. dropdown** = giriş modu seçimi (Whisper ile sesli dikte / klavyeyle yazma), **2. dropdown** = ses kaynağı seçimi (kulaklık mikrofonu, bilgisayarın dahili mikrofonu, vb. — sistemdeki mevcut ses giriş cihazları listelenir), proje/bölüm adı ("Manga Taslağı"), ve DOCX indirme butonu

**Akış (Whisper/sesli modda):**
1. Kullanıcı giriş modunu "Whisper" seçer, ses kaynağını (kulaklık/dahili mikrofon) belirler, mikrofonu başlatır → tarayıcı ses akışını WebSocket ile backend'e gönderir
2. Backend, faster-whisper ile gerçek zamanlı/parça parça transkripsiyon yapar
3. Her tamamlanan cümle/paragraf hem ekrana yazılır hem Ollama'ya flag taraması için gönderilir
4. Flag varsa metin yerine 🚩 rozeti render edilir, yoksa düz metin yazılır

**Akış (Klavye modunda):** Whisper/ses akışı devre dışı kalır, kullanıcı doğrudan yazar; yazılan her paragraf yine aynı şekilde Ollama'ya flag taraması için gönderilir — flag sistemi giriş yönteminden bağımsız çalışır.

---

## 7. AI Editör Sohbeti (Sağ Panel)

Görsel 1'deki "ENSŌ Editör" sekmesi → **"Nicole Editör"** olarak yeniden adlandırılacak.

- Sohbet, "Hikaye bağlamını ekle" checkbox'ı işaretliyken **soldaki mevcut metnin tamamını** otomatik context olarak Gemini'ye gönderir.
- Yazar karakter, plot twist, dünya kuralları hakkında soru sorabilir.
- İkinci sekme **"Lore Sözlüğü"** — projenin birikmiş tüm LoreEntry kayıtlarının listelendiği, aranabilir bir sözlük ekranı olacak (arama, kategori filtresi, manuel ekleme/silme).

---

## 8. Çoklu Proje Yönetimi

Görsel 1'deki sağ üstteki **"Romanlar"** butonu → tüm projelerin listelendiği bir ekrana açılacak:
- Proje kartları: başlık, son düzenleme tarihi, toplam kelime/bölüm sayısı
- Yeni proje oluşturma
- Projeye tıklayınca o projenin dikte + editör ekranına geçiş

Her proje kendi bölümlerini, lore sözlüğünü ve sohbet geçmişini izole şekilde tutar (yukarıdaki veri modeliyle uyumlu).

---

## 9. Word (.docx) Dışa Aktarım

- "DOCX" butonuna basınca, aktif bölümün (veya tüm projenin) paragrafları python-docx ile biçimlendirilip indirilir.
- Flag'li/çözülmemiş terimler varsa (🔴 kırmızı), export öncesi kullanıcıya "N adet çözülmemiş terim var, yine de indirilsin mi?" uyarısı gösterilir (öneri — istersen kaldırırız).

---

## 10. Marka/Tasarım Notları (ENSŌ → Nicole geçişi)

- Görsellerdeki koyu, minimal, Japon estetiği (ink/paper/vermil/gold/jade renk paleti, 円 karakteri, dual-panel yerleşim) **korunacak** — sadece isim/metinler güncellenecek, tema aynı kalacak.
- Değişecekler: logo/başlık metni "ENSŌ" → **"NICOLE"**, sekme başlığı "ENSŌ Editör" → **"Nicole Editör"**, alt yazı vb. metinler isme göre uyarlanacak.
- Görsellerdeki spesifik yerleşim/etiketler (ör. "Manga Taslağı", buton konumları) birebir kopyalanmayacak, genel tasarım dili referans alınacak.

---

## 11. Geliştirme Fazları (Roadmap)

| Faz | Kapsam | Tahmini efor |
|---|---|---|
| **0 — Temel İskelet** | Sıfırdan FastAPI + SQLite + Tailwind/Vanilla JS iskeletini kur, Nicole marka/temayı uygula | Orta |
| **1 — Dikte Modülü** | faster-whisper entegrasyonu, giriş modu (sesli/klavye) ve ses kaynağı dropdown'ları | Orta |
| **2 — Flag Akışı** | Ollama/llama3 tespit + Müdahale modalı + 4 buton (mavi/yeşil/sarı/iptal) akışı | Orta-Yüksek |
| **3 — Paylaşılan Lore Sözlüğü** | Proje bağımsız sözlük tablosu, arama/filtreleme/manuel yönetim ekranı | Düşük-Orta |
| **4 — Çoklu Proje Ekranı** | "Romanlar" listeleme/oluşturma ekranı | Orta |
| **5 — AI Editör Sohbeti** | Gemini API entegrasyonu, bağlam gönderimi | Orta |
| **6 — DOCX Export** | python-docx ile biçimlendirilmiş indirme | Düşük |
| **7 — Cilalama** | Performans, hata payı, UX detayları | Sürekli |

---

## 12. Netleşen Sorular

| Soru | Cevap |
|---|---|
| Flag modalinde mavi durum için ayrı buton mu? | **Evet — 4. buton: "Orijinal Terimi Kabul Et"** |
| Lore Sözlüğü proje bazlı mı, paylaşılan mı? | **Tüm projeler arasında paylaşılan tek sözlük** |
| İkinci dropdown ne için? | **Ses giriş kaynağı** (kulaklık/dahili mikrofon vb.) — birinci dropdown giriş modunu (sesli/klavye) seçiyor |
| Görsel kimlik korunacak mı? | **Evet**, Japon estetiği aynen korunuyor |
| Export'ta çözülmemiş flag uyarısı gösterilsin mi? | **Henüz kararsız** — geliştirme sırasında opsiyonel bir ayar olarak ekleyip sonra açık/kapalı bırakabiliriz |

Flag akışı artık tamamen netleşti — plan geliştirmeye başlamak için hazır.
