import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Col, Descriptions, Popconfirm, Row, Space, Statistic, Table, Tag, Tooltip, Typography, message } from "antd";
import { CheckCircleOutlined, ClockCircleOutlined, FireOutlined, ThunderboltOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import dayjs from "dayjs";
import { BackendTag, type Backend } from "../../components/common/BackendTag";
import { TrendChart, type TrendPoint } from "../../components/common/TrendChart";
import { useHealth, warmupModal } from "../../api/health";
import { useRequests, type BridgeRequest, type RequestStatus } from "../../api/requests";
import { statusMeta } from "../logs/RequestLogPanel";

function backendOf(r: BridgeRequest): Backend {
  return (r.final_backend || r.backend) as Backend;
}

function fmtSec(ms: number): string {
  if (ms >= 60_000) return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { data: health, refetch: refetchHealth } = useHealth();
  // 拉满后端单页上限 500 条，前端表格内分页浏览
  const { data, isLoading, refetch } = useRequests("all", "all", true, 500);
  const items = useMemo(() => data?.items ?? [], [data]);

  // 运行中任务的“已耗时”需要秒级跳动；没有执行中任务时不空转定时器
  const [now, setNow] = useState(Date.now());
  const runningCount = items.filter((r) => r.status === "queued" || r.status === "running").length;
  useEffect(() => {
    if (runningCount === 0) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [runningCount > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  const [warming, setWarming] = useState(false);
  const warmup = async () => {
    setWarming(true);
    try {
      const r = await warmupModal();
      if (r.ok) {
        message.success(`云端容器已就绪（耗时 ${r.seconds}s）`);
        refetchHealth();
      } else {
        message.error(`预热失败：${r.error ?? "未知错误"}`);
      }
    } finally {
      setWarming(false);
    }
  };

  const [interrupting, setInterrupting] = useState(false);
  const interrupt = async () => {
    // /api/interrupt 会同时中断本地队列与云端执行器任务；响应是透传流，不解析只消费
    setInterrupting(true);
    try {
      const res = await fetch("/api/interrupt", { method: "POST" });
      if (!res.ok) {
        message.error(`中断失败：${res.status} ${res.statusText}`);
        return;
      }
      await res.text().catch(() => "");
      message.success("已发送中断指令");
      refetch();
    } catch (err) {
      message.error(`中断失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setInterrupting(false);
    }
  };

  // —— 今日统计（本地时区零点起）——
  const startOfToday = dayjs().startOf("day").valueOf();
  const today = items.filter((r) => r.created_ms >= startOfToday);
  const todayLocal = today.filter((r) => backendOf(r) === "local").length;
  const todayModal = today.length - todayLocal;
  const todayVideo = today.filter((r) => r.gen_type === "video").length;
  const done = today.filter((r) => r.status === "done").length;
  const failed = today.filter((r) => r.status === "error").length;
  const cancelled = today.filter((r) => r.status === "cancelled").length;
  const attempts = done + failed + cancelled;
  const successRate = attempts > 0 ? Math.round((done / attempts) * 100) : null;
  const finishedToday = today.filter((r) => r.duration_ms != null);
  const avgBy = (b: Backend) => {
    const list = finishedToday.filter((r) => backendOf(r) === b);
    return list.length ? list.reduce((s, r) => s + (r.duration_ms ?? 0), 0) / list.length : null;
  };
  const avgLocal = avgBy("local");
  const avgModal = avgBy("modal");
  const avgAll = finishedToday.length
    ? finishedToday.reduce((s, r) => s + (r.duration_ms ?? 0), 0) / finishedToday.length
    : null;

  // —— 近 14 天趋势（按执行后端分桶）——
  const trend: TrendPoint[] = useMemo(() => {
    const buckets = new Map<string, TrendPoint>();
    for (let i = 13; i >= 0; i--) {
      const d = dayjs().subtract(i, "day").format("MM-DD");
      buckets.set(d, { date: d, local: 0, modal: 0 });
    }
    for (const r of items) {
      const key = dayjs(r.created_ms).format("MM-DD");
      const bucket = buckets.get(key);
      if (bucket) bucket[backendOf(r)] += 1;
    }
    return [...buckets.values()];
  }, [items]);

  const running = items.filter((r) => r.status === "queued" || r.status === "running");

  const local = health?.local;
  const modal = health?.modal;

  const runningColumns: ColumnsType<BridgeRequest> = [
    { title: "时间", dataIndex: "created_at", width: 120, render: (v: string) => v?.slice(11, 19) },
    {
      title: "工作流",
      dataIndex: "workflow_name",
      render: (v: string | null, row) => v ?? <Typography.Text type="secondary">未命名 #{row.id}</Typography.Text>,
    },
    { title: "后端", key: "backend", width: 110, render: (_, r) => <BackendTag backend={backendOf(r)} /> },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (s: RequestStatus) => <Tag color={statusMeta[s].color}>{statusMeta[s].text}</Tag>,
    },
    {
      title: "已耗时",
      key: "elapsed",
      width: 90,
      render: (_, r) => {
        const start = r.started_ms ?? r.created_ms;
        return start ? fmtSec(Math.max(now - start, 0)) : "—";
      },
    },
    {
      title: "prompt_id",
      dataIndex: "prompt_id",
      width: 110,
      render: (v: string) => (
        <Typography.Text code copyable={{ text: v, tooltips: ["复制 prompt_id", "已复制"] }}>
          {v?.slice(0, 8)}
        </Typography.Text>
      ),
    },
    {
      title: "操作",
      width: 90,
      render: () => (
        <Popconfirm
          title="中断当前任务？"
          description="本地队列与云端执行任务会一并尝试中断。"
          onConfirm={() => void interrupt()}
        >
          <Button size="small" danger loading={interrupting} onClick={(e) => e.stopPropagation()}>
            中断
          </Button>
        </Popconfirm>
      ),
    },
  ];

  const recentColumns: ColumnsType<BridgeRequest> = [
    { title: "时间", dataIndex: "created_at", width: 130, render: (v: string) => v?.slice(5, 16) },
    {
      title: "工作流",
      dataIndex: "workflow_name",
      render: (v: string | null, row) => v ?? <Typography.Text type="secondary">未命名 #{row.id}</Typography.Text>,
    },
    { title: "后端", key: "backend", width: 100, render: (_, r) => <BackendTag backend={backendOf(r)} /> },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (s: RequestStatus) => <Tag color={statusMeta[s].color}>{statusMeta[s].text}</Tag>,
    },
    { title: "耗时", key: "duration", width: 80, render: (_, r) => (r.duration_ms != null ? fmtSec(r.duration_ms) : "—") },
  ];

  const modalStateText = modal?.app_state === "deployed" ? "已部署" : modal?.app_state === "stopped" ? "已停止（按需冷启动）" : (modal?.app_state ?? "—");

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} xl={6}>
          <Card size="small">
            <Statistic
              title={<span><ThunderboltOutlined style={{ color: "#8a90a2" }} /> 今日生成</span>}
              value={today.length}
            />
            <div style={{ color: "#8a90a2", fontSize: 12 }}>
              本地 {todayLocal} · 云端 {todayModal}
              {todayVideo > 0 && ` · 视频 ${todayVideo}`}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <Card size="small">
            <Statistic
              title={<span><CheckCircleOutlined style={{ color: "#8a90a2" }} /> 今日成功率</span>}
              valueRender={() => (
                <span style={{ fontSize: 24, fontVariantNumeric: "tabular-nums" }}>{successRate == null ? "—" : `${successRate}%`}</span>
              )}
            />
            <div style={{ color: "#8a90a2", fontSize: 12 }}>
              {attempts > 0 ? `完成 ${done} · 失败 ${failed} · 中断 ${cancelled}` : "今日暂无完成的生成请求"}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <Card size="small">
            <Statistic
              title={<span><ClockCircleOutlined style={{ color: "#8a90a2" }} /> 今日平均耗时</span>}
              valueRender={() => (
                <span style={{ fontSize: 24, fontVariantNumeric: "tabular-nums" }}>{avgAll == null ? "—" : `${(avgAll / 1000).toFixed(1)}s`}</span>
              )}
            />
            <div style={{ color: "#8a90a2", fontSize: 12 }}>
              本地 {avgLocal == null ? "—" : fmtSec(avgLocal)} · 云端 {avgModal == null ? "—" : fmtSec(avgModal)}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <Card size="small">
            <Statistic
              title="Modal 执行通道"
              valueRender={() => (
                <Tooltip title={modal?.reason ?? "检测中…"}>
                  <Space size={6}>
                    <Badge status={health == null ? "default" : modal?.online ? "success" : "error"} />
                    <span style={{ fontSize: 24, fontWeight: 600 }}>{health == null ? "…" : modal?.online ? "就绪" : "未就绪"}</span>
                  </Space>
                </Tooltip>
              )}
            />
            <div style={{ color: "#8a90a2", fontSize: 12, marginBottom: 8 }}>
              {modalStateText}
              {modal?.cloud_index_files != null && ` · 云端模型 ${modal.cloud_index_files} 项`}
            </div>
            <Popconfirm
              title="预热云端容器？"
              description="唤醒 L40S 容器（空转约 $1.95/小时，闲置自动缩容到零）。"
              onConfirm={warmup}
            >
              <Button size="small" icon={<FireOutlined />} loading={warming}>
                预热
              </Button>
            </Popconfirm>
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={16}>
          <Card size="small" title="生成趋势（近 14 天）">
            <TrendChart data={trend} />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card size="small" title="运行环境">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="本地 ComfyUI">
                {local?.online ? (
                  <Space size={6} wrap>
                    <Tag color="green">{local.version ?? "未知版本"}</Tag>
                    <span>{local.gpu ?? "未知 GPU"}</span>
                    <span style={{ color: "#8a90a2" }}>{local.latency_ms ?? "?"}ms</span>
                  </Space>
                ) : (
                  <Tooltip title={local?.error ?? "本地服务未启动"}>
                    <Tag color="red">未连接</Tag>
                  </Tooltip>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Modal Runner">
                <Tooltip title={modal?.reason}>
                  <Tag color={modal?.online ? "geekblue" : "default"}>{modal?.online ? "通道就绪" : "通道未就绪"}</Tag>
                </Tooltip>
                {modal?.web_url && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                    {modal.web_url}
                  </Typography.Text>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="桥接地址">
                <Typography.Text copyable style={{ fontSize: 12 }}>
                  {health?.bridge?.url ?? "—"}
                </Typography.Text>
              </Descriptions.Item>
            </Descriptions>
            <div style={{ color: "#8a90a2", fontSize: 12, marginTop: 8 }}>
              经桥接 {health?.bridge?.url ?? "—"} 出图的请求都会被拦截路由：云端独占模型走 Modal L40S，其余留在本地。
            </div>
          </Card>
        </Col>
      </Row>

      <Card size="small" title={`运行中任务${runningCount > 0 ? `（${runningCount}）` : ""}`}>
        <Table<BridgeRequest>
          columns={runningColumns}
          dataSource={running}
          rowKey="id"
          loading={isLoading}
          pagination={{ pageSize: 5, hideOnSinglePage: true, showSizeChanger: false, size: "small" }}
          size="small"
          locale={{ emptyText: "当前没有排队或执行中的任务 —— 在出图界面提交一次生成即可实时看到进度" }}
        />
      </Card>

      <Card
        size="small"
        title="最近请求"
        extra={
          <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => navigate("/logs")}>
            全部日志 →
          </Button>
        }
      >
        <Table<BridgeRequest>
          columns={recentColumns}
          dataSource={items}
          rowKey="id"
          loading={isLoading}
          pagination={{ pageSize: 10, showSizeChanger: false, size: "small", showTotal: (total) => `共 ${total} 条` }}
          size="small"
          rowClassName={() => "clickable-row"}
          onRow={(record) => ({ onClick: () => navigate("/logs"), style: { cursor: "pointer" } })}
        />
      </Card>
    </Space>
  );
}
