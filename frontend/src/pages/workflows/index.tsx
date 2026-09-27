import { Alert, Button, Card, Modal, Space, Spin, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { message } from "antd";
import { useLocalWorkflows, type LocalWorkflow } from "../../api/workflows";
import { BackendTag, type Backend } from "../../components/common/BackendTag";
import { EmptyGuide } from "../../components/common/EmptyGuide";

/* ---------------- 本地清单（真实数据） ---------------- */

function fmtSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function LocalInventory() {
  const { data, isLoading, refetch, isFetching } = useLocalWorkflows();

  if (isLoading) return <Spin style={{ display: "block", margin: "48px auto" }} />;
  if (!data) return null;

  const wfColumns: ColumnsType<LocalWorkflow> = [
    { title: "名称", dataIndex: "name" },
    { title: "文件", dataIndex: "file", render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
    { title: "大小", dataIndex: "size_bytes", width: 100, render: (v: number) => fmtSize(v) },
    {
      title: "修改时间",
      dataIndex: "mtime",
      width: 150,
      render: (v: string) => dayjs(v).format("MM-DD HH:mm"),
    },
    { title: "hash", dataIndex: "hash", width: 120, render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message={`工作流目录：${data.workflows_dir}${data.dir_exists ? "" : "（目录尚未创建）"}`}
        action={
          <Button size="small" onClick={() => refetch()} loading={isFetching}>
            重新扫描
          </Button>
        }
      />
      <Card size="small" title={`已保存的工作流（${data.workflows.length}）`}>
        {data.workflows.length === 0 ? (
          <EmptyGuide
            description="本地尚未保存工作流 —— 在出图界面（顶栏入口）保存工作流后，此处自动出现"
          />
        ) : (
          <Table<LocalWorkflow> columns={wfColumns} dataSource={data.workflows} pagination={false} size="small" />
        )}
      </Card>
      {data.assets_db && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          资产数据库 comfyui.db：{data.assets_db.assets_count ?? "?"} 条资产（工作流以文件保存，不入库）
        </Typography.Text>
      )}
    </Space>
  );
}

/* ---------------- 同步状态 / 运行统计（静态，M2 接入） ---------------- */

type WorkflowStatus = "synced" | "local_only" | "cloud_only" | "diff" | "conflict";

const statusMeta: Record<WorkflowStatus, { color: string; text: string }> = {
  synced: { color: "green", text: "已同步" },
  local_only: { color: "default", text: "仅本地" },
  cloud_only: { color: "geekblue", text: "仅云端" },
  diff: { color: "orange", text: "有差异" },
  conflict: { color: "red", text: "冲突" },
};

interface WorkflowRow {
  key: string;
  name: string;
  localTime?: string;
  cloudTime?: string;
  status: WorkflowStatus;
  lastRun: string;
  lastBackend?: Backend;
}

const rows: WorkflowRow[] = [
  { key: "1", name: "SDXL文生图", localTime: "09-24 18:32", cloudTime: "09-24 18:35", status: "synced", lastRun: "09-25 10:02", lastBackend: "local" },
  { key: "2", name: "cloud:flux人像", cloudTime: "09-23 09:10", status: "cloud_only", lastRun: "09-25 09:47", lastBackend: "modal" },
  { key: "3", name: "img2img局部重绘", localTime: "09-22 14:20", cloudTime: "09-21 11:05", status: "diff", lastRun: "09-25 08:30", lastBackend: "local" },
  { key: "4", name: "视频插帧VFI", localTime: "09-20 20:44", cloudTime: "09-24 16:03", status: "conflict", lastRun: "09-24 16:05", lastBackend: "modal" },
];

interface StatsRow {
  key: string;
  name: string;
  runs: number;
  avg: string;
  max: string;
  cost: string;
  backend: Backend;
}

const statsRows: StatsRow[] = [
  { key: "1", name: "SDXL文生图", runs: 28, avg: "9.2s", max: "15s", cost: "$0.0000", backend: "local" },
  { key: "2", name: "cloud:flux人像", runs: 12, avg: "33s", max: "58s", cost: "$0.2208", backend: "modal" },
];

const syncColumns: ColumnsType<WorkflowRow> = [
  { title: "名称", dataIndex: "name" },
  { title: "本地版本", dataIndex: "localTime", width: 130, render: (v?: string) => v ?? "—" },
  { title: "云端版本", dataIndex: "cloudTime", width: 130, render: (v?: string) => v ?? "—" },
  {
    title: "状态",
    dataIndex: "status",
    width: 100,
    render: (s: WorkflowStatus) => <Tag color={statusMeta[s].color}>{statusMeta[s].text}</Tag>,
  },
  {
    title: "最近运行",
    width: 150,
    render: (_v, r) =>
      r.lastRun === "—" ? "—" : (
        <Space size={4}>
          <BackendTag backend={r.lastBackend!} />
          {r.lastRun}
        </Space>
      ),
  },
  {
    title: "操作",
    width: 160,
    render: () => (
      <Space size={0}>
        <Button size="small" type="link" onClick={() => message.info("静态占位：M2 接入工作流同步")}>
          同步
        </Button>
        <Button size="small" type="link" onClick={() => message.info("静态占位：M2 双列对比视图")}>
          对比
        </Button>
      </Space>
    ),
  },
];

const statsColumns: ColumnsType<StatsRow> = [
  { title: "工作流", dataIndex: "name" },
  { title: "运行次数", dataIndex: "runs", width: 90 },
  { title: "平均耗时", dataIndex: "avg", width: 90 },
  { title: "最大耗时", dataIndex: "max", width: 90 },
  { title: "估算成本", dataIndex: "cost", width: 100 },
  { title: "常用后端", dataIndex: "backend", width: 90, render: (b: Backend) => <BackendTag backend={b} /> },
];

function SyncStatus() {
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert type="warning" showIcon message="示例数据 —— 云端版本信息在 M0/M2 接入 Modal Runner 后为真实值" />
      <Table<WorkflowRow> columns={syncColumns} dataSource={rows} pagination={false} size="small" />
    </Space>
  );
}

export default function WorkflowsPage() {
  return (
    <Card size="small">
      <Tabs
        defaultActiveKey="local"
        items={[
          { key: "local", label: "本地清单", children: <LocalInventory /> },
          { key: "sync", label: "同步状态", children: <SyncStatus /> },
          {
            key: "stats",
            label: "运行统计",
            children: <Table<StatsRow> columns={statsColumns} dataSource={statsRows} pagination={false} size="small" />,
          },
        ]}
      />
    </Card>
  );
}
