@echo off
rem Nicole kaldirma: cift tikla. Her adimda sorar; romanlar sadece SIL yazilirsa silinir.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kaldir.ps1"
pause
