@echo off
chcp 65001 >nul
title Prompt Atelier - AI服装提示词资产工作台
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "ai_prompt_workbench.py"
) else (
  python "ai_prompt_workbench.py"
)
if errorlevel 1 (
  echo.
  echo 启动失败：请确认已安装 Python 3。
  pause
)
