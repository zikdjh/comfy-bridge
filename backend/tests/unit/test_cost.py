"""成本记账与预算护栏单元测试（临时库：monkeypatch store 路径，不碰真实 data/）。"""

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


def _insert(backend: str, est: float | None, gpu: float | None, created, model_name: str | None = None,
            workflow_name: str | None = None) -> str:
    created_ms = int(created.timestamp() * 1000)
    pid = f"pid-{created_ms}-{backend}-{est}"
    store.insert_request({
        "prompt_id": pid,
        "created_at": created.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "created_ms": created_ms,
        "backend": backend,
        "final_backend": backend,
        "gen_type": "image",
        "workflow_name": workflow_name,
        "model_name": model_name,
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

    now = datetime.now().astimezone()
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

    now = datetime.now().astimezone()
    # 用本地零点定位「昨天」，避免 now 接近零点时相对偏移跨到前天。
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_late = midnight - timedelta(hours=1)   # 昨天 23:00
    yesterday_early = yesterday_late - timedelta(hours=12)  # 昨天 11:00
    _insert("modal", 0.10, 100.0, yesterday_early)
    _insert("modal", 0.20, 200.0, yesterday_late)
    _insert("modal", 0.40, 400.0, now)

    daily = store.cost_daily(30)
    assert len(daily) == 2
    assert daily[0]["date"] == yesterday_late.strftime("%Y-%m-%d")
    assert daily[0]["requests"] == 2
    assert daily[0]["est_cost_usd"] == pytest.approx(0.30)
    assert daily[-1]["date"] == now.strftime("%Y-%m-%d")
    assert daily[-1]["gpu_seconds"] == pytest.approx(400.0)


def test_record_cost_idempotent_overwrite(tmp_db):
    from datetime import datetime

    pid = _insert("modal", 0.5, 900.0, datetime.now().astimezone())
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
    _insert("modal", 0.49, 900.0, datetime.now().astimezone())
    state = cost_svc.budget_state()
    assert state["enabled"] is True
    assert state["exceeded"] is False
    assert state["remaining_usd"] == pytest.approx(0.01)

    _insert("modal", 0.02, 100.0, datetime.now().astimezone())
    state = cost_svc.budget_state()
    assert state["exceeded"] is True  # 0.51 >= 0.5
    assert state["remaining_usd"] == 0.0


def test_budget_state_disabled_when_limit_zero(tmp_db, monkeypatch):
    from datetime import datetime

    _patch_budget_settings(monkeypatch, monthly_budget_usd=0)
    _insert("modal", 10.0, 9000.0, datetime.now().astimezone())
    state = cost_svc.budget_state()
    assert state["enabled"] is False
    assert state["exceeded"] is False
    assert state["remaining_usd"] is None


def test_summary_shape(tmp_db, monkeypatch):
    from datetime import datetime

    _patch_budget_settings(monkeypatch, monthly_budget_usd=30.0)
    _insert("modal", 1.0, 3600.0, datetime.now().astimezone())
    s = cost_svc.summary()
    assert s["gpu_type"] == cost_svc.GPU_TYPE
    assert s["today"]["requests"] == 1
    assert s["month"]["est_cost_usd"] == pytest.approx(1.0)
    assert s["budget"]["monthly_usd"] == 30.0
    assert isinstance(s["daily"], list)
    assert "note" in s


# ---------------------------------------------------------------- Modal 账单 CLI 子进程

# modal billing summary --json 实测输出（1.5.5；Decimal 串 + snake_case 键）
_CLI_JSON = b'''{
  "metered_cost": "5.96977888",
  "billed_cost": "0E-8",
  "adjustments": {"plan_cost": "0E-8", "credits": "-5.50000000"},
  "metered_cost_breakdown": {"llm_tokens": "0E-8", "deployed_apps": "4.38552567", "volumes": "0.46977888"}
}'''


class _FakeProc:
    def __init__(self, payload: bytes = b"", err: bytes = b"", returncode: int = 0):
        self._payload = payload
        self._err = err
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return self._payload, self._err

    def kill(self):
        self.killed = True


@pytest.fixture()
def fresh_billing_cache():
    modal_billing._cache.update(at=-1e9, data=None)
    yield
    modal_billing._cache.update(at=-1e9, data=None)


def test_parse_output_cli_shape():
    data = modal_billing.parse_output(_CLI_JSON)
    assert data["ok"] is True
    assert data["cycle"] == modal_billing.current_cycle()
    assert data["period_start"].endswith("-01")
    assert data["metered_cost_usd"] == pytest.approx(5.9698)
    assert data["billed_cost_usd"] == 0.0
    assert data["credits_applied_usd"] == pytest.approx(5.5)  # 负数调整取绝对值
    assert data["breakdown"] == {"deployed_apps": 4.3855, "volumes": 0.4698}  # 0 值剔除、按额降序


def test_parse_output_garbage():
    bad = modal_billing.parse_output(b"<html>not json")
    assert bad["ok"] is False and bad["error"]
    assert modal_billing.parse_output(b"")["ok"] is False


async def test_fetch_success_then_cache(monkeypatch, fresh_billing_cache):
    calls = {"n": 0}

    async def fake_exec(*a, **kw):
        calls["n"] += 1
        return _FakeProc(payload=_CLI_JSON)

    monkeypatch.setattr(modal_billing.asyncio, "create_subprocess_exec", fake_exec)
    first = await modal_billing.fetch()
    assert first["ok"] is True and first["metered_cost_usd"] == pytest.approx(5.9698)
    second = await modal_billing.fetch()
    assert second is first and calls["n"] == 1  # 120s 内命中缓存，不重复拉 CLI


async def test_fetch_cli_error_strips_noise_and_short_caches(monkeypatch, fresh_billing_cache):
    calls = {"n": 0}

    async def fake_exec(*a, **kw):
        calls["n"] += 1
        return _FakeProc(
            err=b"DeprecationWarning: old policy\n  asyncio.set_event_loop_policy(...)\nToken validation failed",
            returncode=1,
        )

    monkeypatch.setattr(modal_billing.asyncio, "create_subprocess_exec", fake_exec)
    first = await modal_billing.fetch()
    assert first["ok"] is False
    assert "Token validation failed" in first["error"] and "DeprecationWarning" not in first["error"]
    await modal_billing.fetch()
    assert calls["n"] == 1  # 失败结果也短暂缓存（20s），前端 30s 轮询不会连续拉 CLI


async def test_afull_summary_includes_modal(monkeypatch, tmp_db):
    from datetime import datetime

    _insert("modal", 1.0, 3600.0, datetime.now().astimezone())

    async def fake_fetch(force=False):
        return {"ok": True, "metered_cost_usd": 5.97, "credits_applied_usd": 5.5}

    monkeypatch.setattr(modal_billing, "fetch", fake_fetch)
    data = await cost_svc.afull_summary()
    assert data["modal"]["ok"] is True and data["modal"]["metered_cost_usd"] == 5.97
    assert "budget" in data and "daily" in data  # 本地汇总不受账单子进程影响


def test_count_active_modal(tmp_db):
    """并发护栏计数源：final_backend=modal 且非终态才计入；回退/本地/终态均不算。"""
    base = {"created_at": store.now_iso(), "created_ms": store.now_ms(), "gen_type": "image",
            "workflow_name": None, "model_name": None, "graph_hash": "h", "graph_file": "",
            "client_id": None, "rule_trace": None, "error": None}
    rows = [
        {"prompt_id": "p1", "backend": "modal", "final_backend": "modal", "status": "queued", **base},
        {"prompt_id": "p2", "backend": "modal", "final_backend": "modal", "status": "running", **base},
        {"prompt_id": "p3", "backend": "modal", "final_backend": "local", "status": "queued", **base},  # 已回退
        {"prompt_id": "p4", "backend": "local", "final_backend": "local", "status": "running", **base},
        {"prompt_id": "p5", "backend": "modal", "final_backend": "modal", "status": "done", **base},
    ]
    for r in rows:
        store.insert_request(r)
    assert store.count_active_modal() == 2
    store.update_status("p1", "done")
    assert store.count_active_modal() == 1


# ---------------------------------------------------------------- 报表 v2：聚合 / 流水 / UTC 逐日
def test_cost_breakdown_by_workflow_and_model(tmp_db):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    _insert("modal", 0.5, 900.0, now, model_name="sd_xl.safetensors", workflow_name="portrait")
    _insert("modal", 0.3, 500.0, now, model_name="sd_xl.safetensors", workflow_name="portrait")
    _insert("modal", 0.1, 100.0, now)  # 无工作流名、无主模型 → 未命名/未记录
    _insert("modal", 0.2, 200.0, now - timedelta(days=40), model_name="old.safetensors")  # 窗口外

    wf = store.cost_breakdown("workflow", days=30)
    assert [r["key"] for r in wf] == ["portrait", "（未命名）"]  # 按金额降序
    assert wf[0]["est_cost_usd"] == pytest.approx(0.8)
    assert wf[0]["requests"] == 2

    md = store.cost_breakdown("model", days=30)
    assert [r["key"] for r in md] == ["sd_xl.safetensors", "（未记录）"]
    assert md[0]["est_cost_usd"] == pytest.approx(0.8)
    assert md[1]["est_cost_usd"] == pytest.approx(0.1)

    with pytest.raises(ValueError):
        store.cost_breakdown("bogus")


def test_cost_entries_rows(tmp_db):
    from datetime import UTC, datetime, timedelta

    _insert("modal", 0.1, 100.0, datetime.now(UTC) - timedelta(hours=1))
    _insert("modal", 0.5, 900.0, datetime.now(UTC), model_name="a.safetensors", workflow_name="wf")
    entries = store.cost_entries(30)
    assert len(entries) == 2
    assert entries[0]["workflow_name"] == "wf"  # 新→旧（按入库序）
    assert entries[0]["model_name"] == "a.safetensors"
    assert {"created_at", "gen_type", "final_backend", "status", "gpu_seconds",
            "est_cost_usd", "error"} <= set(entries[0])


def test_cost_daily_utc_buckets(tmp_db):
    """UTC 日界聚合（对账口径）：同 UTC 日的两条并入同桶，与本地日界无关。"""
    from datetime import UTC, datetime

    def _utc_row(est: float, when: datetime) -> None:
        pid = f"utc-{est}-{int(when.timestamp())}"
        store.insert_request({
            "prompt_id": pid, "created_at": store.now_iso(),
            "created_ms": int(when.timestamp() * 1000), "backend": "modal",
            "final_backend": "modal", "gen_type": "image", "workflow_name": None,
            "model_name": None, "graph_hash": None, "graph_file": None,
            "client_id": None, "status": "done", "rule_trace": None, "error": None,
        })
        store.record_cost(pid, 100.0, est)

    _utc_row(0.2, datetime(2026, 9, 15, 10, tzinfo=UTC))
    _utc_row(0.3, datetime(2026, 9, 15, 23, tzinfo=UTC))  # 本地已是 16 日，UTC 仍是 15 日
    _utc_row(0.4, datetime(2026, 9, 16, 1, tzinfo=UTC))

    from app.cost.reconciliation import utc_bounds

    start_ms, end_ms, _, _ = utc_bounds("2026-09")
    daily = store.cost_daily_utc(start_ms, end_ms)
    by_date = {r["date"]: r for r in daily}
    assert set(by_date) == {"2026-09-15", "2026-09-16"}
    assert by_date["2026-09-15"]["requests"] == 2
    assert by_date["2026-09-15"]["est_cost_usd"] == pytest.approx(0.5)


# ---------------------------------------------------------------- 报表 v2：对账
def test_reconciliation_validate_cycle():
    from app.cost import reconciliation

    assert reconciliation.validate_cycle("2026-09") == "2026-09"
    for bad in ("2026-13", "2026-9", "abc", "2026-091", ""):
        with pytest.raises(ValueError):
            reconciliation.validate_cycle(bad)


async def test_areconcile_merges_both_sides(monkeypatch, tmp_db):
    from datetime import UTC, datetime

    from app.cost import reconciliation

    _insert("modal", 0.4, 400.0, datetime(2026, 9, 15, 12, tzinfo=UTC))

    async def fake_report(start, end, force=False):
        assert start == "2026-09-01" and end == "2026-10-01"  # UTC 月界
        return {
            "ok": True, "gpu_usd": 0.5, "total_usd": 0.8,
            "resources": {"L40S": 0.5, "CPU": 0.3},
            "apps": [{"name": "comfyui-l40s", "usd": 0.8}],
            "daily": {
                "2026-09-15": {"gpu_usd": 0.5, "total_usd": 0.8},
                "2026-09-20": {"gpu_usd": 0.0, "total_usd": 0.0},  # Modal 侧独有日期
            },
        }

    monkeypatch.setattr(reconciliation.modal_billing, "report", fake_report)
    data = await reconciliation.areconcile("2026-09")
    assert data["cycle"] == "2026-09"
    assert data["local"]["est_cost_usd"] == pytest.approx(0.4)
    assert data["modal"]["gpu_usd"] == pytest.approx(0.5)
    assert data["ratio_gpu_vs_est"] == pytest.approx(1.25)
    assert {r["date"] for r in data["daily"]} == {"2026-09-15", "2026-09-20"}  # 并集
    row15 = next(r for r in data["daily"] if r["date"] == "2026-09-15")
    assert row15["local_usd"] == pytest.approx(0.4) and row15["modal_gpu_usd"] == pytest.approx(0.5)
    row20 = next(r for r in data["daily"] if r["date"] == "2026-09-20")
    assert row20["local_usd"] == 0.0 and row20["modal_total_usd"] == 0.0
    assert "note" in data


async def test_areconcile_modal_failure_keeps_local(monkeypatch, tmp_db):
    from datetime import UTC, datetime

    from app.cost import reconciliation

    _insert("modal", 0.4, 400.0, datetime(2026, 9, 15, 12, tzinfo=UTC))

    async def fail_report(start, end, force=False):
        return {"ok": False, "error": "CLI 失败"}

    monkeypatch.setattr(reconciliation.modal_billing, "report", fail_report)
    data = await reconciliation.areconcile("2026-09")
    assert data["modal"]["ok"] is False
    assert data["local"]["est_cost_usd"] == pytest.approx(0.4)  # 本地侧照常展示
    assert data["daily"] and data["daily"][0]["modal_gpu_usd"] == 0.0
    assert data["ratio_gpu_vs_est"] is None


# ---------------------------------------------------------------- 报表 v2：账单明细 CLI
_REPORT_JSON = b'''[
  {"object_id": "ap-1", "description": "comfyui-l40s", "environment": "main",
   "interval_start": "2026-10-01T00:00:00", "resource": "L40S", "cost": "0.72782441"},
  {"object_id": "ap-1", "description": "comfyui-l40s", "environment": "main",
   "interval_start": "2026-10-01T00:00:00", "resource": "Memory", "cost": "0.09555028"},
  {"object_id": "ap-1", "description": "comfyui-l40s", "environment": "main",
   "interval_start": "2026-10-02T00:00:00", "resource": "CPU", "cost": "0.0700"}
]'''


def test_parse_report_aggregates():
    data = modal_billing.parse_report(_REPORT_JSON)
    assert data["ok"] is True
    assert data["gpu_usd"] == pytest.approx(0.7278)  # 仅 GPU 资源行与本地估算同口径
    assert data["total_usd"] == pytest.approx(0.8934)
    assert list(data["resources"]) == ["L40S", "Memory", "CPU"]  # 按额降序
    assert set(data["daily"]) == {"2026-10-01", "2026-10-02"}
    assert data["daily"]["2026-10-01"]["gpu_usd"] == pytest.approx(0.7278)
    assert data["apps"] == [{"name": "comfyui-l40s", "usd": pytest.approx(0.8934)}]


def test_parse_report_garbage():
    assert modal_billing.parse_report(b"<html>")["ok"] is False
    empty = modal_billing.parse_report(b"")
    assert empty["ok"] is True and empty["gpu_usd"] == 0.0  # 空区间 = 无用量


@pytest.fixture()
def fresh_report_cache():
    modal_billing._report_cache.clear()
    yield
    modal_billing._report_cache.clear()


async def test_report_fetch_then_cache(monkeypatch, fresh_report_cache):
    calls = {"n": 0}

    async def fake_exec(*a, **kw):
        calls["n"] += 1
        args = list(a)
        assert "--show-resources" in args and "--json" in args
        return _FakeProc(payload=_REPORT_JSON)

    monkeypatch.setattr(modal_billing.asyncio, "create_subprocess_exec", fake_exec)
    first = await modal_billing.report("2026-09-01", "2026-10-01")
    assert first["ok"] is True
    second = await modal_billing.report("2026-09-01", "2026-10-01")
    assert second is first and calls["n"] == 1  # 同区间命中缓存
    await modal_billing.report("2026-09-01", "2026-10-01", force=True)
    assert calls["n"] == 2


# ---------------------------------------------------------------- 报表 v2：主模型回填
def test_backfill_model_names(tmp_db, monkeypatch, tmp_path):
    from datetime import UTC, datetime

    base = {"created_at": store.now_iso(), "created_ms": store.now_ms(), "gen_type": "image",
            "workflow_name": None, "graph_hash": "h", "client_id": None,
            "status": "done", "rule_trace": None, "error": None}
    store.insert_request({"prompt_id": "bf-1", "backend": "modal", "final_backend": "modal",
                          "model_name": None, "graph_file": "g1.json",
                          "created_ms": int(datetime(2026, 9, 15, tzinfo=UTC).timestamp() * 1000), **base})
    store.insert_request({"prompt_id": "bf-2", "backend": "modal", "final_backend": "modal",
                          "model_name": None, "graph_file": "gone.json",
                          "created_ms": int(datetime(2026, 9, 15, tzinfo=UTC).timestamp() * 1000 + 1), **base})

    graphs = tmp_path / "graphs"
    graphs.mkdir()
    (graphs / "g1.json").write_bytes(b'{"prompt": {"1": {"class_type": "CheckpointLoaderSimple",'
                                     b' "inputs": {"ckpt_name": "a.safetensors"}}}}')
    monkeypatch.setattr(store, "GRAPH_DIR", graphs)

    assert cost_svc.backfill_model_names() == 1
    rows = {r["prompt_id"]: r for r in (store.get_request_by_prompt("bf-1"), store.get_request_by_prompt("bf-2"))}
    assert rows["bf-1"]["model_name"] == "a.safetensors"
    assert rows["bf-2"]["model_name"] == ""  # 快照缺失 → 空串哨兵，不再重扫
    assert cost_svc.backfill_model_names() == 0  # 第二次启动无 NULL 行可回填
