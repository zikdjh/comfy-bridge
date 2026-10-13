"""统一事件总线与 SSE 端点（P1-5）单测：单调 ID、回放、缺口与任务事件。"""

from __future__ import annotations

from collections import deque

import pytest

from app import events
from app.sync import jobs


@pytest.fixture(autouse=True)
def _reset_events():
    events.reset()
    jobs.reset()
    yield
    events.reset()
    jobs.reset()


def test_publish_uses_monotonic_ids_and_copies_payload():
    payload = {"state": "running"}
    first = events.publish("job", payload)
    payload["state"] = "changed"
    second = events.publish("deploy", {"state": "ok"})

    assert (first["id"], second["id"]) == (1, 2)
    assert first["data"]["state"] == "running"  # deepcopy，发布后外部修改不影响历史
    assert [item["id"] for item in events.snapshot()] == [1, 2]


def test_replay_after_returns_only_newer_events():
    events.publish("job", {"n": 1})
    events.publish("node_sync", {"n": 2})
    events.publish("cost_alert", {"n": 3})

    replayed, gap = events.replay_after(1)
    assert gap is False
    assert [item["id"] for item in replayed] == [2, 3]
    assert events.replay_after(None) == ([], False)


def test_replay_detects_history_gap(monkeypatch):
    monkeypatch.setattr(events, "_events", deque(maxlen=2))
    events.reset()
    events.publish("job", {"n": 1})
    events.publish("job", {"n": 2})
    events.publish("job", {"n": 3})

    replayed, gap = events.replay_after(0)
    assert gap is True
    assert [item["id"] for item in replayed] == [2, 3]


async def test_stream_replays_after_last_event_id():
    events.publish("job", {"action": "started", "job": {"id": "a"}})
    events.publish("deploy", {"action": "finished", "job": {"state": "ok"}})

    class Request:
        async def is_disconnected(self):
            return True

    chunks = [chunk async for chunk in events.stream(Request(), "1")]
    text = "".join(chunks)
    assert "event: ready" in text
    assert "event: job" not in text  # id=1 已由 Last-Event-ID 确认
    assert "event: deploy" in text
    assert "id: 2" in text
    assert text.index("event: deploy") < text.index("event: ready")  # 先回放，再建立基线


async def test_stream_orders_resync_before_replay_without_id_on_resync(monkeypatch):
    monkeypatch.setattr(events, "_events", deque(maxlen=2))
    events.reset()
    events.publish("job", {"n": 1})
    events.publish("job", {"n": 2})
    events.publish("deploy", {"n": 3})

    class Request:
        async def is_disconnected(self):
            return True

    chunks = [chunk async for chunk in events.stream(Request(), "0")]
    text = "".join(chunks)
    assert text.index("event: resync") < text.index("id: 2")
    resync_frame = text.split("event: resync", 1)[1].split("\n\n", 1)[0]
    assert "id:" not in resync_frame


def test_slow_subscriber_gets_resync_instead_of_blocking(monkeypatch):
    monkeypatch.setattr(events, "_QUEUE_MAX", 1)
    queue = events.asyncio.Queue(maxsize=1)
    events._subs.add(queue)
    try:
        events.publish("job", {"n": 1})
        events.publish("deploy", {"n": 2})
        assert queue.qsize() == 1
        queued = queue.get_nowait()
        assert queued["event"] == "resync"
    finally:
        events._subs.discard(queue)


async def test_job_lifecycle_publishes_domain_and_generic_events():
    async def runner(handle):
        handle.log("开始")
        handle.advance({"name": "a", "ok": True})

    job = jobs.start_job("node_sync", "节点同步", 1, runner)
    await jobs.wait_job(job["id"])

    history = events.snapshot()
    names = [item["event"] for item in history]
    assert names[0] == "job" and names[1] == "node_sync"
    assert names.count("job") >= 4  # started + log + advance + finished
    assert history[-1]["data"]["job"]["state"] == "ok"
    assert history[-1]["data"]["action"] == "finished"
