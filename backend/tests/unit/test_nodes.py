"""Node inventory manifest and comparison behavior."""

from app.api.v1 import nodes


def test_manifest_keeps_git_and_enabled_metadata():
    manifest = nodes._manifest({
        "packages": [
            {
                "name": "ExampleNode",
                "repo": "https://example.test/node.git",
                "commit": "abc1234",
                "has_requirements": True,
                "enabled": True,
            },
            {
                "name": "DisabledNode.disabled",
                "repo": None,
                "commit": None,
                "has_requirements": True,
                "enabled": False,
            },
        ],
        "single_files": ["single.py"],
    })

    assert manifest["schema"] == 1
    by_name = {p["name"]: p for p in manifest["packages"]}
    assert by_name["ExampleNode"]["repo"] == "https://example.test/node.git"
    assert by_name["ExampleNode"]["commit"] == "abc1234"
    assert by_name["ExampleNode"]["enabled"] is True
    # 停用包按实际目录名（含 .disabled 后缀）登记，保留启停状态
    disabled = by_name["DisabledNode.disabled"]
    assert disabled["enabled"] is False
    assert manifest["single_files"][0]["kind"] == "single"


def test_manifest_entries_accepts_missing_legacy_manifest():
    assert nodes._manifest_entries(None) == {}
    entries = nodes._manifest_entries({
        "packages": [{"name": "ExampleNode", "kind": "pkg"}],
        "single_files": [{"name": "single.py", "kind": "single"}],
    })
    assert set(entries) == {"ExampleNode", "single.py"}


def test_safe_node_names_reject_paths_and_hidden_names():
    assert nodes._safe_name("ExampleNode")
    assert nodes._safe_name("ExampleNode.disabled")  # 停用包按实际目录名同步
    assert not nodes._safe_name("../ExampleNode")
    assert not nodes._safe_name(".hidden")
    assert not nodes._safe_name("nested/Node")


async def test_dependency_precheck_endpoint_uses_lock_and_forwards_force(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(nodes, "scan_custom_nodes", lambda: {"packages": [], "single_files": []})
    monkeypatch.setattr(
        nodes.lockfile,
        "generate",
        lambda scan: {"nodes": [{"name": "A", "requirements": ["x==1"]}]},
    )

    def fake_precheck(lock_nodes, *, force=False):
        seen["nodes"] = lock_nodes
        seen["force"] = force
        return {
            "status": "ok", "conflicts": [], "nodes": ["A"],
            "merged_requirements": ["x==1"], "detail": None, "log_tail": [], "cached": False,
        }

    monkeypatch.setattr(nodes.resolve, "precheck", fake_precheck)
    result = await nodes.nodes_dependency_precheck(force=True)

    assert result["status"] == "ok"
    assert seen == {"nodes": [{"name": "A", "requirements": ["x==1"]}], "force": True}

