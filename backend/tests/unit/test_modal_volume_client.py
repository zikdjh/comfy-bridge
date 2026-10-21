"""Modal SDK 网关：形状归一、错误映射、状态/账单与 CLI 保留面。"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pytest

from app.modal_volume import client as modal_cli
from app.modal_volume.client import (
    ModalCliError,
    _app_state_name,
    _decimal_float,
    _humanize_filesize,
    _map_sdk_error,
    _normalize_entry,
)


@dataclass
class _Entry:
    path: str
    type: int
    mtime: int
    size: int


def _reset_list_dir_state() -> None:
    for request in modal_cli._list_dir_inflight.values():
        if request.task is not None and not request.task.done():
            request.task.cancel()
    modal_cli._list_dir_cache.clear()
    modal_cli._list_dir_inflight.clear()
    modal_cli._list_dir_gate = None
    modal_cli._list_dir_gate_loop = None


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


def test_file_not_found_mapping_keeps_legacy_string_marker():
    """SDK read_file 把 NotFoundError 转成 FileNotFoundError，网关仍需保留旧标记。"""
    error = _map_sdk_error(FileNotFoundError("missing"), "下载文件")
    assert error.category == "uncertain" and error.retryable is False
    assert "No such file or directory" in str(error)


def test_normalize_remote_expands_directory_suffix(tmp_path):
    local = tmp_path / "a.txt"
    local.write_text("x", encoding="utf-8")
    assert modal_cli._normalize_remote(local, "/input/") == "/input/a.txt"


async def test_put_file_uses_batch_upload_and_waits_whole_context(monkeypatch, tmp_path):
    local = tmp_path / "a.txt"
    local.write_text("x", encoding="utf-8")
    calls: list[tuple] = []

    class _Batch:
        async def __aenter__(self):
            calls.append(("enter",))
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            calls.append(("exit", exc_type))

        def put_file(self, local_file, remote_path):
            calls.append(("put", str(local_file), remote_path))

    class _Handle:
        class _Upload:
            def aio(self, *, force: bool):
                calls.append(("force", force))
                return _Batch()

        batch_upload = _Upload()

    async def fake_get_volume(volume=None):
        return _Handle()

    monkeypatch.setattr(modal_cli, "_get_volume", fake_get_volume)
    await modal_cli.put_file(local, "/input/", timeout=3)
    assert calls == [
        ("force", True),
        ("enter",),
        ("put", str(local), "/input/a.txt"),
        ("exit", None),
    ]


async def test_put_file_maps_error_raised_on_batch_exit(monkeypatch, tmp_path):
    local = tmp_path / "a.txt"
    local.write_text("x", encoding="utf-8")

    class ResourceExhaustedError(Exception):
        pass

    class _Batch:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            raise ResourceExhaustedError("rate limited")

        def put_file(self, local_file, remote_path):
            return None

    class _Handle:
        class _Upload:
            def aio(self, *, force: bool):
                return _Batch()

        batch_upload = _Upload()

    async def fake_get_volume(volume=None):
        return _Handle()

    monkeypatch.setattr(modal_cli, "_get_volume", fake_get_volume)
    with pytest.raises(ModalCliError) as ei:
        await modal_cli.put_file(local, "input/a.txt")
    assert ei.value.category == "rate_limited"
    assert ei.value.retryable is True


async def test_get_file_writes_atomically_and_overwrites(monkeypatch, tmp_path):
    destination = tmp_path / "nested" / "a.txt"
    destination.parent.mkdir()
    destination.write_text("old", encoding="utf-8")
    reads: list[str] = []

    class _Entry:
        path = "input/a.txt"  # SDK FileEntry.path 不带前导斜杠
        type = 1

    class _Handle:
        class _Listdir:
            async def aio(self, path, recursive=False):
                return [_Entry()]

        listdir = _Listdir()

        class _Read:
            async def aio(self, path, fileobj):
                reads.append(path)
                fileobj.write(b"new")
                return 3

        read_file_into_fileobj = _Read()

    async def fake_get_volume(volume=None):
        return _Handle()

    monkeypatch.setattr(modal_cli, "_get_volume", fake_get_volume)
    await modal_cli.get_file("input/a.txt", destination, force=True)
    assert destination.read_bytes() == b"new"
    assert reads == ["input/a.txt"]


async def test_get_file_absolute_remote_writes_file_destination(monkeypatch, tmp_path):
    """CLI `volume get vol /file local-file` 的本地目标按文件名落盘，不展开成目录。"""
    destination = tmp_path / "downloaded.txt"

    class _Entry:
        path = "smoke/a.txt"
        type = 1

    class _Handle:
        class _Listdir:
            async def aio(self, path, recursive=False):
                assert path == "/smoke/a.txt"
                return [_Entry()]

        listdir = _Listdir()

        class _Read:
            async def aio(self, path, fileobj):
                assert path == "smoke/a.txt"
                fileobj.write(b"new")
                return 3

        read_file_into_fileobj = _Read()

    async def fake_get_volume(volume=None):
        return _Handle()

    monkeypatch.setattr(modal_cli, "_get_volume", fake_get_volume)
    await modal_cli.get_file("/smoke/a.txt", destination, force=True)
    assert destination.read_bytes() == b"new"
    assert not (destination / "smoke").exists()


async def test_list_dir_missing_prefix_matches_cli_empty_directory(monkeypatch):
    modal_cli._clear_diagnostics()

    class NotFoundError(Exception):
        pass

    class _Handle:
        class _Listdir:
            async def aio(self, path, recursive=False):
                # 对齐 modal.exception.NotFoundError：名称匹配、不是内建 FileNotFoundError。
                raise NotFoundError("No such file or directory")

        listdir = _Listdir()

    async def fake_get_volume(volume=None):
        return _Handle()

    monkeypatch.setattr(modal_cli, "_get_volume", fake_get_volume)
    assert await modal_cli.list_dir("/removed", volume="vol") == []
    assert modal_cli.diagnostics()["last_failure"] is None


async def test_list_dir_single_flight_caches_shared_result(monkeypatch):
    _reset_list_dir_state()
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_uncached(_remote_path, _volume, _timeout):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [{"name": "shared.txt", "type": "file"}]

    monkeypatch.setattr(modal_cli, "_list_dir_uncached", fake_uncached)
    first = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    await started.wait()
    second = asyncio.create_task(modal_cli.list_dir("models/", volume="vol"))
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(first, second) == [
        [{"name": "shared.txt", "type": "file"}],
        [{"name": "shared.txt", "type": "file"}],
    ]
    assert calls == 1

    assert (await modal_cli.list_dir("/models", volume="vol"))[0]["name"] == "shared.txt"
    assert calls == 1


async def test_list_dir_limits_distinct_concurrent_reads(monkeypatch):
    _reset_list_dir_state()
    active = 0
    peak = 0
    release = asyncio.Event()

    class _Handle:
        class _Listdir:
            async def aio(self, _path):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await release.wait()
                    return []
                finally:
                    active -= 1

        listdir = _Listdir()

    async def fake_get_volume(_volume=None):
        return _Handle()

    monkeypatch.setattr(modal_cli, "_get_volume", fake_get_volume)
    tasks = [
        asyncio.create_task(modal_cli.list_dir(f"/dir-{index}", volume="vol"))
        for index in range(12)
    ]
    await asyncio.sleep(0.05)
    assert peak == modal_cli._LIST_DIR_CONCURRENCY
    release.set()
    assert await asyncio.gather(*tasks) == [[] for _ in tasks]
    assert peak == modal_cli._LIST_DIR_CONCURRENCY


async def test_list_dir_invalidation_does_not_backfill_old_result(monkeypatch):
    _reset_list_dir_state()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_uncached(_remote_path, _volume, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return [{"name": "old.txt"}]
        return [{"name": "new.txt"}]

    monkeypatch.setattr(modal_cli, "_list_dir_uncached", fake_uncached)
    old = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    await started.wait()
    modal_cli._invalidate_list_dir("/models", "vol")
    release.set()
    assert (await old)[0]["name"] == "new.txt"
    assert (await modal_cli.list_dir("/models", volume="vol"))[0]["name"] == "new.txt"
    assert calls == 2


async def test_list_dir_invalidation_then_immediate_read_uses_new_request(monkeypatch):
    _reset_list_dir_state()
    first_started = asyncio.Event()
    release_old = asyncio.Event()
    calls = 0

    async def fake_uncached(_remote_path, _volume, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_old.wait()
            return [{"name": "stale.txt"}]
        return [{"name": "fresh.txt"}]

    monkeypatch.setattr(modal_cli, "_list_dir_uncached", fake_uncached)
    stale = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    await first_started.wait()
    modal_cli._invalidate_list_dir("/models", "vol")
    fresh = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    assert (await fresh)[0]["name"] == "fresh.txt"
    release_old.set()
    assert (await stale)[0]["name"] == "fresh.txt"
    assert calls == 2


async def test_list_dir_failure_is_not_cached(monkeypatch):
    _reset_list_dir_state()
    calls = 0

    async def fake_uncached(_remote_path, _volume, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModalCliError("boom", category="command")
        return [{"name": "recovered.txt"}]

    monkeypatch.setattr(modal_cli, "_list_dir_uncached", fake_uncached)
    with pytest.raises(ModalCliError):
        await modal_cli.list_dir("/models", volume="vol")
    assert (await modal_cli.list_dir("/models", volume="vol"))[0]["name"] == "recovered.txt"
    assert calls == 2


async def test_list_dir_timeout_budget_covers_invalidation_retry(monkeypatch):
    _reset_list_dir_state()
    loop = asyncio.get_running_loop()
    calls = 0

    async def fake_uncached(_remote_path, _volume, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            modal_cli._invalidate_list_dir("/models", "vol")
        await asyncio.sleep(0.03)
        return []

    monkeypatch.setattr(modal_cli, "_list_dir_uncached", fake_uncached)
    started = loop.time()
    assert await modal_cli.list_dir("/models", volume="vol", timeout=0.05) == []
    assert calls == 2
    assert loop.time() - started < 0.2


async def test_list_dir_waiter_that_started_after_invalidation_retries(monkeypatch):
    _reset_list_dir_state()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_uncached(_remote_path, _volume, _timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return [{"name": f"result-{calls}.txt"}]

    monkeypatch.setattr(modal_cli, "_list_dir_uncached", fake_uncached)
    first = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    await started.wait()
    modal_cli._invalidate_list_dir("/models", "vol")
    await asyncio.sleep(0)
    second = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    release.set()
    assert (await first)[0]["name"] == "result-2.txt"
    assert (await second)[0]["name"] == "result-2.txt"
    assert calls == 2


async def test_cancelling_one_list_dir_waiter_does_not_cancel_shared_read(monkeypatch):
    _reset_list_dir_state()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_uncached(_remote_path, _volume, _timeout):
        started.set()
        await release.wait()
        return [{"name": "shared.txt"}]

    monkeypatch.setattr(modal_cli, "_list_dir_uncached", fake_uncached)
    first = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    await started.wait()
    second = asyncio.create_task(modal_cli.list_dir("/models", volume="vol"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == [{"name": "shared.txt"}]


async def test_volume_delete_forgets_cached_handle(monkeypatch):
    forgotten: list[str] = []
    monkeypatch.setattr(modal_cli, "_forget_volume", forgotten.append)

    class _Delete:
        async def aio(self, name, *, client=None):
            return None

    class _Objects:
        delete = _Delete()

    class _Volume:
        objects = _Objects()

    class _Modal:
        Volume = _Volume

    async def fake_client():
        return object()

    monkeypatch.setattr(modal_cli, "_get_client", fake_client)
    monkeypatch.setattr(modal_cli, "_get_modal", lambda: _Modal)
    await modal_cli.volume_delete("old-volume")
    assert forgotten == ["old-volume"]


def test_rate_limit_is_retryable():
    """SDK ResourceExhaustedError → 平台限流分类，只读操作可退避重试。"""
    error = _map_sdk_error(RuntimeError("rate limited"), "列出数据卷")
    assert error.category == "command"
    error = ModalCliError("rate limit exceeded", category="rate_limited")
    assert error.label == "平台限流"
    assert "限流" in error.next_action


def test_error_payload_has_next_action():
    error = ModalCliError("bad", category="not_logged_in")
    payload = error.to_dict()
    assert payload["category"] == "not_logged_in"
    assert payload["label"] == "未登录"
    assert "modal token new" in payload["next_action"]
    assert payload["command"] is None


async def test_sdk_read_only_op_retries_network_error(monkeypatch):
    calls = {"n": 0}

    async def fake_op():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModalCliError("connection timed out", category="network", retryable=True)
        return "ok"

    monkeypatch.setattr(modal_cli.asyncio, "sleep", lambda _: _noop())
    assert await modal_cli._with_retry("app_status", fake_op) == "ok"
    assert calls["n"] == 2


async def test_sdk_write_op_does_not_retry(monkeypatch):
    calls = {"n": 0}

    async def fake_op():
        calls["n"] += 1
        raise ModalCliError("connection timed out", category="network", retryable=True)

    monkeypatch.setattr(modal_cli.asyncio, "sleep", lambda _: _noop())
    with pytest.raises(ModalCliError):
        await modal_cli._with_retry("put_file", fake_op)
    assert calls["n"] == 1


async def test_sdk_rate_limited_retries_with_backoff_then_recovers(monkeypatch):
    """限流：前两次 rate limit、第三次成功 → 共 3 次调用，退避按 1s/2s 序列。"""
    calls = {"n": 0, "delays": []}

    async def fake_op():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ModalCliError(
                "VolumeListFiles rate limit exceeded.", category="rate_limited", retryable=True
            )
        return "ok"

    async def fake_sleep(delay):
        calls["delays"].append(delay)

    monkeypatch.setattr(modal_cli.asyncio, "sleep", fake_sleep)
    assert await modal_cli._with_retry("list_dir", fake_op) == "ok"
    assert calls["n"] == 3
    assert calls["delays"] == [1.0, 2.0]


async def test_sdk_rate_limited_exhausts_backoff_and_raises(monkeypatch):
    """限流持续：1s/2s 两次重试后仍失败 → 抛出最后一次错误（共 3 次调用）。"""
    calls = {"n": 0}

    async def fake_op():
        calls["n"] += 1
        raise ModalCliError("rate limit exceeded", category="rate_limited", retryable=True)

    monkeypatch.setattr(modal_cli.asyncio, "sleep", lambda _: _noop())
    with pytest.raises(ModalCliError):
        await modal_cli._with_retry("list_dir", fake_op)
    assert calls["n"] == 3


async def _noop():
    return None


def test_app_state_golden_matches_cli():
    assert _app_state_name(3) == "deployed"
    assert _app_state_name(5) == "stopped"
    assert _app_state_name(1) == "ephemeral"
    assert _app_state_name(2) == "ephemeral (detached)"
    assert _app_state_name(8) == "ephemeral (detached)"
    assert _app_state_name(6) == "initializing..."
    assert _app_state_name(4) == "stopping..."
    assert _app_state_name(7) == "disabled"
    assert _app_state_name(0) == "unknown"
    assert _app_state_name(99) == "unknown"
    assert _app_state_name("bad") == "unknown"


def test_decimal_float_rounds_billing_values():
    assert _decimal_float(Decimal("1.23456")) == 1.2346
    assert _decimal_float("0E-8") == 0.0
    assert _decimal_float(None) is None


async def test_app_status_matches_app_and_environment(monkeypatch):
    captured: dict = {}

    class _Stub:
        # retry/timeout 必须显式传入：RPC 级 deadline 是 app_status 的契约之一，
        # 裸调 stub（不带 kwargs）的回归会在此 TypeError 失败。
        async def AppList(self, request, *, retry, timeout):
            captured["retry"] = retry
            captured["timeout"] = timeout
            return _Response()

    class _Response:
        apps: ClassVar[list] = [
            type("App", (), {"description": "other", "state": 3})(),
            type("App", (), {"description": "comfyui-l40s", "state": 5})(),
        ]

    class _Client:
        stub = _Stub()

    async def fake_client():
        return _Client()

    class _ModalCfg:
        environment = "main"
        app_name = "comfyui-l40s"

    class _Settings:
        modal = _ModalCfg()

    def fake_request(**kwargs):
        captured["request_kwargs"] = dict(kwargs)
        return object()

    class _Proto:
        AppListRequest = staticmethod(fake_request)

    def fake_blocking():
        # asyncio.run 只能在线程里跑：若 app_status 退化为在事件循环上直接调用
        # 同步入口（阻塞 loop），这里会抛 "cannot be called from a running event
        # loop"，从而守住「RPC 必须经 to_thread 送入 SDK 循环」的调用形态。
        def _run(client, request, *, timeout):
            return asyncio.run(client.stub.AppList(request, retry=None, timeout=timeout))

        return _run

    monkeypatch.setitem(__import__("sys").modules, "modal_proto", type("P", (), {"api_pb2": _Proto})())
    monkeypatch.setattr(modal_cli, "_get_client", fake_client)
    monkeypatch.setattr(modal_cli, "_app_list_blocking", fake_blocking)
    monkeypatch.setattr(modal_cli, "get_settings", lambda: _Settings())
    monkeypatch.setattr(modal_cli, "_assert_app_proto_contract", lambda: None)
    assert await modal_cli.app_status("comfyui-l40s") == "stopped"
    assert captured["retry"] is None
    assert captured["timeout"] == 20.0
    assert captured["request_kwargs"] == {"environment_name": "main"}


async def test_billing_summary_uses_aio_and_decimal(monkeypatch):
    calls = {"aio": 0}

    class _Summary:
        metered_cost = Decimal("5.96977888")
        billed_cost = Decimal("0E-8")
        adjustments: ClassVar[dict] = {"plan_cost": Decimal(0), "credits": Decimal("-5.5")}
        metered_cost_breakdown: ClassVar[dict] = {
            "llm_tokens": Decimal(0),
            "deployed_apps": Decimal("4.38552567"),
            "volumes": Decimal("0.46977888"),
        }

    class _Billing:
        class _SummaryCall:
            async def aio(self, **kwargs):
                calls["aio"] += 1
                assert kwargs == {"cycle": "2026-10"}
                return _Summary()

        summary = _SummaryCall()

    class _Workspace:
        billing = _Billing()

    class _Modal:
        Workspace = type("W", (), {"from_context": staticmethod(lambda **kw: _Workspace())})

    async def fake_client():
        return object()

    monkeypatch.setattr(modal_cli, "_get_modal", lambda: _Modal)
    monkeypatch.setattr(modal_cli, "_get_client", fake_client)
    data = await modal_cli.billing_summary("2026-10")
    assert calls["aio"] == 1
    assert data["metered_cost_usd"] == pytest.approx(5.9698)
    assert data["billed_cost_usd"] == 0.0
    assert data["credits_applied_usd"] == pytest.approx(5.5)
    assert data["breakdown"] == {"deployed_apps": 4.3855, "volumes": 0.4698}


async def test_billing_report_uses_aio_and_aggregates_decimal(monkeypatch):
    calls = {"aio": 0}

    class _Billing:
        class _ReportCall:
            async def aio(self, **kwargs):
                calls["aio"] += 1
                assert kwargs["start"] == datetime(2026, 9, 1, tzinfo=UTC)
                assert kwargs["end"] == datetime(2026, 10, 1, tzinfo=UTC)
                assert kwargs["resolution"] == "d"
                return [
                    type("Row", (), {
                        "object_id": "ap-1",
                        "description": "comfyui-l40s",
                        "interval_start": datetime(2026, 9, 1, tzinfo=UTC),
                        "cost_by_resource": {
                            "L40S": Decimal("0.72782441"),
                            "Memory": Decimal("0.09555028"),
                        },
                    })(),
                    type("Row", (), {
                        "object_id": "ap-1",
                        "description": "comfyui-l40s",
                        "interval_start": datetime(2026, 9, 2, tzinfo=UTC),
                        "cost_by_resource": {"CPU": Decimal("0.07")},
                    })(),
                ]

        report = _ReportCall()

    class _Workspace:
        billing = _Billing()

    class _Modal:
        Workspace = type("W", (), {"from_context": staticmethod(lambda **kw: _Workspace())})

    async def fake_client():
        return object()

    monkeypatch.setattr(modal_cli, "_get_modal", lambda: _Modal)
    monkeypatch.setattr(modal_cli, "_get_client", fake_client)
    data = await modal_cli.billing_report(
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert calls["aio"] == 1
    assert data["gpu_usd"] == pytest.approx(0.7278)
    assert data["total_usd"] == pytest.approx(0.8934)
    assert list(data["resources"]) == ["L40S", "Memory", "CPU"]
    assert data["daily"]["2026-09-01"]["gpu_usd"] == pytest.approx(0.7278)
    assert data["apps"] == [{"name": "comfyui-l40s", "usd": pytest.approx(0.8934)}]


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


def test_cli_env_strips_proxy_by_default(monkeypatch):
    """默认关闭系统代理：CLI 子进程剥掉 HTTP(S)_PROXY 并强制 NO_PROXY=*。"""
    from app.core.config import AppConfig, BridgeConfig

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    settings = AppConfig(bridge=BridgeConfig(use_system_proxy=False))
    monkeypatch.setattr(modal_cli, "get_settings", lambda: settings)
    env = modal_cli.cli_env()
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert env["NO_PROXY"] == "*"


def test_cli_env_keeps_proxy_when_use_system_proxy(monkeypatch):
    """use_system_proxy=True：保留系统代理变量供 CLI 自行处理。"""
    from app.core.config import AppConfig, BridgeConfig

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    settings = AppConfig(bridge=BridgeConfig(use_system_proxy=True))
    monkeypatch.setattr(modal_cli, "get_settings", lambda: settings)
    env = modal_cli.cli_env()
    assert env["HTTP_PROXY"] == "http://127.0.0.1:9"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:9"


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
