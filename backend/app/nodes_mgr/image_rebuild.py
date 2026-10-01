"""镜像固化（M2-2）：本地节点锁 → install_nodes.sh → modal deploy 把依赖预装进镜像。

与卷模式的关系：卷同步（/nodes/sync）仍是「即时试装」路径——文件落卷、依赖等下次
冷启动兜底安装；固化把「启用节点集」的依赖烧进镜像，冷启动只剩兜底检查（秒级）。
两者并存：固化后新同步的节点（不在锁里）仍走冷启动兜底，直到下次固化。
流程：扫描本地 → 生成锁+脚本（备份旧内容）→ 覆盖写入 → deploy（deploy.py 单飞）；
deploy 非零退出/CLI 缺失时回滚锁与脚本；超时不回滚（镜像可能已建成）。
"""

from __future__ import annotations

import asyncio

from . import deploy, lockfile
from .scan import scan_custom_nodes


async def start_rebuild() -> dict:
    """生成锁文件与预装脚本并触发镜像重建；部署进行中抛 RuntimeError（API 转 409）。"""
    scan = await asyncio.to_thread(scan_custom_nodes)
    lock = lockfile.generate(scan)
    script = lockfile.render_install_script(lock)
    backup = lockfile.backup_current()
    await deploy.begin_exclusive(
        f"镜像固化（预装 {len(lock['nodes'])} 个节点的依赖，ComfyUI {lock['comfyui_version']}）"
    )
    lockfile.write_outputs(lock, script)
    deploy.spawn(lambda: lockfile.restore(*backup))
    return deploy.job_state()
