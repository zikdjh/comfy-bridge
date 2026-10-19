import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Drawer,
  Select,
  Space,
  Steps,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { RedoOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { BackendTag, type Backend } from "../../components/common/BackendTag";
import { useRequests, type BridgeRequest, type GenType, type RequestStatus } from "../../api/requests";

export const statusMeta: Record<RequestStatus, { color: string; text: string }> = {
  queued: { color: "default", text: "排队中" },
  running: { color: "blue", text: "执行中" },
  done: { color: "green", text: "完成" },
  error: { color: "red", text: "失败" },
  cancelled: { color: "orange", text: "已中断" },
  blocked: { color: "gold", text: "待云端通道" },
  preflight: { color: "volcano", text: "预校验拦截" },
};

const genTypeMeta: Record<GenType, { color: string; text: string }> = {
  image: { color: "purple", text: "生图" },
  video: { color: "magenta", text: "视频" },
  none: { color: "default", text: "非生成" },
};

function formatDuration(ms?: number): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

const columns: ColumnsType<BridgeRequest> = [
  { title: "时间", dataIndex: "created_at", width: 150, render: (v: string) => v?.slice(5, 19) },
  {
    title: "工作流",
    dataIndex: "workflow_name",
    render: (v: string | null, row) => v ?? <Typography.Text type="secondary">未命名 #{row.id}</Typography.Text>,
  },
  {
    title: "类型",
    dataIndex: "gen_type",
    width: 80,
    render: (t: GenType) => <Tag color={genTypeMeta[t].color}>{genTypeMeta[t].text}</Tag>,
  },
  {
    title: "后端",
    dataIndex: "backend",
    width: 110,
    render: (b: Backend, row) => (
      <Space size={4}>
        <BackendTag backend={b} />
        {row.final_backend && row.final_backend !== b && <Tag color="orange">回退本地</Tag>}
      </Space>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 90,
    render: (s: RequestStatus) => <Tag color={statusMeta[s].color}>{statusMeta[s].text}</Tag>,
  },
  { title: "耗时", key: "duration", width: 80, render: (_, row) => formatDuration(row.duration_ms) },
  {
    title: "成本",
    key: "est_cost",
    width: 90,
    render: (_, row) =>
      row.est_cost_usd != null ? (
        <Tooltip title={`云端耗时 ${row.gpu_seconds?.toFixed(0) ?? "?"}s（估算口径见仪表盘额度卡片）`}>
          <Typography.Text style={{ fontVariantNumeric: "tabular-nums" }}>
            ${row.est_cost_usd.toFixed(4)}
          </Typography.Text>
        </Tooltip>
      ) : (
        <Typography.Text type="secondary">—</Typography.Text>
      ),
  },
  {
    title: "prompt_id",
    dataIndex: "prompt_id",
    width: 110,
    render: (v: string) => <Typography.Text code copyable={{ text: v, tooltips: ["复制 prompt_id", "已复制"] }}>{v?.slice(0, 8)}</Typography.Text>,
  },
];

/** 请求日志：桥接器拦截 POST /prompt 后落库的每次请求（GET /api/v1/requests）。 */
export function RequestLogPanel() {
  const [backendFilter, setBackendFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [realtime, setRealtime] = useState(true);
  const [detail, setDetail] = useState<BridgeRequest | null>(null);

  const { data, isLoading, isFetching, refetch, error } = useRequests(backendFilter, statusFilter, realtime);
  const items = data?.items ?? [];
  const loadError = error instanceof Error ? error.message : error ? String(error) : null;

  return (
    <Card
      size="small"
      title={`请求日志（拦截层共 ${items.length} 条）`}
      extra={
        <Space wrap style={{ justifyContent: "flex-end", maxWidth: "100%" }}>
          <Select
            size="small"
            style={{ width: 110 }}
            value={backendFilter}
            onChange={setBackendFilter}
            options={[
              { value: "all", label: "全部后端" },
              { value: "local", label: "本地" },
              { value: "modal", label: "云端" },
            ]}
          />
          <Select
            size="small"
            style={{ width: 110 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: "all", label: "全部状态" },
              { value: "queued", label: "排队中" },
              { value: "running", label: "执行中" },
              { value: "done", label: "完成" },
              { value: "error", label: "失败" },
              { value: "cancelled", label: "已中断" },
              { value: "blocked", label: "待云端通道" },
              { value: "preflight", label: "预校验拦截" },
            ]}
          />
          <Space size={4}>
            <span style={{ fontSize: 12, color: "#5a6272" }}>实时</span>
            <Switch size="small" checked={realtime} onChange={setRealtime} />
          </Space>
        </Space>
      }
    >
      {loadError && (
        <Alert
          type="error"
          showIcon
          message="请求日志读取失败"
          description={loadError}
          action={<Button size="small" onClick={() => void refetch()}>重试</Button>}
          style={{ marginBottom: 12 }}
        />
      )}
      <Table<BridgeRequest>
        scroll={{ x: 820 }}
        columns={columns}
        dataSource={items}
        rowKey="id"
        loading={isLoading}
        rowClassName={() => "clickable-row"}
        onRow={(record) => ({ onClick: () => setDetail(record), style: { cursor: "pointer" } })}
        pagination={{ pageSize: 50, showSizeChanger: false }}
        size="small"
      />

      <Drawer
        title={detail ? `请求详情 #${detail.id}` : ""}
        width="min(60%, 720px)"
        open={!!detail}
        onClose={() => setDetail(null)}
        extra={
          <Button size="small" icon={<RedoOutlined />} onClick={() => refetch()}>
            刷新
          </Button>
        }
      >
        {detail && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Descriptions column={2} size="small">
              <Descriptions.Item label="状态">
                <Tag color={statusMeta[detail.status].color}>{statusMeta[detail.status].text}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="类型">
                <Tag color={genTypeMeta[detail.gen_type].color}>{genTypeMeta[detail.gen_type].text}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="决策后端">
                <BackendTag backend={detail.backend} />
              </Descriptions.Item>
              <Descriptions.Item label="实际执行">
                {detail.final_backend ? <BackendTag backend={detail.final_backend as Backend} /> : "已拒绝"}
              </Descriptions.Item>
              <Descriptions.Item label="prompt_id">
                <Typography.Text copyable>{detail.prompt_id}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="时间">{detail.created_at}</Descriptions.Item>
              <Descriptions.Item label="耗时">{formatDuration(detail.duration_ms)}</Descriptions.Item>
              <Descriptions.Item label="图快照 hash">{detail.graph_hash ?? "—"}</Descriptions.Item>
            </Descriptions>

            <div>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>决策链路（首条命中生效）</div>
              <Steps
                size="small"
                direction="vertical"
                current={(detail.rule_trace?.length ?? 1) - 1}
                items={(detail.rule_trace ?? []).map((s) => ({
                  title: s.rule + (s.target ? ` → ${s.target}` : ""),
                  description: s.detail,
                  status: s.matched ? "finish" : "wait",
                }))}
              />
            </div>

            {detail.error && (
              <Collapse
                defaultActiveKey={["err"]}
                items={[
                  {
                    key: "err",
                    label: "错误信息",
                    children: (
                      <pre style={{ fontSize: 12, margin: 0, whiteSpace: "pre-wrap" }}>{detail.error}</pre>
                    ),
                  },
                ]}
              />
            )}

            <Collapse
              items={[
                {
                  key: "timeline",
                  label: "时间线与输出（M1 接入 Modal 执行通道后记录）",
                  children: (
                    <Typography.Text type="secondary">
                      本地执行的节点级耗时与输出清单将随执行通道完善；图快照可用
                      /api/v1/requests/{detail.id}/graph 查看。
                    </Typography.Text>
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Drawer>
    </Card>
  );
}
