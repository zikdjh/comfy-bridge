import { useState } from "react";
import { Alert, Button, Card, Modal, Space, Spin, Table, Tabs, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useQueryClient } from "@tanstack/react-query";
import {
  applyWorkflowSync,
  useLocalWorkflows,
  useWorkflowSync,
  type LocalWorkflow,
  type WfSyncAction,
  type WfSyncRow,
} from "../../api/workflows";
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

/* ---------------- 同步状态（M2-4 真实数据：本地 ⇄ 数据卷 三方检测） ---------------- */

type WfState = WfSyncRow["state"];

const stateMeta: Record<WfState, { color: string; text: string }> = {
  synced: { color: "green", text: "已同步" },
  local_new: { color: "blue", text: "仅本地" },
  cloud_new: { color: "geekblue", text: "仅云端" },
  local_changed: { color: "orange", text: "本地已改" },
  cloud_changed: { color: "orange", text: "云端已改" },
  conflict: { color: "red", text: "冲突" },
};

const shortHash = (h: string | null) => (h ? h.slice(0, 8) : "—");

function SyncMatrix() {
  const queryClient = useQueryClient();
  const [operating, setOperating] = useState(false);
  const { data, isLoading, refetch, isFetching } = useWorkflowSync();

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["workflows"] });
  };

  const run = async (actions: { name: string; action: WfSyncAction }[], done: string) => {
    setOperating(true);
    try {
      const res = await applyWorkflowSync(actions);
      const failed = res.results.filter((r) => !r.ok);
      const copies = res.results.filter((r) => r.conflict_copy);
      if (failed.length > 0) {
        Modal.error({
          title: "部分同步动作失败",
          content: (
            <pre style={{ maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap" }}>
              {failed.map((f) => `${f.name}: ${f.error ?? "未知错误"}`).join("\n")}
            </pre>
          ),
        });
      } else {
        message.success(
          done + (copies.length > 0 ? `；冲突已留双副本：${copies.map((c) => c.conflict_copy).join("、")}` : ""),
        );
      }
      refresh();
    } catch (exc) {
      message.error(`同步请求失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setOperating(false);
    }
  };

  const syncAll = () => {
    const pending = (data?.rows ?? []).filter((r) => r.state !== "conflict" && r.suggested);
    if (pending.length === 0) return;
    Modal.confirm({
      title: `同步 ${pending.length} 个工作流？`,
      content: "仅本地/本地已改 → 推送到云端；仅云端/云端已改 → 拉取到本地。冲突行不动（需人工处理）。",
      okText: "执行同步",
      onOk: () => run(pending.map((r) => ({ name: r.name, action: r.suggested! })), `已同步 ${pending.length} 个工作流`),
    });
  };

  const columns: ColumnsType<WfSyncRow> = [
    {
      title: "文件",
      dataIndex: "name",
      render: (v: string) => <Typography.Text code>{v}</Typography.Text>,
    },
    {
      title: "本地 hash",
      dataIndex: "local_hash",
      width: 120,
      render: (v: string | null) => <Typography.Text type="secondary">{shortHash(v)}</Typography.Text>,
    },
    {
      title: "云端 hash",
      dataIndex: "cloud_hash",
      width: 120,
      render: (v: string | null) => <Typography.Text type="secondary">{shortHash(v)}</Typography.Text>,
    },
    {
      title: "状态",
      dataIndex: "state",
      width: 100,
      render: (s: WfState) => <Tag color={stateMeta[s].color}>{stateMeta[s].text}</Tag>,
    },
    {
      title: "操作",
      width: 280,
      render: (_v, r) => {
        if (r.state === "synced") return null;
        if (r.state === "conflict") {
          return (
            <Space size={0} wrap>
              <Button size="small" type="link" disabled={operating}
                onClick={() => run([{ name: r.name, action: "push" }], `已用本地版覆盖云端：${r.name}`)}>
                推本地版
              </Button>
              <Button size="small" type="link" disabled={operating}
                onClick={() => run([{ name: r.name, action: "pull" }], `已用云端版覆盖本地：${r.name}`)}>
                推云端版
              </Button>
              <Button size="small" type="link" disabled={operating}
                onClick={() => run([{ name: r.name, action: "keep_both" }], `已保留双副本：${r.name}`)}>
                保留双副本
              </Button>
            </Space>
          );
        }
        const label = r.suggested === "push" ? "推送到云端" : "拉取到本地";
        return (
          <Button size="small" type="link" disabled={operating}
            onClick={() => run([{ name: r.name, action: r.suggested! }], `已同步：${r.name}`)}>
            {label}
          </Button>
        );
      },
    },
  ];

  if (isLoading && !data) return <Spin style={{ display: "block", margin: "48px auto" }} />;
  if (!data) return null;

  const pendingCount = (data.rows ?? []).filter((r) => r.state !== "conflict" && r.suggested).length;

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      {data.ok ? (
        <Alert
          type="info"
          showIcon
          message={`云端数据卷 ${data.volume} · ${data.cloud_dir} · 待同步 ${pendingCount} / 冲突 ${(data.counts.conflict ?? 0)}`}
          description="按 last-synced 基线检测两端变更；「保留双副本」把云端版留为 ×.conflict-时间.json、本地版推上云端，之后在 ComfyUI 里人工合并。删除不传播：仅本地存在的文件会重新推回云端。"
          action={
            <Space>
              <Button size="small" onClick={() => refetch()} loading={isFetching}>
                重新对比
              </Button>
              <Button size="small" type="primary" disabled={pendingCount === 0 || operating} onClick={syncAll}>
                一键同步（{pendingCount}）
              </Button>
            </Space>
          }
        />
      ) : (
        <Alert type="error" showIcon message="云端清单读取失败" description={data.error ?? undefined} />
      )}
      <Card size="small" title={`工作流同步矩阵（${data.rows.length}）`}>
        {data.rows.length === 0 ? (
          <EmptyGuide description="本地工作流目录与云端卷 user/default/workflows/ 均为空——保存工作流后这里出现对比" />
        ) : (
          <Table<WfSyncRow> columns={columns} dataSource={data.rows} pagination={false} size="small" rowKey="name" />
        )}
      </Card>
    </Space>
  );
}

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

const statsColumns: ColumnsType<StatsRow> = [
  { title: "工作流", dataIndex: "name" },
  { title: "运行次数", dataIndex: "runs", width: 90 },
  { title: "平均耗时", dataIndex: "avg", width: 90 },
  { title: "最大耗时", dataIndex: "max", width: 90 },
  { title: "估算成本", dataIndex: "cost", width: 100 },
  { title: "常用后端", dataIndex: "backend", width: 90, render: (b: Backend) => <BackendTag backend={b} /> },
];

function RunStats() {
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert type="warning" showIcon message="示例数据 —— 运行统计按工作流聚合属 v2（规划 §8.3 报表升级）" />
      <Table<StatsRow> columns={statsColumns} dataSource={statsRows} pagination={false} size="small" />
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
          { key: "sync", label: "同步状态", children: <SyncMatrix /> },
          { key: "stats", label: "运行统计", children: <RunStats /> },
        ]}
      />
    </Card>
  );
}
