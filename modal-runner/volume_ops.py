"""数据卷维护脚本（modal run 临时调用，不常驻部署）。

- move_entries: 在挂载容器内 shutil 复制条目到目标目录 + vol.remove_file 删源。
  复制经 FUSE 挂载完成 = 云端内部拷贝，不走本地带宽；V1 卷无服务端 rename/copy
  原语（实测 copy_files 静默无效、cp -r 拒绝），这是卷内搬移的唯一可靠路径。
- 用法: CB_MODAL_VOLUME=<卷名> modal run modal-runner/volume_ops.py::move_entries \
        --entries-json '[["src","dst","dir|file"],...]'
  （src/dst 均为卷内相对路径；dst 是完整目标路径 dest_dir/name）
- 输出: 每个条目开始/进度/完成时向 stdout 打一行 JSON，供后端作业引擎泵进度。
"""

import json
import os
import shutil

import modal

VOLUME_NAME = os.environ.get("CB_MODAL_VOLUME") or "comfyui-data"
vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=False)

app = modal.App("cb-volume-ops")

image = modal.Image.debian_slim()


@app.function(image=image, volumes={"/vol": vol}, timeout=3600)
def move_entries(entries_json: str) -> str:
    results: list[dict] = []

    def emit(payload: dict) -> None:
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    for src, dst, kind in json.loads(entries_json):
        s, d = f"/vol/{src}", f"/vol/{dst}"
        try:
            if kind == "dir":
                files: list[str] = []
                for dirpath, _, filenames in os.walk(s):
                    for fn in filenames:
                        files.append(os.path.join(dirpath, fn))
                emit({"type": "entry_start", "src": src, "files_total": len(files)})
                total = len(files)
                done = 0
                for fp in files:
                    target = os.path.join(d, os.path.relpath(fp, s))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(fp, target)
                    done += 1
                    emit({"type": "file_progress", "src": src,
                          "files_done": done, "files_total": total})
                # 空子目录同样搬过去（文件循环不会创建空目录）
                for dirpath, dirnames, _ in os.walk(s):
                    os.makedirs(os.path.join(d, os.path.relpath(dirpath, s)),
                                exist_ok=True)
                vol.commit()
                vol.remove_file(src, recursive=True)
                vol.commit()
            else:
                emit({"type": "entry_start", "src": src, "files_total": 1})
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                vol.commit()
                vol.remove_file(src)
                vol.commit()
            results.append({"src": src, "ok": True, "error": None})
            emit({"type": "entry_done", "src": src, "ok": True, "error": None})
        except Exception as exc:  # noqa: BLE001 —— 单条目失败不拖垮整批
            results.append(
                {"src": src, "ok": False,
                 "error": f"{type(exc).__name__}: {exc}"[:300]}
            )
            emit({"type": "entry_done", "src": src, "ok": False,
                  "error": f"{type(exc).__name__}: {exc}"[:300]})
    return json.dumps({"results": results, "ok": all(r["ok"] for r in results)},
                      ensure_ascii=False)
