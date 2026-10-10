"""节点依赖预检（P1-3）：静态约束、目标环境 marker、pip 状态降级与缓存。"""

from app.nodes_mgr import resolve


def _nodes(*requirements: str, name: str = "NodeA") -> list[dict]:
    return [{"name": name, "requirements": list(requirements)}]


def test_direct_conflict_same_package_multiple_versions():
    result = resolve.precheck(
        [
            {"name": "A", "requirements": ["requests==2.31.0"]},
            {"name": "B", "requirements": ["requests==2.32.0"]},
        ],
        use_pip=False,
    )
    assert result["status"] == "blocked"
    assert result["conflicts"][0]["package"] == "requests"
    assert result["conflicts"][0]["nodes"] == ["A", "B"]
    assert "requests==2.31.0" in result["conflicts"][0]["requirements"]


def test_direct_conflict_empty_range():
    result = resolve.precheck(
        _nodes("urllib3>=3", "urllib3<2"),
        use_pip=False,
    )
    assert result["status"] == "blocked"
    assert "没有交集" in result["conflicts"][0]["reason"]


def test_direct_conflict_equal_bounds_with_exclusive_edge():
    result = resolve.precheck(_nodes("pkg>=1.0", "pkg<=1.0", "pkg!=1.0"), use_pip=False)
    assert result["status"] == "blocked"


def test_pep440_equivalent_pins_are_not_a_conflict():
    result = resolve.precheck(_nodes("pkg==1.0", "pkg==1.0.0"), use_pip=False)
    assert result["status"] == "ok"
    assert result["merged_requirements"] == ["pkg==1.0"]


def test_pep440_equivalent_exclusion_is_a_conflict():
    result = resolve.precheck(_nodes("pkg==1.0", "pkg!=1.0.0"), use_pip=False)
    assert result["status"] == "blocked"


def test_open_upper_bound_is_not_false_positive():
    result = resolve.precheck(_nodes("pkg>=1000"), use_pip=False)
    assert result["status"] == "ok"
    assert result["merged_requirements"] == ["pkg>=1000"]


def test_marker_filtered_for_target_linux_python312():
    result = resolve.precheck(
        _nodes(
            "windows-only==1.0; sys_platform == 'win32'",
            "linux-only==2.0; sys_platform == 'linux'",
            "old-python==1.0; python_version < '3.12'",
        ),
        use_pip=False,
    )
    assert result["status"] == "ok"
    assert result["merged_requirements"] == ["linux-only==2.0"]
    assert result["nodes"] == ["NodeA"]


def test_direct_reference_is_preserved_without_false_conflict():
    result = resolve.precheck(
        _nodes(
            "git+https://example.test/repo.git#egg=my_node",
            "./vendor/local_pkg",
            "https://example.test/releases/pkg-1.0-py3-none-any.whl",
        ),
        use_pip=False,
    )
    assert result["status"] == "ok"
    assert result["conflicts"] == []
    assert result["merged_requirements"] == [
        "git+https://example.test/repo.git#egg=my_node",
        "./vendor/local_pkg",
        "https://example.test/releases/pkg-1.0-py3-none-any.whl",
    ]


def test_pip_conflict_blocks(monkeypatch):
    monkeypatch.setattr(
        resolve,
        "_pip_resolve",
        lambda requirements, force=False: (
            "blocked",
            "The conflict is caused by:\n...",
            ["ResolutionImpossible"],
            False,
        ),
    )
    result = resolve.precheck(_nodes("a==1", "b==1"))
    assert result["status"] == "blocked"
    assert result["conflicts"][0]["package"] == "pip resolver"
    assert result["conflicts"][0]["requirements"] == ["a==1", "b==1"]


def test_pip_environment_failure_is_unavailable_and_does_not_block(monkeypatch):
    monkeypatch.setattr(
        resolve,
        "_pip_resolve",
        lambda requirements, force=False: (
            "unavailable",
            "no matching distribution for local Python",
            ["no matching distribution"],
            False,
        ),
    )
    result = resolve.precheck(_nodes("a==1"))
    assert result["status"] == "unavailable"
    assert result["conflicts"] == []


def test_pip_result_is_cached_and_force_refreshes(monkeypatch):
    """_pip_resolve 缓存成功结果；force=True 绕过缓存。"""
    import subprocess

    resolve.clear_cache()
    calls = {"n": 0}

    def fake_run(*args, **kwargs):
        calls["n"] += 1
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(resolve.subprocess, "run", fake_run)
    first = resolve._pip_resolve(["a==1"])
    second = resolve._pip_resolve(["a==1"])
    assert first[0] == "ok" and first[3] is False
    assert second[0] == "ok" and second[3] is True
    assert calls["n"] == 1
    resolve._pip_resolve(["a==1"], force=True)
    assert calls["n"] == 2


def test_pip_environment_markers_only_accept_real_resolver_conflicts():
    assert resolve._looks_like_real_conflict("ResolutionImpossible: cannot resolve")
    assert resolve._looks_like_real_conflict("The conflict is caused by:")
    assert not resolve._looks_like_real_conflict("ERROR: No matching distribution found")
    assert not resolve._looks_like_real_conflict("ERROR: Cannot install package")


def test_blocked_message_contains_nodes_and_constraints():
    result = {
        "conflicts": [{
            "nodes": ["NodeA"],
            "requirements": ["x==1", "x==2"],
            "reason": "版本约束没有交集",
        }],
    }
    text = resolve.blocked_message(result)
    assert "NodeA" in text
    assert "x==1" in text and "x==2" in text
