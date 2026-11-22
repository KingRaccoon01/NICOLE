# Nicole — kaldırma (Windows). "Kaldir.bat" bu dosyayı çalıştırır. Kurulum.bat'ın kurduklarını geri alır.
# Her grup ayrı sorulur. Romanlar (nicole.db, yedekler) ve API anahtarı (.env) sadece "SIL" yazılırsa silinir.
$ErrorActionPreference = "Continue"  # bir adım başarısız olsa da diğerleri denensin
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Adim($metin) { Write-Host ""; Write-Host "==> $metin" -ForegroundColor Cyan }
function Tamam($metin) { Write-Host "    ✓ $metin" -ForegroundColor Green }
function Uyari($metin) { Write-Host "    ! $metin" -ForegroundColor Yellow }
function Evet($soru) { return (Read-Host "    $soru (E/H)") -match "^[eEyY]" }
function Sil($yol, $ad) {
    if (-not (Test-Path -LiteralPath $yol)) { return }
    Remove-Item -LiteralPath $yol -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $yol) { Uyari "$ad silinemedi: $yol (açık bir program kullanıyor olabilir)" } else { Tamam "$ad silindi" }
}

Write-Host "NICOLE KALDIRMA" -ForegroundColor Magenta
Write-Host "Her adımda ne silineceği sorulur. Romanların ancak SIL yazarsan silinir."

# Açık Nicole sunucusu venv dosyalarını kilitler: önce kapat
Get-Process python, pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith((Join-Path $PSScriptRoot "venv"), [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- 1) Masaüstü kısayolu
Adim "Masaüstü kısayolu"
Sil (Join-Path ([Environment]::GetFolderPath("Desktop")) "Nicole.lnk") "Nicole kısayolu"

# ---------------------------------------------------------------- 2) Kütüphaneler
Adim "Kütüphaneler"
Write-Host "    Python paketleri (venv), Whisper ses modelleri ve Ollama'nın llama3.2:3b modeli (toplam ~3-5 GB)."
Write-Host "    Silersen Nicole'yi tekrar kullanmak için Kurulum.bat'ı yeniden çalıştırman gerekir."
$ollamaVar = [bool](Get-Command ollama -ErrorAction SilentlyContinue)
if (Evet "Kütüphaneleri de silmek istiyor musun?") {
    Sil (Join-Path $PSScriptRoot "venv") "Python paketleri"
    $hub = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    Get-ChildItem -LiteralPath $hub -Directory -Filter "models--Systran--faster-whisper-*" -ErrorAction SilentlyContinue |
        ForEach-Object { Sil $_.FullName "Whisper modeli ($($_.Name -replace '^models--Systran--', ''))" }
    if ($ollamaVar) {
        & ollama rm llama3.2:3b 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { Tamam "llama3.2:3b modeli silindi" } else { Uyari "llama3.2:3b modeli silinemedi (zaten yok olabilir)" }
    }
} else {
    Tamam "Kütüphanelere dokunulmadı"
}

# ---------------------------------------------------------------- 3) Programlar (Python 3.11, Ollama)
Adim "Programlar"
Write-Host "    Kurulum, bilgisayarda yoksa Python 3.11 ve Ollama'yı da kurmuştu."
Write-Host "    Başka programların (ya da senin) bunları kullanıyorsa silme."
if (Get-Command winget -ErrorAction SilentlyContinue) {
    if ($ollamaVar -and (Evet "Ollama kaldırılsın mı?")) {
        Get-Process ollama*, "ollama app" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        winget uninstall --id Ollama.Ollama -e --silent --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { Tamam "Ollama kaldırıldı" } else { Uyari "Ollama kaldırılamadı; Ayarlar > Uygulamalar'dan elle kaldırabilirsin." }
    }
    if (Evet "Python 3.11 kaldırılsın mı?") {
        winget uninstall --id Python.Python.3.11 -e --silent --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { Tamam "Python 3.11 kaldırıldı" } else { Uyari "Python 3.11 kaldırılamadı; Ayarlar > Uygulamalar'dan elle kaldırabilirsin." }
    }
} else {
    Uyari "'winget' yok: Python 3.11 ve Ollama'yı istersen Ayarlar > Uygulamalar'dan elle kaldır."
}

# ---------------------------------------------------------------- 4) Romanlar ve ayarlar
Adim "Romanların ve ayarların"
Write-Host "    nicole.db (bütün romanların), yedekler klasörü ve .env (Gemini API anahtarın)." -ForegroundColor Yellow
Write-Host "    Bunlar silinirse GERİ GETİRİLEMEZ. Saklamak istersen bu dosyaları önce başka bir yere kopyala." -ForegroundColor Yellow
if ((Read-Host "    Silmek için büyük harfle SIL yaz, saklamak için sadece Enter'a bas") -ceq "SIL") {
    Get-ChildItem -LiteralPath $PSScriptRoot -Filter "nicole.db*" -File | ForEach-Object { Sil $_.FullName $_.Name }
    Sil (Join-Path $PSScriptRoot "yedekler") "Yedekler"
    Sil (Join-Path $PSScriptRoot ".env") ".env (API anahtarı)"
} else {
    Tamam "Romanlar ve ayarlar saklandı"
}

Write-Host ""
Write-Host "KALDIRMA TAMAM." -ForegroundColor Green
Write-Host "Nicole'nin klasörünü ($PSScriptRoot) artık elle silebilirsin (romanlarını sakladıysan önce onları al)."
