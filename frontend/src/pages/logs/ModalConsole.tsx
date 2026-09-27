import { Tag, Tooltip } from "antd";
import { CloudServerOutlined } from "@ant-design/icons";
import { ConsolePane } from "../../components/common/ConsolePane";
import { openModalLogStream } from "../../api/logs";

/** 实时控制台右栏：Modal Runner 执行控制台。
 *  内容为桥接器执行器的结构化里程碑（受理/上传/提交/进度/回传/失败），
 *  与左栏本地 stdout 对照查看；云端容器原始 stdout 用 `modal app logs comfyui-l40s`。 */
export function ModalConsole() {
  return (
    <ConsolePane
      title="Modal Runner"
      openStream={openModalLogStream}
      downloadPrefix="comfybridge-modal"
      footnote="来源：执行器里程碑（受理/上传/提交/回传）+ 云端容器 stdout（/internal/logs/raw 每 2.5s 轮询差分，时间戳为采集时刻）；历史缓冲 500 条，重启清空。"
      emptyHint={(online) =>
        online
          ? "已连接。提交一个路由到云端的工作流，执行里程碑会实时滚动到这里。"
          : "等待连接控制台事件流…"
      }
      headerExtra={({ status, statusError, channel }) => (
        <>
          {status === "online" ? (
            <Tag color="processing" style={{ marginInlineEnd: 0 }}>● 实时推送中</Tag>
          ) : status === "connecting" ? (
            <Tag color="default" style={{ marginInlineEnd: 0, cursor: "default" }}>● 连接中</Tag>
          ) : (
            <Tooltip title={statusError || "正在连接控制台事件流…"}>
              <Tag color="warning" style={{ marginInlineEnd: 0, cursor: "default" }}>
                ● 通道断开，重连中
              </Tag>
            </Tooltip>
          )}
          {channel === undefined ? null : channel.available ? (
            <Tag color="success" style={{ marginInlineEnd: 0 }}>● 执行通道就绪</Tag>
          ) : (
            <Tooltip title={`Modal 执行通道不可用（${channel.detail || "未部署/未配置"}）`}>
              <Tag color="default" style={{ marginInlineEnd: 0, cursor: "default" }}>
                <CloudServerOutlined /> 通道未就绪
              </Tag>
            </Tooltip>
          )}
        </>
      )}
    />
  );
}
