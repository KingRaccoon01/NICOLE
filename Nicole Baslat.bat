@echo off
rem Nicole baslatici. Not: bu dosyada Turkce karakter kullanma - cmd UTF-8 .bat dosyalarini bozuk okuyor.
title Nicole
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo Nicole henuz kurulmamis: venv klasoru yok.
  echo README.md dosyasindaki Kurulum adimlarini bir kez uygula, sonra bu dosyaya tekrar cift tikla.
  pause
  exit /b 1
)

rem Ollama calismiyorsa arka planda baslat. Kurulu degilse atla: Nicole yine acilir, sadece flag taramasi olmaz.
tasklist /fi "imagename eq ollama.exe" | "%SystemRoot%\System32\find.exe" /i "ollama.exe" >nul
if errorlevel 1 (
  where ollama >nul 2>nul && start "" /min ollama serve
)

rem Sunucu acilinca tarayiciyi ac
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8000"

echo Nicole baslatiliyor... Kapatmak icin bu pencereyi kapat.
echo.
"venv\Scripts\python.exe" -m uvicorn backend.main:app --port 8000
echo.
echo Nicole kapandi. Yukarida hata varsa (ornegin "address already in use" = Nicole zaten acik) ona bak.
pause
