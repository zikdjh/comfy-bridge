"""真实两端联调测试包（pytest -m live）。

默认 pytest 不收集这里的用例：用例统一用 ``pytest.mark.live`` 标记。
运行前设置 ``CB_LIVE=1`` 并准备真实本地 ComfyUI + Modal Runner：

    cd backend
    $env:CB_LIVE = "1"
    .venv\\Scripts\\python.exe -m pytest -m live -q

可选环境变量见 ``backend/tests/live/conftest.py``。
"""
