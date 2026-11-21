# Nicole — tek tıkla kurulum (Windows). "Kurulum.bat" bu dosyayı çalıştırır.
# Her adım önce "zaten yapılmış mı?" diye bakar; tekrar çalıştırmak güvenlidir, hiçbir şeyi silmez.
#   -Sessiz : soru sormaz (API anahtarı sorulmaz, kısayol oluşturulmaz, sonda Nicole açılmaz) — test için
param([switch]$Sessiz)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Adim($metin) { Write-Host ""; Write-Host "==> $metin" -ForegroundColor Cyan }
function Tamam($metin) { Write-Host "    ✓ $metin" -ForegroundColor Green }
function Uyari($metin) { Write-Host "    ! $metin" -ForegroundColor Yellow }
function Hata($metin) {
    Write-Host ""; Write-Host "    ✗ $metin" -ForegroundColor Red
    if (-not $Sessiz) { Read-Host "Kapatmak için Enter'a bas" | Out-Null }
    exit 1
}
function PathYenile {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function WingetKur($id, $ad) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Hata "$ad kurulu değil ve otomatik kurmak için gereken 'winget' bu bilgisayarda yok. $ad'ı elle kurup bu dosyayı tekrar çalıştır."
    }
    Write-Host "    $ad indiriliyor ve kuruluyor (birkaç dakika sürebilir)..."
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    PathYenile
}

Write-Host "NICOLE KURULUMU" -ForegroundColor Magenta
Write-Host "Bu pencere kurulum bitene kadar açık kalsın. İnternet bağlantısı gerekir."

# ---------------------------------------------------------------- 1) Python 3.11
Adim "Python 3.11"
function Python311Bul {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $yol = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $yol) { return $yol.Trim() }
        } catch { }  # 3.11 yoksa py hata yazar; PowerShell 5.1 bunu istisnaya çevirir
    }
    $aday = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $aday) { return $aday }
    return $null
}
$python = Python311Bul
if (-not $python) {
    WingetKur "Python.Python.3.11" "Python 3.11"
    $python = Python311Bul
    if (-not $python) { Hata "Python 3.11 kurulamadı. https://www.python.org/downloads/release/python-3119/ adresinden elle kurup tekrar dene." }
}
Tamam "Python: $python"

# ---------------------------------------------------------------- 2) Ollama + model
Adim "Ollama (yerel dil modeli — flag taraması için)"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    $olasi = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $olasi) { $env:Path += ";" + (Split-Path $olasi) } else { WingetKur "Ollama.Ollama" "Ollama" }
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Uyari "Ollama kurulamadı. Nicole yine çalışır; flag taraması için Gemini kullanılabilir (Ayarlar > Flag taraması)."
} else {
    Tamam "Ollama kurulu"
    if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
        Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 4
    }
    Write-Host "    llama3.2:3b modeli indiriliyor (ilk seferde ~2 GB)..."
    & ollama pull llama3.2:3b
    if ($LASTEXITCODE -eq 0) { Tamam "Model hazır" } else { Uyari "Model indirilemedi; sonra 'ollama pull llama3.2:3b' ile tekrar denenebilir." }
}

# ---------------------------------------------------------------- 3) Sanal ortam + paketler
Adim "Python paketleri"
if (-not (Test-Path "venv\Scripts\python.exe")) {
    & $python -m venv venv
    if ($LASTEXITCODE -ne 0) { Hata "Sanal ortam (venv) oluşturulamadı." }
}
$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPy -m pip install -r requirements.txt --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Hata "Paketler kurulamadı (internet bağlantısını kontrol et)." }
Tamam "Paketler kuruldu"

# ---------------------------------------------------------------- 4) NVIDIA ekran kartı
Adim "Ekran kartı"
$gpu = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($gpu) {
    Write-Host "    NVIDIA ekran kartı bulundu — Whisper'ı hızlandıracak kütüphaneler kuruluyor (~1 GB)..."
    & $venvPy -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 --quiet --disable-pip-version-check
    if ($LASTEXITCODE -eq 0) { Tamam "GPU desteği hazır" } else { Uyari "GPU kütüphaneleri kurulamadı; Whisper işlemcide (daha yavaş) çalışır."; $gpu = $false }
} else {
    Tamam "NVIDIA ekran kartı yok — Whisper işlemcide çalışacak (biraz daha yavaş)"
}

# ---------------------------------------------------------------- 5) Ayar dosyası (.env)
Adim "Ayarlar (.env)"
if (Test-Path ".env") {
    Tamam ".env zaten var — dokunulmadı"
} else {
    $ayar = Get-Content ".env.example" -Raw -Encoding UTF8
    if ($gpu) {
        $ayar = $ayar -replace "(?m)^WHISPER_DEVICE=.*$", "WHISPER_DEVICE=cuda" -replace "(?m)^WHISPER_MODEL_SIZE=.*$", "WHISPER_MODEL_SIZE=medium"
    }
    if (-not $Sessiz) {
        Write-Host ""
        Write-Host "    Nicole Editör (AI sohbet) için ücretsiz bir Gemini API anahtarı gerekir."
        Write-Host "    Tarayıcıda açılan sayfada 'Create API key' ile oluştur ve buraya yapıştır."
        Write-Host "    Şimdi istemiyorsan boş bırakıp Enter'a bas (sonra .env dosyasına yazabilirsin)."
        Start-Process "https://aistudio.google.com/apikey"
        $anahtar = (Read-Host "    Gemini API anahtarı").Trim()
        if ($anahtar) {
            $ayar = $ayar -replace "(?m)^GEMINI_API_KEY=.*$", "GEMINI_API_KEY=$anahtar"
            $ayar = $ayar -replace "(?m)^FLAG_ENGINE=.*$", "FLAG_ENGINE=gemini"
        }
    }
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $ayar, (New-Object Text.UTF8Encoding $false))
    Tamam ".env oluşturuldu"
}

# ---------------------------------------------------------------- 6) Masaüstü kısayolu
if (-not $Sessiz) {
    Adim "Masaüstü kısayolu"
    $kisayol = Join-Path ([Environment]::GetFolderPath("Desktop")) "Nicole.lnk"
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($kisayol)
    $sc.TargetPath = Join-Path $PSScriptRoot "Nicole Baslat.bat"
    $sc.WorkingDirectory = $PSScriptRoot
    $sc.Save()
    Tamam "Masaüstüne 'Nicole' kısayolu eklendi"
}

Write-Host ""
Write-Host "KURULUM TAMAM." -ForegroundColor Green
Write-Host "Nicole'yi açmak için masaüstündeki 'Nicole' kısayoluna ya da 'Nicole Baslat.bat' dosyasına çift tıkla."
if (-not $Sessiz) {
    if ((Read-Host "Nicole şimdi açılsın mı? (E/H)") -match "^[eEyY]") {
        Start-Process (Join-Path $PSScriptRoot "Nicole Baslat.bat")
    }
}
