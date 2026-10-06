@echo off
rem Double-click this file to install Codex Router on Windows.
rem It runs install.ps1 from this folder; nothing else needs to be installed first.
title Codex Router installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
