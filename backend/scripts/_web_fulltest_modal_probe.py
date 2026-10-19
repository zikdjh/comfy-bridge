"""Verify explicit Modal routing without executing a model job."""

from __future__ import annotations

import json
from pathlib import Path

import httpx


BASE = "http://127.0.0.1:18189"
EVIDENCE = Path(
    r"C:\Users\z2351\.codex\visualizations\2026\10\15\01a13d51-a08d-7370-85c3-888b64255c42"
    r"\ego\comfy-bridge-web-20261018\header-privacy-probe.json"
)


def main() -> None:
    with httpx.Client(trust_env=False, timeout=30.0) as client:
        rules = client.post(
            f"{BASE}/api/v1/routing/preview",
            json={
                "prompt": {
                    "1": {
                        "class_type": "UNETLoader",
                        "inputs": {
                            "unet_name": "krea2_turbo_fp8_scaled.safetensors",
                            "weight_dtype": "default",
                        },
                    }
                },
                "workflow_name": "fulltest-header-dryrun",
                "manual_backend": "modal",
            },
        )
        malformed = client.post(
            f"{BASE}/api/prompt",
            headers={
                "content-type": "application/json",
                "x-bridge-backend": "modal",
                "x-bridge-workflow-name": "fulltest-header-dryrun",
            },
            json={"prompt": {"not": "a graph"}, "client_id": "fulltest-header-dryrun"},
        )
        after = client.get(f"{BASE}/api/v1/requests", params={"limit": 5}).json()
        result = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        result["routing_preview"] = {
            "status": rules.status_code,
            "body": rules.json(),
        }
        result["malformed_after_preview"] = {
            "status": malformed.status_code,
            "body": malformed.json(),
        }
        result["after_ids"] = [item["id"] for item in after["items"]]
        EVIDENCE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
