import { useMemo, useState } from "react";
import { Alert, Button, Card, Modal, Segmented, Space, Spin, Table, Tabs, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyWorkflowSync,
  fetchWorkflowCompare,
  useLocalWorkflows,
  useWorkflowRunStats,
  useWorkflowSync,
  type LocalWorkflow,
  type WfCompare,
  type WfCompareSide,
  type WfRunStat,
  type WfSyncAction,
  type WfSyncRow,
} from "../../api/workflows";
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

/* ---------------- 冲突双列对比（M2 收尾）：本地版 vs 云端版元数据 + 内容摘要 ---------------- */

function SideCard({ title, side, onlyTypes }: { title: string; side: WfCompareSide; onlyTypes: Set<string> }) {
  return (
    <Card
      size="small"
      title={title}
      style={{ flex: 1, minWidth: 320 }}
      styles={{ body: { paddingInline: 16 } }}
    >
      {!side.exists ? (
        <Typography.Text type="secondary">{side.error ?? "该端不存在此文件"}</Typography.Text>
      ) : (
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            大小 {side.size_bytes != null ? fmtSize(side.size_bytes) : "—"}
            {" · hash "}
            <Typography.Text code>{side.hash ?? "—"}</Typography.Text>
            {side.mtime ? ` · 修改 ${dayjs(side.mtime).format("MM-DD HH:mm")}` : ""}
          </Typography.Text>
          {side.summary_error ? (
            <Typography.Text type="warning" style={{ fontSize: 12 }}>{side.summary_error}</Typography.Text>
          ) : (
            <>
              <div>
                <span style={{ fontSize: 12, color: "#8a90a2", marginRight: 8 }}>节点 {side.node_count ?? 0} 个：</span>
                {(side.types ?? []).slice(0, 10).map(([t, c]) => (
                  <Tag
                    key={t}
                    color={onlyTypes.has(t) ? "orange" : undefined}
                    style={{ marginInlineEnd: 4, fontSize: 12 }}
                  >
                    {t}{c > 1 ? ` ×${c}` : ""}
                  </Tag>
                ))}
                {(side.types?.length ?? 0) > 10 && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    等 {side.types!.length} 类
                  </Typography.Text>
                )}
              </div>
              {(side.models?.length ?? 0) > 0 && (
                <div>
                  <span style={{ fontSize: 12, color: "#8a90a2", marginRight: 8 }}>模型引用：</span>
                  {side.models!.slice(0, 8).map((m) => (
                    <Tag key={m} style={{ marginInlineEnd: 4, fontSize: 12 }}>{m}</Tag>
                  ))}
                  {side.models!.length > 8 && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>等 {side.models!.length} 个</Typography.Text>
                  )}
                </div>
              )}
            </>
          )}
        </Space>
      )}
    </Card>
  );
}

function CompareModal({ name, onClose }: { name: string | null; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["workflows", "compare", name],
    queryFn: ({ signal }) => fetchWorkflowCompare(name!, signal),
    enabled: !!name,
  });

  const onlySets = useMemo(() => {
    const lt = new Set((data?.local.types ?? []).map(([t]) => t));
    const ct = new Set((data?.cloud.types ?? []).map(([t]) => t));
    const onlyLocal = new Set([...lt].filter((t) => !ct.has(t)));
    const onlyCloud = new Set([...ct].filter((t) => !lt.has(t)));
    return { onlyLocal, onlyCloud };
  }, [data]);

  return (
    <Modal
      title={`冲突对比：${name ?? ""}`}
      open={!!name}
      onCancel={onClose}
      footer={null}
      width={960}
    >
      {isLoading || !data ? (
        <Spin style={{ display: "block", margin: "40px auto" }} />
      ) : (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          {data.identical && (
            <Alert type="success" showIcon message="两端内容一致（hash 相同）——可直接选「推本地版」或「推云端版」任一收口" />
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            橙色标签 = 仅该端有的节点类型；「保留双副本」会把云端版留为 ×.conflict-时间.json 后推本地版上云。
          </Typography.Text>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <SideCard title="本地版" side={data.local} onlyTypes={onlySets.onlyLocal} />
            <SideCard title="云端版（数据卷）" side={data.cloud} onlyTypes={onlySets.onlyCloud} />
          </div>
        </Space>
      )}
    </Modal>
  );
}

function SyncMatrix() {
  const queryClient = useQueryClient();
  const [operating, setOperating] = useState(false);
  const [compareName, setCompareName] = useState<string | null>(null);
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
              <Button size="small" type="link" disabled={operating} onClick={() => setCompareName(r.name)}>
                对比
              </Button>
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
      <CompareModal name={compareName} onClose={() => setCompareName(null)} />
    </Space>
  );
}

function fmtSec2(ms: number): string {
  if (ms >= 60_000) return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function RunStats() {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useWorkflowRunStats(days);
  const rows = data?.rows ?? [];
  const columns: ColumnsType<WfRunStat> = [
    { title: "工作流", dataIndex: "name" },
    {
      title: "运行次数",
      dataIndex: "runs",
      width: 90,
      render: (v: number) => <b style={{ fontVariantNumeric: "tabular-nums" }}>{v}</b>,
    },
    {
      title: "成功 / 失败",
      key: "ok",
      width: 100,
      render: (_, r) => (
        <Space size={4}>
          <span style={{ color: "#43a047" }}>{r.done}</span>
          {r.errored > 0 && <span style={{ color: "#cf1322" }}>/ {r.errored}</span>}
        </Space>
      ),
    },
    {
      title: "本地 / 云端",
      key: "side",
      width: 110,
      render: (_, r) => (
        <span style={{ fontVariantNumeric: "tabular-nums" }}>
          {r.local_runs} / {r.modal_runs}
        </span>
      ),
    },
    { title: "平均耗时", key: "avg", width: 90, render: (_, r) => (r.avg_ms != null ? fmtSec2(r.avg_ms) : "—") },
    { title: "最大耗时", key: "max", width: 90, render: (_, r) => (r.max_ms != null ? fmtSec2(r.max_ms) : "—") },
    {
      title: "估算成本",
      key: "cost",
      width: 100,
      render: (_, r) => (r.est_cost_usd > 0 ? `$${r.est_cost_usd.toFixed(4)}` : "—"),
    },
    {
      title: "最近运行",
      key: "last",
      width: 120,
      render: (_, r) => dayjs(r.last_ms).format("MM-DD HH:mm"),
    },
  ];
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space style={{ justifyContent: "space-between", width: "100%" }} align="center">
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {data?.note ?? "统计自桥接请求日志实时聚合"}
        </Typography.Text>
        <Segmented
          options={[
            { label: "近 7 天", value: 7 },
            { label: "近 30 天", value: 30 },
            { label: "近 90 天", value: 90 },
          ]}
          value={days}
          onChange={(v) => setDays(v as number)}
        />
      </Space>
      <Table<WfRunStat>
        columns={columns}
        dataSource={rows}
        rowKey="name"
        loading={isLoading}
        pagination={false}
        size="small"
        locale={{ emptyText: `近 ${days} 天没有记录到工作流运行 —— 经桥接提交一次生成后这里会出现统计` }}
      />
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
