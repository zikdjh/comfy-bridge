"""Follow-up read-only probe for the full Web test plan.

This probe validates remaining API boundaries and current operational state.
It does not submit a prompt, mutate configuration, or start local inference.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


BASE = "http://127.0.0.1:18189"
FRONTEND = "http://localhost:15173"
EVIDENCE = Path(
    r"C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42"
    r"\ego\comfy-bridge-web-20261018\followup-api-probe.json"
)


def body_excerpt(response: httpx.Response) -> object:
    try:
        value = response.json()
    except ValueError:
        return response.text[:3000]
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > 10000:
        return {"truncated": True, "preview": text[:10000]}
    return value


def main() -> None:
    results: dict[str, object] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read-only follow-up; no generation or persistent mutation",
        "cases": {},
        "derived": {},
    }
    cases = results["cases"]
    headers = {"accept": "application/json", "origin": FRONTEND}

    with httpx.Client(trust_env=False, timeout=90.0, headers=headers) as client:

        def probe(
            case: str,
            method: str,
            path: str,
            *,
            base: str = BASE,
            **kwargs: object,
        ) -> httpx.Response:
            response = client.request(method, f"{base}{path}", **kwargs)
            cases[case] = {
                "status": response.status_code,
                "content_type": response.headers.get("content-type"),
                "content_length": len(response.content),
                "body": body_excerpt(response),
            }
            return response

        config_response = probe("API-008", "GET", "/api/v1/config")
        comfy_root = None
        if config_response.is_success:
            comfy_root = config_response.json().get("local", {}).get("comfy_root")
            results["derived"]["configured_comfy_root"] = comfy_root
        if comfy_root:
            probe(
                "API-010-valid-configured",
                "POST",
                "/api/v1/config/validate-comfy",
                json={"path": comfy_root},
            )
        probe(
            "API-010-invalid",
            "POST",
            "/api/v1/config/validate-comfy",
            json={"path": r"Z:\definitely-no-such-comfyui-fulltest"},
        )

        probe(
            "NEG-010-budget-negative-real-field",
            "PUT",
            "/api/v1/cost/budget",
            json={"monthly_budget_usd": -1},
        )
        probe(
            "NEG-010-budget-extra-field",
            "PUT",
            "/api/v1/cost/budget",
            json={"monthly_budget_usd": 0.0, "fulltest_unknown_field": -1},
        )
        probe(
            "API-013-budget-after-probes",
            "GET",
            "/api/v1/cost/summary",
        )

        preview = probe(
            "NEG-010-routing-invalid-backend",
            "POST",
            "/api/v1/routing/preview",
            json={"manual_backend": "not-a-backend"},
        )
        if preview.is_success:
            value = preview.json()
            results["derived"]["invalid_backend_selected"] = {
                "backend": value.get("backend"),
                "final_backend": value.get("final_backend"),
                "trace": value.get("trace"),
            }

        uploads_response = probe(
            "API-066-upload-sessions",
            "GET",
            "/api/v1/uploads?limit=200",
        )
        if uploads_response.is_success:
            uploads = uploads_response.json().get("uploads") or []
            counts: dict[str, int] = {}
            stuck: list[dict[str, object]] = []
            duplicate_names: dict[str, int] = {}
            for item in uploads:
                status = str(item.get("status") or "unknown")
                counts[status] = counts.get(status, 0) + 1
                filename = str(item.get("filename") or "")
                if filename:
                    duplicate_names[filename] = duplicate_names.get(filename, 0) + 1
                received = item.get("uploaded_chunks") or []
                total = item.get("total_chunks")
                if status == "receiving" and total and len(received) < int(total):
                    stuck.append(
                        {
                            "id": item.get("id"),
                            "filename": filename,
                            "received_chunks": received,
                            "total_chunks": total,
                            "uploaded_bytes": item.get("uploaded_bytes"),
                            "size": item.get("size"),
                            "updated_at": item.get("updated_at"),
                            "missing_count": item.get("missing_count"),
                        }
                    )
            results["derived"]["upload_summary"] = {
                "total": len(uploads),
                "status_counts": counts,
                "incomplete_receiving_count": len(stuck),
                "incomplete_receiving": stuck[:50],
                "duplicate_filenames": {
                    name: count for name, count in duplicate_names.items() if count > 1
                },
            }

        health = probe("API-001", "GET", "/api/v1/health")
        if health.is_success:
            value = health.json()
            results["derived"]["health_bridge_url"] = value.get("bridge", {}).get("url")
            results["derived"]["health_bridge_listen"] = value.get("bridge", {}).get("listen")
            results["derived"]["modal_online"] = value.get("modal", {}).get("online")

        modal_logs = probe("API-049-modal-logs", "GET", "/api/v1/logs/modal?tail=100")
        if modal_logs.is_success:
            value = modal_logs.json()
            results["derived"]["modal_logs_online"] = value.get("online")
            results["derived"]["modal_logs_state"] = value.get("state")

        request_response = probe(
            "API-053-request-200",
            "GET",
            "/api/v1/requests/200",
        )
        if request_response.is_success:
            item = request_response.json().get("item") or request_response.json()
            results["derived"]["request_200"] = {
                key: item.get(key)
                for key in (
                    "id",
                    "status",
                    "final_backend",
                    "workflow_name",
                    "model_name",
                    "graph_hash",
                    "prompt_id",
                    "created_at",
                    "finished_at",
                )
            }

        compare = probe(
            "NEG-008-workflow-compare-missing",
            "GET",
            "/api/v1/workflows/compare?name=__fulltest_no_such_workflow__.json",
        )
        if compare.is_success:
            results["derived"]["workflow_compare_missing"] = compare.json()

        volume = probe(
            "API-072-volume-usage",
            "GET",
            "/api/v1/volume/volumes?refresh=true",
        )
        if volume.is_success:
            value = volume.json()
            results["derived"]["volume_usage_computing"] = value.get("computing")

        probe("NEG-009-node-missing", "GET", "/api/v1/nodes/fulltest-no-such-node")
        object_missing = probe(
            "DP-005-missing",
            "GET",
            "/object_info/fulltest-no-such-node",
        )
        if object_missing.is_success:
            results["derived"]["object_info_missing_value"] = object_missing.json()

        probe(
            "UI-007-health-link",
            "GET",
            "/api/v1/health",
        )
        frontend_health = probe(
            "UI-007-vite-proxy-health",
            "GET",
            "/api/v1/health",
            base=FRONTEND,
        )
        if frontend_health.is_success:
            results["derived"]["vite_proxy_health_bridge_url"] = (
                frontend_health.json().get("bridge", {}).get("url")
            )

        probe("DP-010-history", "GET", "/history")

        modal_containers = probe(
            "API-025-modal-containers-final",
            "GET",
            "/api/v1/launch/modal/containers",
        )
        if modal_containers.is_success:
            results["derived"]["modal_containers"] = modal_containers.json()

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "evidence": str(EVIDENCE),
                "summary": {
                    name: value.get("status") for name, value in cases.items()
                },
                "derived": results["derived"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
