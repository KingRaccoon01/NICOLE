@echo off
rem Nicole kurulumu: cift tikla. Yonetici olarak calistirmak gerekmez.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kurulum.ps1"
if errorlevel 1 pause
