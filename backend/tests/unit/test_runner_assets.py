"""runner_assets 双布局解析：仓库布局优先，wheel 安装回落包内副本。"""

from __future__ import annotations

from pathlib import Path

from app import runner_assets


def test_repo_layout_preferred(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "modal-runner").mkdir(parents=True)
    (repo / "modal-runner" / "runner.py").write_text("# runner", encoding="utf-8")
    monkeypatch.setattr(runner_assets, "_REPO_ROOT", repo)
    monkeypatch.setattr(runner_assets, "_PACKAGED_DIR", tmp_path / "packaged")
    assert runner_assets.runner_dir() == repo / "modal-runner"
    assert runner_assets.runner_file("lock", "nodes.lock.yaml") == (
        repo / "modal-runner" / "lock" / "nodes.lock.yaml"
    )


def test_packaged_fallback_without_repo(tmp_path: Path, monkeypatch) -> None:
    pkg = tmp_path / "site-packages" / "app" / "_modal_runner"
    pkg.mkdir(parents=True)
    (pkg / "runner.py").write_text("# runner", encoding="utf-8")
    monkeypatch.setattr(runner_assets, "_REPO_ROOT", tmp_path / "elsewhere")
    monkeypatch.setattr(runner_assets, "_PACKAGED_DIR", pkg)
    assert runner_assets.runner_dir() == pkg
    assert runner_assets.runner_file("volume_ops.py") == pkg / "volume_ops.py"


def test_dev_defaults_resolve_into_repo() -> None:
    # 开发布局默认即仓库根 modal-runner（模块常量不 monkeypatch 时的真实解析）
    d = runner_assets.runner_dir()
    assert d.name == "modal-runner"
    assert (d / "runner.py").is_file()
