"""modal CLI 输出 quirk 修复：volume ls --json 对非 ASCII 文件名的双重转义还原。"""

from dataclasses import dataclass

import pytest

from app.modal_volume import client as modal_cli
from app.modal_volume.client import (
    ModalCliError,
    _humanize_filesize,
    _map_sdk_error,
    _normalize_entry,
    _unescape_cli_name,
)


@dataclass
class _Entry:
    path: str
    type: int
    mtime: int
    size: int


def test_humanize_filesize_matches_cli_format():
    assert _humanize_filesize(0) == "0 B"
    assert _humanize_filesize(6) == "6 B"
    assert _humanize_filesize(1258291) == "1.2 MiB"
    assert _humanize_filesize(1024**4) == "1.0 TiB"


def test_normalize_entry_matches_cli_shape_and_adds_int_fields():
    row = _normalize_entry(_Entry(path="/models/demo.safetensors", type=1, mtime=1_700_000_000, size=6))
    assert row["name"] == "demo.safetensors"
    assert row["type"] == "file"
    assert row["size"] == "6 B"
    assert row["size_bytes"] == 6
    assert row["mtime_s"] == 1_700_000_000
    assert row["mtime"]


def test_normalize_entry_type_mapping_and_empty_mtime():
    assert _normalize_entry(_Entry(path="/a/b", type=2, mtime=0, size=0))["type"] == "dir"
    assert _normalize_entry(_Entry(path="/a/link", type=3, mtime=0, size=0))["type"] == "link"
    assert _normalize_entry(_Entry(path="/a/sock", type=5, mtime=0, size=0))["type"] == "socket"
    assert _normalize_entry(_Entry(path="/a/b", type=1, mtime=0, size=0))["mtime"] == ""


def test_localized_datetime_json_matches_cli_json_shape():
    value = modal_cli._localized_datetime_json(1_700_000_000)
    assert value
    assert value[4] == "-" and value[10] == " "
    assert modal_cli._localized_datetime_json(0) == ""


def test_sdk_error_mapping_keeps_stable_categories():
    class ResourceExhaustedError(Exception):
        pass

    class PermissionDeniedError(Exception):
        pass

    class AuthError(Exception):
        pass

    rate = _map_sdk_error(ResourceExhaustedError("429"), "列出数据卷")
    assert rate.category == "rate_limited" and rate.retryable is True
    denied = _map_sdk_error(PermissionDeniedError("denied"), "列出数据卷")
    assert denied.category == "permission" and denied.retryable is False
    auth = _map_sdk_error(AuthError("bad token"), "获取客户端")
    assert auth.category == "not_logged_in"
    unknown = _map_sdk_error(RuntimeError("boom"), "列出数据卷")
    assert unknown.category == "command" and unknown.retryable is False


def test_not_found_mapping_keeps_legacy_string_marker():
    class NotFoundError(Exception):
        pass

    error = _map_sdk_error(NotFoundError("volume missing"), "列出数据卷")
    assert error.category == "uncertain" and error.retryable is False
    assert "No such file or directory" in str(error)


def test_unescapes_double_escaped_chinese():
    # CLI 实测输出：json.loads 一次后名字仍是字面 backslash-u 序列
    assert _unescape_cli_name("krea2-\\u6587\\u751f\\u56fe.json") == "krea2-文生图.json"


def test_keeps_plain_ascii_and_real_chinese():
    assert _unescape_cli_name("plain-node.json") == "plain-node.json"
    assert _unescape_cli_name("krea2-文生图.json") == "krea2-文生图.json"


def test_keeps_invalid_sequences_and_lone_surrogates():
    # 非 hex 序列不动
    assert _unescape_cli_name("weird-\\uzzzz.json") == "weird-\\uzzzz.json"
    # 代理对拆出的孤立代理项无法编码 utf-8 → 保留原样，不产生坏字符串
    lone = "emoji-\\ud83d.json"
    assert _unescape_cli_name(lone) == lone


def test_error_classification_covers_all_categories():
    assert modal_cli._classify_error("Token validation failed")[0] == "not_logged_in"
    assert modal_cli._classify_error("Permission denied")[0] == "permission"
    category, retryable = modal_cli._classify_error("Connection timed out")
    assert category == "network" and retryable is True
    assert modal_cli._classify_error("some unknown CLI error")[0] == "command"
    assert modal_cli._classify_error("killed", timed_out=True)[0] == "uncertain"


def test_rate_limit_is_retryable():
    """VolumeListFiles rate limit → 平台限流分类，只读命令可退避重试。"""
    category, retryable = modal_cli._classify_error(
        "VolumeListFiles rate limit exceeded. Please wait and retry."
    )
    assert category == "rate_limited" and retryable is True
    assert modal_cli._classify_error("HTTP 429 Too Many Requests")[0] == "rate_limited"
    error = ModalCliError("rate limit exceeded", category="rate_limited", command="modal volume ls")
    assert error.label == "平台限流"
    assert "限流" in error.next_action


def test_error_payload_has_next_action():
    error = ModalCliError("bad", category="not_logged_in", command="modal app list")
    payload = error.to_dict()
    assert payload["category"] == "not_logged_in"
    assert payload["label"] == "未登录"
    assert "modal token new" in payload["next_action"]
    assert payload["command"] == "modal app list"


