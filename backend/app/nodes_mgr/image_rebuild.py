"""镜像重建（M2-2 固化 / M2-5 版本对齐 / 合并入口共用）。

与卷模式的关系：卷同步（/nodes/sync）仍是「即时试装」路径——文件落卷、依赖等下次
冷启动兜底安装；固化把「启用节点集」的依赖烧进镜像，冷启动只剩兜底检查（秒级）。
两者并存：固化后新同步的节点（不在锁里）仍走冷启动兜底，直到下次固化。

入口合并（M2 收尾）：align_version=True 时把「版本 pin 对齐」并入同一次重建——
本地版本 != 云端 pin 才写 pin，一次 modal deploy 同时完成固化 + 对齐，避免两次
重建（每次数十秒～数分钟）。流程：扫描本地 → 生成锁+脚本（备份旧内容）→
begin_exclusive（409 互斥，此后才动文件）→ 覆盖写入（+写 pin）→ deploy；
deploy 非零退出/CLI 缺失时回滚锁与脚本与 pin；超时不回滚（镜像可能已建成）。
版本 pin 实际变更时挂 deploy 收尾回调，对齐历史（version_align.HISTORY_FILE）留痕。
"""

from __future__ import annotations

import asyncio

from ..core.config import get_settings
from . import deploy, lockfile, version_align
from .scan import scan_custom_nodes


async def start_rebuild(align_version: bool = False) -> dict:
    """生成锁文件与预装脚本并触发镜像重建；align_version=True 时并入库内版本对齐。

    部署进行中抛 RuntimeError（API 转 409）；本地版本缺失只跳过对齐不阻塞固化。
    """
    scan = await asyncio.to_thread(scan_custom_nodes)
    lock = lockfile.generate(scan)
    script = lockfile.render_install_script(lock)
    backup = lockfile.backup_current()

    pin_change: tuple[str, str] | None = None  # (旧 pin, 新 tag)，begin_exclusive 成功后才落盘
    local_commit: str | None = None
    started_ms = version_align.now_ms()
    if align_version:
        version, local_commit = version_align.local_version(get_settings().local.comfy_root)
        if version:
            tag = version_align.tag_for(version)
            pin = version_align.read_pin()
            if tag != pin:
                pin_change = (pin, tag)

    label = f"镜像重建（固化 {len(lock['nodes'])} 个节点的依赖，ComfyUI {lock['comfyui_version']}）"
    if pin_change:
        label += f" + 版本对齐 → {pin_change[1]}（此前 {pin_change[0]}）"
    await deploy.begin_exclusive(label)
    lockfile.write_outputs(lock, script)
    if pin_change:
        version_align.write_pin(pin_change[1])
        # 随镜像重建的版本对齐同样留痕（deploy 收尾回调拿到真实结局）
        deploy.set_finish_sink(version_align.sink_for(
            "rebuild", pin_change[0], pin_change[1], started_ms, local_commit))

    def rollback() -> None:
        lockfile.restore(*backup)
        if pin_change:
            try:
                version_align.write_pin(pin_change[0])
            except OSError:
                pass  # pin 回滚失败不掩盖部署错误本身

    deploy.spawn(rollback)
    return deploy.job_state()
