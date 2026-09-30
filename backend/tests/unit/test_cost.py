"""成本记账与预算护栏单元测试（临时库：monkeypatch store 路径，不碰真实 data/）。"""

import json
from types import SimpleNamespace

import pytest

from app import cost as cost_svc
from app.cost import modal_billing
from app.logs import store


class _Bridge:
    def __init__(self, **kw):
        self.monthly_budget_usd = kw.get("monthly_budget_usd", 0.0)
        self.on_budget_exceed = kw.get("on_budget_exceed", "fallback_local")
        self.gpu_hourly_rate_usd = kw.get("gpu_hourly_rate_usd", 1.95)


def _patch_budget_settings(monkeypatch, **kw):
    fake = SimpleNamespace(bridge=_Bridge(**kw))
    monkeypatch.setattr(cost_svc, "get_settings", lambda: fake)
    return fake


def _insert(backend: str, est: float | None, gpu: float | None, created) -> str:
    created_ms = int(created.timestamp() * 1000)
    pid = f"pid-{created_ms}-{backend}"
    store.insert_request({
        "prompt_id": pid,
        "created_at": created.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "created_ms": created_ms,
        "backend": backend,
        "final_backend": backend,
        "gen_type": "image",
        "workflow_name": None,
        "graph_hash": None,
        "graph_file": None,
        "client_id": None,
        "status": "done",
        "rule_trace": None,
        "error": None,
    })
    if est is not None:
        store.record_cost(pid, gpu, est)
    return pid


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bridge.sqlite3")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store._conn = None
    store.init_db()
    yield store
    store.close_db()


# ---------------------------------------------------------------- 记账与聚合
def test_record_cost_and_totals(tmp_db):
    from datetime import datetime, timedelta

    now = datetime.now()
    _insert("modal", 0.5, 900.0, now - timedelta(days=2))
    _insert("modal", 0.25, 450.0, now)
    _insert("local", None, None, now)  # 本地请求不产生成本记录

    totals = store.cost_totals()
    assert totals["requests"] == 2
    assert totals["gpu_seconds"] == pytest.approx(1350.0)
    assert totals["est_cost_usd"] == pytest.approx(0.75)

    start_of_day = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    today = store.cost_totals(start_of_day)
    assert today["requests"] == 1
    assert today["est_cost_usd"] == pytest.approx(0.25)


def test_cost_daily_groups_by_local_date(tmp_db):
    from datetime import datetime, timedelta

    now = datetime.now()
    _insert("modal", 0.10, 100.0, now - timedelta(days=1, hours=2))
    _insert("modal", 0.20, 200.0, now - timedelta(days=1, hours=-1))
    _insert("modal", 0.40, 400.0, now)

    daily = store.cost_daily(30)
    assert len(daily) == 2
    assert daily[0]["date"] == (now - timedelta(days=1)).strftime("%Y-%m-%d")
    assert daily[0]["requests"] == 2
    assert daily[0]["est_cost_usd"] == pytest.approx(0.30)
    assert daily[-1]["date"] == now.strftime("%Y-%m-%d")
    assert daily[-1]["gpu_seconds"] == pytest.approx(400.0)


def test_record_cost_idempotent_overwrite(tmp_db):
    from datetime import datetime

    pid = _insert("modal", 0.5, 900.0, datetime.now())
    store.record_cost(pid, 10.0, 0.01)  # 同 prompt_id 重复入账 = 覆盖
    assert store.cost_totals()["est_cost_usd"] == pytest.approx(0.01)


# ---------------------------------------------------------------- 旧库迁移
def test_migrates_legacy_db_without_cost_columns(monkeypatch, tmp_path):
    import sqlite3

    db = tmp_path / "bridge.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS requests ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_id TEXT UNIQUE,"
        " created_at TEXT NOT NULL, created_ms INTEGER NOT NULL,"
        " backend TEXT NOT NULL, final_backend TEXT NOT NULL, gen_type TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'queued');"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(store, "DB_PATH", db)
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store._conn = None
    try:
        store.init_db()
        cols = {r["name"] for r in store._get_conn().execute("PRAGMA table_info(requests)")}
        assert {"gpu_seconds", "est_cost_usd"} <= cols
    finally:
        store.close_db()


# ---------------------------------------------------------------- 成本服务
def test_est_cost_usd_math(monkeypatch):
    assert cost_svc.est_cost_usd(3600, rate=1.95) == pytest.approx(1.95)
    assert cost_svc.est_cost_usd(90, rate=1.95) == pytest.approx(0.04875)
    assert cost_svc.est_cost_usd(0) == 0.0
    assert cost_svc.est_cost_usd(-5, rate=1.95) == 0.0
    _patch_budget_settings(monkeypatch)
    assert cost_svc.est_cost_usd(7200) == pytest.approx(3.9)  # 默认读配置单价