async def test_run_retries_read_only_network_error(monkeypatch):
    calls = {"n": 0}

    async def fake_once(*args, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModalCliError("connection timed out", category="network", retryable=True)
        return "ok\n"

    monkeypatch.setattr(modal_cli, "_run_once", fake_once)
    monkeypatch.setattr(modal_cli.asyncio, "sleep", lambda _: _noop())
    assert await modal_cli.run("app", "list", "--json") == "ok\n"
    assert calls["n"] == 2


async def test_run_does_not_retry_write_command(monkeypatch):
    calls = {"n": 0}

    async def fake_once(*args, timeout):
        calls["n"] += 1
        raise ModalCliError("connection timed out", category="network", retryable=True)

    monkeypatch.setattr(modal_cli, "_run_once", fake_once)
    with pytest.raises(ModalCliError):
        await modal_cli.run("volume", "put", "vol", "local", "remote")
    assert calls["n"] == 1


async def test_run_rate_limited_retries_with_backoff_then_recovers(monkeypatch):
    """限流：前两次 rate limit、第三次成功 → 共 3 次调用，退避按 1s/4s 序列。"""
    calls = {"n": 0, "delays": []}

    async def fake_once(*args, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ModalCliError(
                "VolumeListFiles rate limit exceeded.", category="rate_limited", retryable=True
            )
        return "ok\n"

    async def fake_sleep(delay):
        calls["delays"].append(delay)

    monkeypatch.setattr(modal_cli, "_run_once", fake_once)
    monkeypatch.setattr(modal_cli.asyncio, "sleep", fake_sleep)
    assert await modal_cli.run("volume", "ls", "vol", "--json") == "ok\n"
    assert calls["n"] == 3
    assert calls["delays"] == [1.0, 4.0]


async def test_run_rate_limited_exhausts_backoff_and_raises(monkeypatch):
    """限流持续：1s/4s/10s 三次重试后仍失败 → 抛出最后一次错误（共 4 次调用）。"""
    calls = {"n": 0}

    async def fake_once(*args, timeout):
        calls["n"] += 1
        raise ModalCliError("rate limit exceeded", category="rate_limited", retryable=True)

    monkeypatch.setattr(modal_cli, "_run_once", fake_once)
    monkeypatch.setattr(modal_cli.asyncio, "sleep", lambda _: _noop())
    with pytest.raises(ModalCliError):
        await modal_cli.run("volume", "ls", "vol", "--json")
    assert calls["n"] == 4


async def test_run_records_success_and_failure(monkeypatch):
    modal_cli._clear_diagnostics()

    async def ok(*args, timeout):
        modal_cli._record_success()
        return "ok"

    monkeypatch.setattr(modal_cli, "_run_once", ok)
    await modal_cli.run("app", "list")
    assert modal_cli.diagnostics()["last_success_ms"] is not None

    async def fail(*args, timeout):
        error = ModalCliError("permission denied", category="permission")
        modal_cli._record_failure(error)
        raise error

    monkeypatch.setattr(modal_cli, "_run_once", fail)
    with pytest.raises(ModalCliError):
        await modal_cli.run("app", "list")
    assert modal_cli.diagnostics()["last_failure"]["category"] == "permission"


async def _noop():
    return None


def test_read_only_command_detection():
    assert modal_cli._is_read_only(("app", "list", "--json"))
    assert modal_cli._is_read_only(("app", "logs", "app-name"))
    assert modal_cli._is_read_only(("volume", "ls", "vol", "--json"))
    assert modal_cli._is_read_only(("volume", "get", "vol", "remote", "local"))
    assert modal_cli._is_read_only(("billing", "summary", "--json"))
    assert not modal_cli._is_read_only(("volume", "put", "vol", "a", "b"))
    assert not modal_cli._is_read_only(("volume", "rm", "vol", "a"))
    assert not modal_cli._is_read_only(("volume", "delete", "vol", "-y"))
    assert not modal_cli._is_read_only(("app", "shell", "app-name"))
    assert not modal_cli._is_read_only(("image", "rm", "img"))
    assert not modal_cli._is_read_only(("billing",))


def test_cli_env_forces_utf8_cli_output(monkeypatch):
    """Windows 中文控制台默认 GBK：CLI 成功路径打印 ✓/🔨 会以非零码退出，必须强制 UTF-8。"""
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")
    env = modal_cli.cli_env({"EXTRA": "1"})
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONWARNINGS"] == "ignore"
    assert env["EXTRA"] == "1"


def test_cli_env_layers_credentials_below_extra(monkeypatch):
    monkeypatch.setattr(modal_cli, "auth_env", lambda: {"MODAL_TOKEN_ID": "ak-test"})
    env = modal_cli.cli_env({"MODAL_TOKEN_ID": "override"})
    assert env["MODAL_TOKEN_ID"] == "override"
    assert modal_cli.cli_env()["MODAL_TOKEN_ID"] == "ak-test"


def test_popen_builds_cli_env(monkeypatch):
    captured: dict = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(modal_cli.subprocess, "Popen", fake_popen)
    modal_cli._popen("volume", "ls", "vol", "--json")
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
