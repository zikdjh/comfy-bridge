"""成本记账与预算护栏单元测试（临时库：monkeypatch store 路径，不碰真实 data/）。"""

from types import SimpleNamespace

import pytest

from app import cost as cost_svc
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
