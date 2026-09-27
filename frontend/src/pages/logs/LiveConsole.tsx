import { Tag, Tooltip } from "antd";
import { ConsolePane } from "../../components/common/ConsolePane";
import { openLocalLogStream } from "../../api/logs";

/** 实时控制台左栏：本地 ComfyUI stdout 日志（SSE 快照 + 实时增量）。 */
export function LiveConsole() {
  return (
    <ConsolePane
      title="本地 ComfyUI"
      openStream={openLocalLogStream}
      downloadPrefix="comfyui-local"
      footnote="来源：本地 ComfyUI stdout 拦截缓冲（快照最近 ≤300 条）+ WebSocket 实时推送；仅渲染可视区行（虚拟滚动）。"
      emptyHint={(online) =>
        online
          ? "已连接，暂无匹配的日志。出一张图试试 —— 生成过程会实时滚动到这里。"
          : "等待连接本地 ComfyUI 日志通道…"
      }
      headerExtra={({ status, statusError }) =>
        status === "online" ? (
          <Tag color="processing" style={{ marginInlineEnd: 0 }}>● 实时推送中</Tag>
        ) : status === "connecting" ? (
          <Tag color="default" style={{ marginInlineEnd: 0, cursor: "default" }}>● 连接中</Tag>
        ) : (
          <Tooltip title={statusError || "正在连接本地 ComfyUI 日志通道…"}>
            <Tag color="warning" style={{ marginInlineEnd: 0, cursor: "default" }}>
              ● 通道断开，重连中
            </Tag>
          </Tooltip>
        )
      }
    />
  );
}
