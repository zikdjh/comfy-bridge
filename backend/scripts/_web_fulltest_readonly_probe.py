"""Read-only Web full-test probe.

No config, settings, workflow, node, volume, or upload mutation is performed.
Real generation is also excluded; the probe records current Modal evidence only.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx


BASE = "http://127.0.0.1:18189"
FRONTEND = "http://localhost:15173"
LOCAL_COMFY = "http://127.0.0.1:8188"
EVIDENCE = Path(
    r"C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42"
    r"\ego\comfy-bridge-web-20261018\readonly-api-probe.json"
)


def excerpt(response: httpx.Response) -> object:
    try:
        value = response.json()
    except ValueError:
        return response.text[:1200]
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > 2500:
        return {"truncated": True, "preview": text[:2500]}
    return value


def listen_ports() -> list[dict]:
    command = (
        "Get-NetTCPConnection -State Listen | "
        "Where-Object { $_.LocalPort -in 8188,8189,18189,18190,18191,18192,18193,15173 } | "
        "Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    try:
        value = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return [{"error": proc.stderr or proc.stdout}]
    return value if isinstance(value, list) else [value]


def capture(client: httpx.Client, results: dict, case: str, method: str, path: str, **kwargs) -> object:
    response = client.request(method, f"{BASE}{path}", **kwargs)
    results[case] = {
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "content_length": len(response.content),
        "body": excerpt(response),
    }
    return response


def main() -> None:
    headers = {"accept": "application/json", "origin": FRONTEND}
    results: dict[str, object] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read-only Web/API; no local inference; no cloud generation",
        "environment": {
            "python": os.sys.version,
            "cwd": str(Path.cwd()),
            "ports": listen_ports(),
        },
        "cases": {},
        "derived": {},
    }
    cases = results["cases"]

    with httpx.Client(trust_env=False, timeout=60.0, headers=headers) as client:
        health = capture(client, cases, "API-001", "GET", "/api/v1/health")
        capture(client, cases, "API-002", "GET", "/api/v1/health/modal-cli")
        capture(client, cases, "API-003", "GET", "/api/v1/config/accounts")
        capture(client, cases, "API-007", "GET", "/api/v1/config/node-env")
        config_response = capture(client, cases, "API-008", "GET", "/api/v1/config")
        capture(client, cases, "API-010-valid", "POST", "/api/v1/config/validate-comfy",
                json={"path": r"E:\JAVA\work\web\zc\modal\comfy-bridge\comfyui"})
        capture(client, cases, "API-010-invalid", "POST", "/api/v1/config/validate-comfy",
                json={"path": "Z:\\definitely-no-such-comfyui-fulltest"})
        capture(client, cases, "API-011", "GET", "/api/v1/config/scan-comfy")
        capture(client, cases, "API-014", "GET", "/api/v1/cost/summary")
        capture(client, cases, "API-016-workflow", "GET",
                "/api/v1/cost/report?by=workflow&days=30&limit=50")
        capture(client, cases, "API-016-model", "GET",
                "/api/v1/cost/report?by=model&days=30&limit=50")
        capture(client, cases, "API-016-invalid", "GET",
                "/api/v1/cost/report?by=not-a-group&days=30&limit=50")
        capture(client, cases, "API-017", "GET",
                "/api/v1/cost/reconciliation?cycle=2026-10")
        for scope in ("report", "entries", "workflow", "model"):
            response = capture(client, cases, f"API-018-{scope}", "GET",
                               f"/api/v1/cost/export?scope={scope}&days=30")
            cases[f"API-018-{scope}"]["bom"] = response.content.startswith(b"\xef\xbb\xbf")

        capture(client, cases, "API-019", "GET", "/api/v1/launch")
        capture(client, cases, "API-021", "GET", "/api/v1/launch/local/status?tail=true")
        capture(client, cases, "API-025", "GET", "/api/v1/launch/modal/containers")
        capture(client, cases, "API-027", "GET", "/api/v1/nodes/local")
        capture(client, cases, "API-028", "GET", "/api/v1/nodes/cloud")
        capture(client, cases, "API-029-cached", "GET", "/api/v1/nodes/diff?deep=false")
        capture(client, cases, "API-029-force", "GET", "/api/v1/nodes/diff?deep=false&force=true")
        capture(client, cases, "API-032", "GET", "/api/v1/nodes/version")
        capture(client, cases, "API-034", "GET", "/api/v1/nodes/dependency-precheck?force=true")

        capture(client, cases, "API-037", "GET", "/api/v1/workflows/local")
        capture(client, cases, "API-038", "GET", "/api/v1/workflows/sync")
        capture(client, cases, "API-040-7", "GET",
                "/api/v1/workflows/run-stats?days=7&limit=50")
        capture(client, cases, "API-040-30", "GET",
                "/api/v1/workflows/run-stats?days=30&limit=50")
        capture(client, cases, "API-040-90", "GET",
                "/api/v1/workflows/run-stats?days=90&limit=50")
        capture(client, cases, "API-041-missing", "GET",
                "/api/v1/workflows/compare?name=fulltest-definitely-missing-20261018.json")
        jobs_response = capture(client, cases, "API-042", "GET", "/api/v1/jobs?limit=50")
        capture(client, cases, "API-043-missing", "GET", "/api/v1/jobs/not-a-job")
        capture(client, cases, "API-045-image", "GET", "/api/v1/media/local?kind=image&limit=50")
        capture(client, cases, "API-045-video", "GET", "/api/v1/media/local?kind=video&limit=50")
        capture(client, cases, "API-046-escape", "GET",
                "/api/v1/media/local/file?path=../../etc/passwd")
        capture(client, cases, "API-047", "GET", "/api/v1/logs/local?tail=100")
        capture(client, cases, "API-047-invalid", "GET", "/api/v1/logs/local?tail=-1")
        capture(client, cases, "API-049", "GET", "/api/v1/logs/modal?tail=100")

        requests_response = capture(
            client,
            cases,
            "API-052-default",
            "GET",
            "/api/v1/requests?limit=50&backend=modal&status=done",
        )
        capture(client, cases, "API-052-local", "GET",
                "/api/v1/requests?limit=50&backend=local&status=done")
        capture(client, cases, "API-052-invalid", "GET",
                "/api/v1/requests?limit=0")
        capture(client, cases, "API-053-invalid", "GET", "/api/v1/requests/not-a-number")
        capture(client, cases, "API-053-missing", "GET", "/api/v1/requests/999999")

        request_items = (
            requests_response.json().get("items", [])
            if requests_response.is_success
            else []
        )
        request_id = request_items[0].get("id") if request_items else 200
        capture(client, cases, "API-053-existing", "GET", f"/api/v1/requests/{request_id}")
        capture(client, cases, "API-054-existing", "GET",
                f"/api/v1/requests/{request_id}/graph")
        capture(client, cases, "API-055-workflow", "POST", "/api/v1/routing/preview",
                json={"workflow_name": "fulltest-header-dryrun"})
        capture(client, cases, "API-055-modal", "POST", "/api/v1/routing/preview",
                json={"prompt": {}, "manual_backend": "modal"})
        capture(client, cases, "API-055-invalid", "POST", "/api/v1/routing/preview",
                json={"manual_backend": "not-a-backend"})
        capture(client, cases, "API-056", "GET", "/api/v1/routing/rules")
        capture(client, cases, "API-058", "GET", "/api/v1/settings-sync")
        capture(client, cases, "API-060", "GET", "/api/v1/modal-volume")
        for category in ("checkpoints", "loras", "vae"):
            capture(client, cases, f"API-061-{category}", "GET",
                    f"/api/v1/modal-volume/overview?category={category}")
        capture(client, cases, "API-063", "GET", "/api/v1/modal-volume/jobs")

        capture(client, cases, "API-065-invalid", "POST", "/api/v1/uploads/init",
                json={})
        uploads_response = capture(client, cases, "API-066", "GET",
                                   "/api/v1/uploads?limit=20")
        capture(client, cases, "API-067-invalid", "GET",
                "/api/v1/uploads/not-an-upload")
        capture(client, cases, "API-071", "GET", "/api/v1/volume/meta")
        capture(client, cases, "API-072", "GET", "/api/v1/volume/volumes?refresh=true")
        capture(client, cases, "API-075-escape", "GET",
                "/api/v1/volume/list?path=../../etc")
        capture(client, cases, "API-077", "GET", "/api/v1/volume/trash")
        capture(client, cases, "API-081", "GET", "/api/v1/volume/dirs")
        capture(client, cases, "API-083", "GET", "/api/v1/volume/moves")
        capture(client, cases, "API-086", "GET", "/api/v1/volume/downloads")
        capture(client, cases, "API-089", "GET", "/api/v1/modal/warmup/status")

        comfy_paths = {
            "DP-003": "/object_info",
            "DP-004": "/api/object_info",
            "DP-005": "/object_info/KSampler",
            "DP-005-missing": "/object_info/fulltest-not-a-node",
            "DP-006": "/api/object_info/KSampler",
            "DP-007": "/queue",
            "DP-008": "/api/queue",
            "DP-010": "/history",
            "DP-010-missing": "/history/not-a-prompt",
        }
        for case, path in comfy_paths.items():
            capture(client, cases, case, "GET", path)
        capture(client, cases, "DP-001", "GET", "/")
        capture(client, cases, "DP-022", "GET", "/definitely-not-a-comfy-route")
        capture(client, cases, "DP-023-options", "OPTIONS", "/object_info")
        capture(client, cases, "DP-023-head", "HEAD", "/queue")
        capture(client, cases, "NEG-009-nodes-missing", "GET", "/api/v1/nodes/nope")
        capture(client, cases, "NEG-010-routing", "PUT", "/api/v1/routing/rules",
                json={"rules": [{"kind": "not-a-kind"}]})
        capture(client, cases, "NEG-010-budget", "PUT", "/api/v1/cost/budget",
                json={"monthly_limit_usd": -1})

        local_response = httpx.get(f"{LOCAL_COMFY}/system_stats",
                                  trust_env=False, timeout=10)
        results["environment"]["local_comfy_status"] = local_response.status_code
        if local_response.is_success:
            stats = local_response.json()
            results["environment"]["local_comfy_version"] = (
                stats.get("system", {}).get("comfyui_version")
            )

        if config_response.is_success:
            config = config_response.json()
            results["derived"]["bridge_url"] = config.get("bridge", {}).get("url")
            results["derived"]["configured_comfy_root"] = (
                config.get("local", {}).get("comfy_root")
            )
        if health.is_success:
            results["derived"]["health_bridge_url"] = (
                health.json().get("bridge", {}).get("url")
            )
        if uploads_response.is_success:
            upload_items = uploads_response.json().get("items", [])
            results["derived"]["upload_states"] = [
                {
                    "id": item.get("id"),
                    "status": item.get("status"),
                    "received_chunks": item.get("uploaded_chunks") or item.get("received_chunks"),
                    "total_chunks": item.get("total_chunks"),
                    "updated_at": item.get("updated_at"),
                }
                for item in upload_items
            ]
        if jobs_response.is_success:
            results["derived"]["job_states"] = [
                {
                    "id": item.get("id"),
                    "type": item.get("type"),
                    "status": item.get("status"),
                    "progress": item.get("progress"),
                    "updated_at": item.get("updated_at"),
                }
                for item in jobs_response.json().get("items", [])[:30]
            ]
        results["derived"]["request_id"] = request_id
        results["derived"]["request_count"] = len(request_items)

        media_probe = results["cases"].get("API-045-image", {})
        if media_probe.get("status") == 200:
            media_body = media_probe.get("body")
            if isinstance(media_body, dict) and media_body.get("items"):
                item = media_body["items"][0]
                relative = item.get("path") or item.get("relative_path")
                if relative:
                    media_file = capture(client, cases, "API-046-valid", "GET",
                                         "/api/v1/media/local/file?path="
                                         + quote(str(relative), safe=""))
                    results["derived"]["media_file_bom"] = (
                        media_file.content.startswith(b"\xef\xbb\xbf")
                    )

        html = httpx.get(FRONTEND, trust_env=False, timeout=20, follow_redirects=True)
        results["environment"]["frontend_status"] = html.status_code
        results["environment"]["frontend_bytes"] = len(html.content)

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        name: {"status": value.get("status")}
        for name, value in cases.items()
    }
    print(json.dumps({"evidence": str(EVIDENCE), "summary": summary,
                      "derived": results["derived"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
