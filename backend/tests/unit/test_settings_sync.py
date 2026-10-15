"""设置键级推拉纯逻辑单元测试（不依赖文件与 Modal CLI）。"""

import contextlib

import pytest

from app.api.v1 import settings_sync as api_ss
from app.modal_volume.client import ModalCliError
from app.sync import settings_sync as ss


def test_diff_covers_all_statuses_and_sorted():
    local = {"b": 1, "c": 2, "d": 3}
    cloud = {"a": 9, "b": 1, "c": 8}
    rows = {r["key"]: r for r in ss.diff_settings(local, cloud)}
    assert [r["key"] for r in ss.diff_settings(local, cloud)] == ["a", "b", "c", "d"]  # 排序稳定
    assert rows["a"]["status"] == ss.STATUS_ONLY_CLOUD
    assert rows["b"]["status"] == ss.STATUS_SAME
    assert rows["c"]["status"] == ss.STATUS_DIFF
    assert rows["d"]["status"] == ss.STATUS_ONLY_LOCAL


def test_diff_default_direction_mapping():
    local = {"only_l": 1, "both_diff": 1, "same": 2}
    cloud = {"only_c": 9, "both_diff": 2, "same": 2}
    rows = {r["key"]: r for r in ss.diff_settings(local, cloud)}
    assert rows["only_l"]["default_direction"] == "push"
    assert rows["only_c"]["default_direction"] == "pull"
    assert rows["both_diff"]["default_direction"] == "push"
    assert rows["same"]["default_direction"] is None


def test_diff_deep_values_compared_by_equality():
    rows = {r["key"]: r for r in ss.diff_settings({"k": {"a": 1}}, {"k": {"a": 1}})}
    assert rows["k"]["status"] == ss.STATUS_SAME


def test_sensitive_key_detection():
    assert ss.is_sensitive_key("Comfy.api_key_custom")
    assert ss.is_sensitive_key("SomeNode.Token")
    assert ss.is_sensitive_key("MY_SECRET_VALUE")
    assert not ss.is_sensitive_key("Comfy.UseNewMenu")


def test_mask_secret_format():
    assert ss.mask_secret("k.api_key", "abcdefgh12345678") == "abcde****5678"  # first5+****+last4
    assert ss.mask_secret("k.api_key", "short") == "****"  # 过短全掩码
    assert ss.mask_secret("k.api_key", 12345) == "****"  # 非字符串凭证键同样掩码
    assert ss.mask_secret("Comfy.UseNewMenu", "Disabled") == "Disabled"  # 非凭证键原样
    assert ss.mask_secret("k.api_key", None) == ""
    assert ss.mask_secret("Comfy.Workflow.Persist", True) == "true"  # 布尔按 JSON 风格
    assert ss.mask_secret("Comfy.Workflow.Persist", False) == "false"


def test_apply_push_and_pull_merge():
    local = {"a": 1, "b": 2, "keep": "L"}
    cloud = {"b": 8, "c": 9, "keep": "L"}
    new_local, new_cloud, pushed, pulled, skipped = ss.apply_directions(
        local, cloud, {"a": "push", "b": "pull", "c": "pull"}
    )
    assert pushed == ["a"] and pulled == ["b", "c"] and skipped == []
    assert new_cloud["a"] == 1  # push：本地值写入云端
    assert new_local["b"] == 8  # pull：云端值写入本地
    assert new_local["keep"] == "L" and new_cloud["keep"] == "L"  # 未涉及的键保持原值


def test_apply_skips_missing_and_unknown_direction():
    local = {"a": 1}
    cloud = {"c": 9}
    _nl, _nc, pushed, pulled, skipped = ss.apply_directions(
        local, cloud, {"a": "pull", "c": "push", "ghost": "push", "x": "sideways"}
    )
    assert pushed == [] and pulled == []
    assert sorted(skipped) == ["a", "c", "ghost", "x"]


def test_apply_does_not_mutate_inputs():
    local = {"a": 1}
    cloud = {"a": 2}
    ss.apply_directions(local, cloud, {"a": "push"})
    assert local == {"a": 1} and cloud == {"a": 2}


# ---------------------------------------------------------------- SET-04：云端错误分类


def _tmpdir(monkeypatch, tmp_path):
    """临时目录在受限沙箱下可能 ACL 失败：用 tmp_path 替代，隔离文件系统噪声。"""
    monkeypatch.setattr(
        api_ss.tempfile, "TemporaryDirectory",
        lambda *a, **k: contextlib.nullcontext(str(tmp_path)),
    )


def test_is_missing_file_only_accepts_not_found_mapping():
    missing = ModalCliError(
        "下载 user/default/comfy.settings.json失败：No such file or directory: user/default/comfy.settings.json",
        category="uncertain",
    )
    assert api_ss._is_missing_file(missing)
    auth = ModalCliError(
        "下载 user/default/comfy.settings.json失败：Token not found",
        category="not_logged_in",
    )
    assert not api_ss._is_missing_file(auth)  # "not found" 不再冒充文件缺失
    assert not api_ss._is_missing_file(ModalCliError("超时", category="network"))


@pytest.mark.asyncio
async def test_read_cloud_auth_error_reports_channel_failure(monkeypatch, tmp_path):
    _tmpdir(monkeypatch, tmp_path)

    async def fake_get_file(*args, **kwargs):
        raise ModalCliError(
            "下载 user/default/comfy.settings.json失败：Token not found",
            category="not_logged_in",
        )

    monkeypatch.setattr(api_ss.volume_cli, "get_file", fake_get_file)
    data, exists, err = await api_ss._read_cloud()
    assert data is None  # 通道不可用 → cloud_available=False
    assert exists is False
    assert err and "Token not found" in err


@pytest.mark.asyncio
async def test_read_cloud_missing_file_is_empty_not_error(monkeypatch, tmp_path):
    _tmpdir(monkeypatch, tmp_path)

    async def fake_get_file(*args, **kwargs):
        raise ModalCliError(
            "下载 user/default/comfy.settings.json失败："
            "No such file or directory: user/default/comfy.settings.json",
            category="uncertain",
        )

    monkeypatch.setattr(api_ss.volume_cli, "get_file", fake_get_file)
    data, exists, err = await api_ss._read_cloud()
    assert data == {} and exists is False and err is None
