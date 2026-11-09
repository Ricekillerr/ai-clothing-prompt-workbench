@echo off
chcp 65001 >nul
title AI服装提示词资产工作台 V0.3
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "ai_prompt_workbench_v0_3.py"
) else (
  python "ai_prompt_workbench_v0_3.py"
)
if errorlevel 1 (
  echo.
  echo 启动失败，请确认 Python 3 已安装。
  pause
)
