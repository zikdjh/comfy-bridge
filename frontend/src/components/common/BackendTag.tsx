import { Tag } from "antd";
import { CloudOutlined, LaptopOutlined } from "@ant-design/icons";

export type Backend = "local" | "modal";

/** 全局唯一的后端语义 Tag：本地=绿、云端=蓝紫 */
export function BackendTag({ backend }: { backend: Backend }) {
  return backend === "local" ? (
    <Tag color="green" icon={<LaptopOutlined />}>本地</Tag>
  ) : (
    <Tag color="geekblue" icon={<CloudOutlined />}>云端</Tag>
  );
}
