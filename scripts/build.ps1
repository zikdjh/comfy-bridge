# 构建前端并将产物拷入 backend/app/static（M4 打包前手动执行）
$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root/frontend"
pnpm install
pnpm build
Pop-Location
if (Test-Path "$root/backend/app/static") { Remove-Item -Recurse -Force "$root/backend/app/static" }
Copy-Item -Recurse "$root/frontend/dist" "$root/backend/app/static"
Write-Host "前端已构建并拷入 backend/app/static"
