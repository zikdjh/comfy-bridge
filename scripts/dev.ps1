# 一键并行启动开发环境：后端 uvicorn :18189 + 前端 Vite :15173
$root = Split-Path -Parent $PSScriptRoot
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root/backend'; uv run uvicorn app.main:app --reload --port 18189"
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root/frontend'; pnpm dev"
Write-Host "backend  -> http://127.0.0.1:18189"
Write-Host "frontend -> http://127.0.0.1:15173 (/api 代理到 18189)"