def test_budget_state_thresholds(tmp_db, monkeypatch):
    from datetime import datetime

    _patch_budget_settings(monkeypatch, monthly_budget_usd=0.5)
    _insert("modal", 0.49, 900.0, datetime.now())
    state = cost_svc.budget_state()
    assert state["enabled"] is True
    assert state["exceeded"] is False
    assert state["remaining_usd"] == pytest.approx(0.01)

    _insert("modal", 0.02, 100.0, datetime.now())
    state = cost_svc.budget_state()
    assert state["exceeded"] is True  # 0.51 >= 0.5
    assert state["remaining_usd"] == 0.0


def test_budget_state_disabled_when_limit_zero(tmp_db, monkeypatch):
    from datetime import datetime

    _patch_budget_settings(monkeypatch, monthly_budget_usd=0)
    _insert("modal", 10.0, 9000.0, datetime.now())
    state = cost_svc.budget_state()
    assert state["enabled"] is False
    assert state["exceeded"] is False
    assert state["remaining_usd"] is None


def test_summary_shape(tmp_db, monkeypatch):
    from datetime import datetime

    _patch_budget_settings(monkeypatch, monthly_budget_usd=30.0)
    _insert("modal", 1.0, 3600.0, datetime.now())
    s = cost_svc.summary()
    assert s["gpu_type"] == cost_svc.GPU_TYPE
    assert s["today"]["requests"] == 1
    assert s["month"]["est_cost_usd"] == pytest.approx(1.0)
    assert s["budget"]["monthly_usd"] == 30.0
    assert isinstance(s["daily"], list)
    assert "note" in s


# ---------------------------------------------------------------- Modal 账单子进程


class _FakeProc:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.returncode = 0
        self.killed = False

    async def communicate(self):
        return self._payload, b""

    def kill(self):
        self.killed = True


@pytest.fixture()
def fresh_billing_cache():
    modal_billing._cache.update(at=-1e9, data=None)
    yield
    modal_billing._cache.update(at=-1e9, data=None)


def test_parse_output_ok():
    raw = json.dumps({"ok": True, "metered_cost_usd": 5.97, "breakdown": {"Deployed Apps": 4.39}}).encode()
    data = modal_billing.parse_output(raw)
    assert data["ok"] is True
    assert data["metered_cost_usd"] == 5.97
    assert data["breakdown"]["Deployed Apps"] == 4.39


def test_parse_output_error_and_garbage():
    err = modal_billing.parse_output(json.dumps({"ok": False, "error": "boom"}).encode())
    assert err["ok"] is False and "boom" in err["error"]
    bad = modal_billing.parse_output(b"<html>not json")
    assert bad["ok"] is False and bad["error"]
    assert modal_billing.parse_output(b"")["ok"] is False


def test_child_script_compiles():
    # 子进程脚本必须始终可编译，防止字符串手误到运行时才爆
    compile(modal_billing._CHILD_SCRIPT, "modal_billing_child", "exec")


async def test_fetch_success_then_cache(monkeypatch, fresh_billing_cache):
    calls = {"n": 0}

    async def fake_exec(*a, **kw):
        calls["n"] += 1
        return _FakeProc(json.dumps({"ok": True, "metered_cost_usd": 1.5}).encode())

    monkeypatch.setattr(modal_billing.asyncio, "create_subprocess_exec", fake_exec)
    first = await modal_billing.fetch()
    assert first["ok"] is True and first["metered_cost_usd"] == 1.5
    second = await modal_billing.fetch()
    assert second is first and calls["n"] == 1  # 120s 内命中缓存，不重复拉子进程


async def test_fetch_failure_short_cache(monkeypatch, fresh_billing_cache):
    calls = {"n": 0}

    async def fake_exec(*a, **kw):
        calls["n"] += 1
        return _FakeProc(b"garbage")

    monkeypatch.setattr(modal_billing.asyncio, "create_subprocess_exec", fake_exec)
    first = await modal_billing.fetch()
    assert first["ok"] is False and first["error"]
    await modal_billing.fetch()
    assert calls["n"] == 1  # 失败结果也短暂缓存（20s），前端 30s 轮询不会连续拉起子进程


async def test_afull_summary_includes_modal(monkeypatch, tmp_db):
    from datetime import datetime

    _insert("modal", 1.0, 3600.0, datetime.now())

    async def fake_fetch(force=False):
        return {"ok": True, "metered_cost_usd": 5.97, "credits_applied_usd": 5.5}

    monkeypatch.setattr(modal_billing, "fetch", fake_fetch)
    data = await cost_svc.afull_summary()
    assert data["modal"]["ok"] is True and data["modal"]["metered_cost_usd"] == 5.97
    assert "budget" in data and "daily" in data  # 本地汇总不受账单子进程影响
