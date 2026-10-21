import { useEffect, useState } from "react";
import { Alert, Badge, Button, Layout, Menu, Space, Tag, Tooltip } from "antd";
import {
  ClusterOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileImageOutlined,
  HddOutlined,
  MenuOutlined,
  NodeIndexOutlined,
  PartitionOutlined,
  PictureOutlined,
  PieChartOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import type { MenuProps } from "antd";
import { Drawer, Menu as AntMenu } from "antd";
import { useHealth } from "../../api/health";
import { useCostSummary } from "../../api/cost";
import { useConfig } from "../../api/config";
import { LOCAL_COMFY_URL } from "../../theme";
import { ConfigWizard, WIZARD_DONE_KEY } from "../../pages/settings/ConfigWizard";
import { JobDrawer } from "../common/JobDrawer";
import { JOB_DRAWER_EVENT, useJobs, type JobsSnapshot, type SyncJob } from "../../api/jobs";
import { openBridgeEventStream } from "../../api/events";
import { useQueryClient } from "@tanstack/react-query";
import { UpdateBanner } from "./UpdateBanner";
import { useIsNarrow } from "../../hooks/useMediaQuery";
import { useIsCompactDesktop } from "../../hooks/useDesktopLayout";
import { useDarkMode } from "../../hooks/useDarkMode";

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
  { key: "/status", icon: <SafetyCertificateOutlined />, label: "系统状态" },
  { key: "/settings", icon: <SettingOutlined />, label: "设置" },
];

type PillState = "online" | "offline" | "pending";

function StatusPill({ title, sub, state, tooltip, compact }: { title: string; sub: string; state: PillState; tooltip?: string; compact?: boolean }) {
  const { tokens: t } = useDarkMode();
  const badge = state === "online" ? "success" : state === "offline" ? "error" : "default";
  const inner = (
    <Space size={6}>
      <Badge status={badge} />
      <span style={{ fontSize: 13 }}>
        <b>{title}</b>
        {compact ? (
          <span style={{ color: t.colors.textSecondary, marginLeft: 6 }}>{state === "online" ? "在线" : state === "offline" ? "离线" : "检测中"}</span>
        ) : (
          <span style={{ color: t.colors.textSecondary, marginLeft: 6 }}>{sub}</span>
        )}
      </span>
    </Space>
  );
  return tooltip ? <Tooltip title={tooltip}>{inner}</Tooltip> : inner;
}

