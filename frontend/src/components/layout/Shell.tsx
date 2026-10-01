import { useState } from "react";
import { Badge, Button, Layout, Menu, Space, Tag, Tooltip } from "antd";
import {
  ClusterOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileImageOutlined,
  HddOutlined,
  NodeIndexOutlined,
  PartitionOutlined,
  PictureOutlined,
  PieChartOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import type { MenuProps } from "antd";
import { useHealth } from "../../api/health";
import { useCostSummary } from "../../api/cost";
import { LOCAL_COMFY_URL } from "../../theme";

const { Sider, Header, Content } = Layout;

const NAV_ITEMS: MenuProps["items"] = [
  { key: "/", icon: <DashboardOutlined />, label: "仪表盘" },
  { key: "/routing", icon: <NodeIndexOutlined />, label: "路由规则" },
  { key: "/nodes", icon: <ClusterOutlined />, label: "节点管理" },
  { key: "/workflows", icon: <PartitionOutlined />, label: "工作流" },
  { key: "/models", icon: <DatabaseOutlined />, label: "模型卷" },
  { key: "/volume", icon: <HddOutlined />, label: "数据卷" },
  { key: "/media", icon: <FileImageOutlined />, label: "媒体库" },
  { key: "/cost", icon: <PieChartOutlined />, label: "成本报表" },
  { key: "/logs", icon: <UnorderedListOutlined />, label: "日志" },
  { key: "/settings", icon: <SettingOutlined />, label: "设置" },
];

type PillState = "online" | "offline" | "pending";

function StatusPill({ title, sub, state, tooltip }: { title: string; sub: string; state: PillState; tooltip?: string }) {
  const badge = state === "online" ? "success" : state === "offline" ? "error" : "default";
  const inner = (
    <Space size={6}>
      <Badge status={badge} />
      <span style={{ fontSize: 13 }}>
        <b>{title}</b>
        <span style={{ color: "#8a90a2", marginLeft: 6 }}>{sub}</span>
      </span>
    </Space>
  );
  return tooltip ? <Tooltip title={tooltip}>{inner}</Tooltip> : inner;
}

export function Shell() {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const { data: health, isLoading } = useHealth();

  const local = health?.local;
  const bridgeUrl = health?.bridge?.url ?? LOCAL_COMFY_URL;
  const localState: PillState = isLoading ? "pending" : local?.online ? "online" : "offline";
  const localSub = isLoading
    ? "检测中…"
    : local?.online
      ? `${local.version ?? "未知版本"} · ${local.latency_ms ?? "?"}ms`
      : "未连接";
  const modal = health?.modal;
  const modalState: PillState = isLoading ? "pending" : modal?.online ? "online" : "offline";
  const modalSub = isLoading ? "检测中…" : modal?.online ? "已接入" : "未接入";

  // 顶栏预算角标（M4-1）：读额度汇总（30s 轮询），未启用护栏时置灰提示入口
  const { data: cost } = useCostSummary();
  const budget = cost?.budget;
  const budgetTag = !budget ? (
    <Tag color="default" style={{ cursor: "default" }}>预算 —</Tag>
  ) : !budget.enabled ? (
    <Tag color="default" style={{ cursor: "default" }}>预算 未启用</Tag>
  ) : budget.exceeded ? (
    <Tag color="error">预算超限 ${budget.used_usd.toFixed(2)}/${budget.monthly_usd.toFixed(2)}</Tag>
  ) : (
    <Tag color="processing">预算 ${budget.used_usd.toFixed(2)}/${budget.monthly_usd.toFixed(2)}</Tag>
  );
  const budgetTooltip = !budget?.enabled
    ? "月度预算护栏未启用：在仪表盘「本月额度」卡片设置上限后，超限请求将自动回落本地或拒绝"
    : budget.exceeded
      ? `本月云端估算 $${budget.used_usd.toFixed(2)} 已达上限 $${budget.monthly_usd.toFixed(2)}：新云端请求将${budget.on_exceed === "reject" ? "被拒绝" : "回落本地"}`
      : `本月云端估算 $${budget.used_usd.toFixed(2)} / 上限 $${budget.monthly_usd.toFixed(2)}（估算 = GPU 单价 × 请求墙钟秒数）`;
  const modalLine = cost?.modal?.ok
    ? `；Modal 账单本期消耗 $${(cost.modal.metered_cost_usd ?? 0).toFixed(2)}（UTC 月）`
    : "";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        theme="light"
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        width={200}
        style={{ borderRight: "1px solid #e4e7ef" }}
      >
        <div
          style={{
            height: 52,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: collapsed ? 14 : 16,
            letterSpacing: 1,
          }}
        >
          {collapsed ? "CB" : "ComfyBridge"}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={NAV_ITEMS}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: "none" }}
        />
        <div style={{ position: "absolute", bottom: 12, width: "100%", textAlign: "center", color: "#8a90a2", fontSize: 12 }}>
          {!collapsed && <span>v0.1.0</span>}
        </div>
      </Sider>
      <Layout>
        <Header
          style={{
            borderBottom: "1px solid #e4e7ef",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            paddingInline: 16,
          }}
        >
          <Space size={24}>
            <StatusPill
              title="本地 ComfyUI"
              sub={localSub}
              state={localState}
              tooltip={local?.online ? `GPU: ${local.gpu ?? "未知"}` : (local?.error ?? `本地服务未启动（${LOCAL_COMFY_URL}）`)}
            />
            <StatusPill
              title="Modal Runner"
              sub={modalSub}
              state={modalState}
              tooltip={modal?.reason ?? "检测中…"}
            />
          </Space>
          <Space size={12}>
            <Tooltip title={budgetTooltip + modalLine} placement="bottom">
              {budgetTag}
            </Tooltip>
            <Tooltip title={`经桥接器 ${bridgeUrl}（生成请求将被拦截路由）`} placement="bottom">
              <Button type="primary" ghost icon={<PictureOutlined />} href={bridgeUrl} target="_blank">
                前往出图界面
              </Button>
            </Tooltip>
          </Space>
        </Header>
        <Content style={{ padding: 16 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
