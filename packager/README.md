# ComfyBridge 整合包（Rust 启动器）

把「内置 Python 运行时 + 运行依赖 + 应用 + GUI」与一个 Rust 编写的 exe 启动器
打成单个 zip：解压到任意目录，双击 `comfy-bridge.exe` 即可启动。不安装、
不联网；整合包**不含 ComfyUI 本体**，数据面由用户已有的本地 ComfyUI 或
Modal 云端提供。

Rust 启动器零第三方依赖（只用 std），cargo 离线可构建；它只做一件事：
定位同级 `runtime/python.exe`，转发参数给 `python -m app.cli`。

## 构建

```powershell
cd backend
uv run python scripts/build_rust_package.py            # 全量：前端 → wheel → 组装 → exe → zip
uv run python scripts/build_rust_package.py --skip-wheel --no-frontend
```

产物：`backend/dist/ComfyBridge-<版本>-win64.zip`。

- 运行依赖直接取自 `backend/.venv`（与测试环境同版本），构建阶段不解析、
  不下载依赖；只有重建 wheel 需要 uv。
- 运行时基目录默认取当前解释器的 `sys.base_prefix`，可用 `CB_BUNDLE_PYTHON` 指定。
- 标准库裁剪 tcl/idle/test 等；site-packages 剔除 pytest/mypy/ruff 等开发工具链。

## 包内结构

```text
ComfyBridge-<版本>/
├── comfy-bridge.exe      # 入口启动器
├── runtime/              # 裁剪版 CPython + site-packages（含 app、GUI 产物、modal-runner）
├── docs/                 # 使用说明手册 + Modal 密钥获取指南（HTML，浏览器直接打开）
└── 使用说明.txt
```

## 使用

```powershell
comfy-bridge.exe --port 8189     # 控制台 http://127.0.0.1:8189/bridge/
comfy-bridge.exe --doctor        # 自检整合包完整性
comfy-bridge.exe --version       # 查看版本
```

- launcher 旗标仅当第一个参数时生效，其余参数原样转发给 CLI。
- `CB_BRIDGE_RUNTIME` 可指定 runtime 目录（默认 exe 同级 `runtime/`）。
- 配置与数据仍在用户目录 `~/.comfybridge`；删除解压目录即彻底卸载。

## 测试

```powershell
cd packager
cargo test --release    # 本机 debug 构建脚本可能被 AV 拦截，统一用 release 验证
```
