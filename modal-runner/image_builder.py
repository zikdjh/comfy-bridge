"""读 lock/nodes.lock.yaml 生成节点安装脚本与镜像构建参数（M2-2 实现）。

职责（对应规划文档 §3.3 节点同步引擎）：
- 解析 nodes.lock.yaml（每个节点的 repo / commit / pip 依赖）
- 生成确定性的 install_nodes.sh（固定 commit 克隆 + 安装 requirements）
- 与 runner.py 的 Image 定义对接；重建失败时回滚上一版镜像
"""
