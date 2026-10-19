"""One-off read-only/dry-run probe for the Web full-test run.

It never launches local inference and never submits a cloud job: the first
payload is invalid, while the second is stopped at the privacy confirmation.
"""

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
        before = client.get(f"{BASE}/api/v1/requests", params={"limit": 5}).json()
        baseline_ids = {item["id"] for item in before["items"]}

        malformed = client.post(
            f"{BASE}/api/prompt",
            headers={
                "content-type": "application/json",
                "x-bridge-backend": "modal",
                "x-bridge-workflow-name": "fulltest-header-dryrun",
            },
            json={"prompt": {"not": "a graph"}, "client_id": "fulltest-header-dryrun"},
        )
        privacy = client.post(
            f"{BASE}/api/prompt",
            headers={
                "content-type": "application/json",
                "x-bridge-backend": "modal",
                "x-bridge-workflow-name": "fulltest-header-dryrun",
            },
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
                "client_id": "fulltest-header-dryrun",
            },
        )
        after = client.get(f"{BASE}/api/v1/requests", params={"limit": 5}).json()
        html = client.get(f"{BASE}/")

        def body(response: httpx.Response) -> object:
            try:
                return response.json()
            except ValueError:
                return response.text[:800]

        result = {
            "baseline_ids": sorted(baseline_ids),
            "malformed": {
                "status": malformed.status_code,
                "body": body(malformed),
            },
            "privacy_challenge": {
                "status": privacy.status_code,
                "body": body(privacy),
            },
            "after_ids": [item["id"] for item in after["items"]],
            "new_records": [
                item for item in after["items"] if item["id"] not in baseline_ids
            ],
            "injected_privacy": "cb_privacy_confirmed" in html.text,
            "injected_overlay": "comfybridge" in html.text.lower(),
            "html_bytes": len(html.content),
        }
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