export function Shell() {
  const { tokens: t, isDark } = useDarkMode();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  // 低于桌面最低宽度时用抽屉兜底；1024-1279px 自动使用紧凑 Sider。
  const isNarrow = useIsNarrow();
  const isCompactDesktop = useIsCompactDesktop();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const { data: health, isLoading } = useHealth();
  const siderCollapsed = isCompactDesktop || collapsed;

  // —— 首次配置向导（M4-3）：Modal 凭证未配置且本浏览器未完成过向导时，自动弹一次 + 顶部常驻提示 ——
  const { data: config } = useConfig();
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardDismissed, setWizardDismissed] = useState(() => {
    try {
      return localStorage.getItem(WIZARD_DONE_KEY) === "1";
    } catch {
      return true; // localStorage 不可用时保持安静，不打扰
    }
  });
  const needSetup = !!config && !config.modal.token_id;

  // —— 全局任务抽屉（M2-6）：顶栏角标显示进行中任务数，页面经 openJobDrawer() 唤起 ——
  const { data: jobsData } = useJobs();
  const [jobsOpen, setJobsOpen] = useState(false);
  const [jobFocusId, setJobFocusId] = useState<string | null>(null);
  const [eventStreamOnline, setEventStreamOnline] = useState(false);
  useEffect(() => {
    const onOpen = (ev: Event) => {
      const jobId = (ev as CustomEvent<{ jobId?: string }>).detail?.jobId ?? null;
      setJobFocusId(jobId);
      setJobsOpen(true);
    };
    window.addEventListener(JOB_DRAWER_EVENT, onOpen);
    return () => window.removeEventListener(JOB_DRAWER_EVENT, onOpen);
  }, []);
  const runningJobs = jobsData?.running ?? 0;

  // 键盘用户无需离开当前页；输入框内不抢 Ctrl/⌘+J。
  useEffect(() => {
    const onShortcut = (ev: KeyboardEvent) => {
      const target = ev.target as HTMLElement | null;
      if (!(ev.ctrlKey || ev.metaKey) || ev.key.toLowerCase() !== "j") return;
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      ev.preventDefault();
      setJobFocusId(null);
      setJobsOpen((previous) => !previous);
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, []);

  // —— 统一事件流（P1-5）：推送直接回写查询缓存；断线由 EventSource 自动重连并轮询兜底 ——
  useEffect(() => {
    const upsertJob = (job: SyncJob) => {
      queryClient.setQueryData<JobsSnapshot>(["jobs"], (old) => {
        const jobs = old?.jobs ?? [];
        const next = [job, ...jobs.filter((item) => item.id !== job.id)];
        return { jobs: next, running: next.filter((item) => item.state === "running").length };
      });
      queryClient.setQueryData(["jobs", job.id], job);
    };

    return openBridgeEventStream({
      onStatus: (online) => setEventStreamOnline(online),
      onEvent: (item) => {
        if (item.event === "resync") {
          void queryClient.invalidateQueries({ queryKey: ["jobs"] });
          void queryClient.invalidateQueries({ queryKey: ["cost-summary"] });
          void queryClient.invalidateQueries({ queryKey: ["nodes", "version"] });
          return;
        }
        if (item.event === "job" || item.event === "node_sync") {
          const job = (item.data as { job?: SyncJob } | null)?.job;
          if (job?.id) upsertJob(job);
          return;
        }
        if (item.event === "deploy") {
          void queryClient.invalidateQueries({ queryKey: ["nodes", "version"] });
          return;
        }
        if (item.event === "cost_alert") {
          void queryClient.invalidateQueries({ queryKey: ["cost-summary"] });
        }
      },
    });
  }, [queryClient]);
  useEffect(() => {
    if (!needSetup || wizardDismissed) return;
    const timer = window.setTimeout(() => setWizardOpen(true), 600);
    return () => window.clearTimeout(timer);
  }, [needSetup, wizardDismissed]);
  const closeWizard = () => {
    setWizardOpen(false);
    setWizardDismissed(true);
    try {
      localStorage.setItem(WIZARD_DONE_KEY, "1");
    } catch {
      /* 同上，忽略 */
    }
  };
  const dismissBanner = () => {
    setWizardDismissed(true);
    try {
      localStorage.setItem(WIZARD_DONE_KEY, "1");
    } catch {
      /* 忽略 */
    }
  };

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
    ? "月度预算护栏未启用：在仪表盘「本月额度」卡片设置上限后，超限请求将自动回落本地或拒绝。这是本地估算护栏，不是 Modal 账户级硬上限"
    : budget.exceeded
      ? `本月云端估算 $${budget.used_usd.toFixed(2)} 已达上限 $${budget.monthly_usd.toFixed(2)}：新云端请求将${budget.on_exceed === "reject" ? "被拒绝" : "回落本地"}。这是本地估算护栏，不是 Modal 账户级硬上限`
      : `本月云端估算 $${budget.used_usd.toFixed(2)} / 上限 $${budget.monthly_usd.toFixed(2)}（估算 = GPU 单价 × 请求墙钟秒数；本地护栏，不是 Modal 账户级硬上限）`;
  const modalLine = cost?.modal?.ok
    ? `；Modal 账单本期消耗 $${(cost.modal.metered_cost_usd ?? 0).toFixed(2)}（UTC 月）`
    : "";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      {/* 低于桌面最低宽度的兜底导航；1024px 起始终使用桌面 Sider。 */}
      <Drawer
        placement="left"
        open={isNarrow && mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        width={220}
        styles={{ body: { padding: 0 } }}
        title={
          <span style={{ fontWeight: 700, letterSpacing: 1 }}>ComfyBridge</span>
        }
      >
        <AntMenu
          mode="inline"
          theme={isDark ? "dark" : "light"}
          selectedKeys={[location.pathname]}
          items={NAV_ITEMS}
          onClick={({ key }) => {
            navigate(key);
            setMobileMenuOpen(false);
          }}
          style={{ borderInlineEnd: "none" }}
        />
        <div style={{ position: "absolute", bottom: 12, width: "100%", textAlign: "center", color: t.colors.textSecondary, fontSize: 12 }}>
          <span>v0.1.0</span>
        </div>
      </Drawer>

      {!isNarrow && (
        <Sider
          theme={isDark ? "dark" : "light"}
          collapsible
          collapsed={siderCollapsed}
          onCollapse={setCollapsed}
          width={200}
          collapsedWidth={64}
          style={{ borderRight: `1px solid ${t.colors.border}` }}
        >
          <div
            style={{
              height: 52,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 700,
              fontSize: siderCollapsed ? 14 : 16,
              letterSpacing: 1,
            }}
          >
            {siderCollapsed ? "CB" : "ComfyBridge"}
          </div>
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            items={NAV_ITEMS}
            onClick={({ key }) => navigate(key)}
            style={{ borderInlineEnd: "none" }}
          />
          <div style={{ position: "absolute", bottom: 12, width: "100%", textAlign: "center", color: t.colors.textSecondary, fontSize: 12 }}>
            {!siderCollapsed && <span>v0.1.0</span>}
          </div>
        </Sider>
      )}
      <Layout>
        <Header
          style={{
            borderBottom: `1px solid ${t.colors.border}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            paddingInline: 16,
            gap: 16,
            overflow: "hidden",
          }}
        >
          <Space size={isNarrow ? 12 : isCompactDesktop ? 16 : 24} style={{ minWidth: 0, flex: 1 }}>
            {isNarrow && (
              <Button
                icon={<MenuOutlined />}
                onClick={() => setMobileMenuOpen(true)}
                aria-label="打开导航菜单"
                style={{ flexShrink: 0 }}
              />
            )}
            <StatusPill
              title="本地"
              sub={localSub}
              state={localState}
              tooltip={local?.online ? `GPU: ${local.gpu ?? "未知"}` : (local?.error ?? `本地服务未启动（${LOCAL_COMFY_URL}）`)}
              compact={isNarrow || isCompactDesktop}
            />
            <StatusPill
              title="Modal"
              sub={modalSub}
              state={modalState}
              tooltip={modal?.reason ?? "检测中…"}
              compact={isNarrow || isCompactDesktop}
            />
          </Space>
          <Space size={12} style={{ flexShrink: 0 }}>
            <Tooltip title={budgetTooltip + modalLine} placement="bottom">
              <span style={{ whiteSpace: "nowrap" }}>{budgetTag}</span>
            </Tooltip>
            <Tooltip
              title={eventStreamOnline ? "任务状态实时推送已连接" : "事件流不可用，任务状态每 15 秒轮询兜底"}
              placement="bottom"
            >
              <Badge count={runningJobs} size="small" offset={[-2, 2]}>
                <Button
                  icon={<UnorderedListOutlined />}
                  aria-keyshortcuts="Control+J Meta+J"
                  onClick={() => { setJobFocusId(null); setJobsOpen(true); }}
                >
                  任务
                </Button>
              </Badge>
            </Tooltip>
            <Tooltip title={`经桥接器 ${bridgeUrl}（生成请求将被拦截路由）`} placement="bottom">
              <Button
                type="primary"
                ghost
                icon={<PictureOutlined />}
                onClick={() => window.location.assign(bridgeUrl)}
              >
                {isCompactDesktop ? "出图" : "前往出图界面"}
              </Button>
            </Tooltip>
          </Space>
        </Header>
        <Content style={{ padding: 16, minWidth: 0, overflowX: "hidden" }}>
          <UpdateBanner />
          {needSetup && !wizardDismissed && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="尚未配置 Modal 云端凭证 —— 先用配置向导完成初始化（也可稍后在「设置 → Modal」补齐，纯本地模式不受影响）"
              action={
                <Space>
                  <Button size="small" type="primary" onClick={() => setWizardOpen(true)}>
                    配置向导
                  </Button>
                  <Button size="small" onClick={dismissBanner}>
                    不再提示
                  </Button>
                </Space>
              }
            />
          )}
          <Outlet />
        </Content>
      </Layout>
      <ConfigWizard open={wizardOpen} onClose={closeWizard} />
      <JobDrawer
        open={jobsOpen}
        onClose={() => setJobsOpen(false)}
        focusId={jobFocusId}
        onFocusHandled={() => setJobFocusId(null)}
        onJobSelected={setJobFocusId}
      />
    </Layout>
  );
}
