import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card, Col, Descriptions, Form, InputNumber, Modal, Popconfirm, Progress, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography, message } from "antd";
import { CheckCircleOutlined, ClockCircleOutlined, DollarOutlined, FireOutlined, InfoCircleOutlined, ThunderboltOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { Link, useNavigate } from "react-router-dom";
import dayjs from "dayjs";
import { BackendTag, type Backend } from "../../components/common/BackendTag";
import type { TrendPoint } from "../../components/common/TrendChart";
import type { CostPoint } from "../../components/common/CostChart";
import { useHealth, warmupModal } from "../../api/health";
import { useModalContainers } from "../../api/launch";
import { useRequests, type BridgeRequest, type RequestStatus } from "../../api/requests";
import { useCostSummary, useUpdateBudget, type ModalBilling } from "../../api/cost";
import { statusMeta } from "../logs/RequestLogPanel";

// 图表懒加载：echarts 体量大且只在数据就绪后渲染，骨架先出、图表随后补上
const TrendChart = lazy(() => import("../../components/common/TrendChart").then((m) => ({ default: m.TrendChart })));
const CostChart = lazy(() => import("../../components/common/CostChart").then((m) => ({ default: m.CostChart })));

function backendOf(r: BridgeRequest): Backend {
  return (r.final_backend || r.backend) as Backend;
}

function fmtSec(ms: number): string {
  if (ms >= 60_000) return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Modal 账单分项提示（悬停「Modal 账单」标签）。 */
function modalBreakdownTip(m: ModalBilling): string {
  const items = Object.entries(m.breakdown ?? {}).map(([k, v]) => `${k} $${v.toFixed(2)}`);
  const window = m.period_start ? `${m.period_start} 起的 UTC 计费月` : "UTC 计费月";
  return `Modal 账户 ${window}（120s 缓存）${items.length ? "：" + items.join("；") : ""}`;
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

  // —— 额度与成本（M4-1）：近 14 天逐日估算成本（无用量日补零）+ 预算护栏 ——
  const { data: cost } = useCostSummary();
  const updateBudget = useUpdateBudget();
  const [budgetOpen, setBudgetOpen] = useState(false);
  const [budgetForm] = Form.useForm();
  const costTrend: CostPoint[] = useMemo(() => {
    const byDate = new Map((cost?.daily ?? []).map((d) => [d.date, d]));
    return Array.from({ length: 14 }, (_, i) => {
      const day = dayjs().subtract(13 - i, "day");
      const key = day.format("YYYY-MM-DD");
      const hit = byDate.get(key);
      return { date: day.format("MM-DD"), est_cost_usd: hit?.est_cost_usd ?? 0, requests: hit?.requests ?? 0 };
    });
  }, [cost]);
  const budget = cost?.budget;
  const budgetPercent =
    budget && budget.enabled && budget.monthly_usd > 0
      ? Math.min(Math.round((budget.used_usd / budget.monthly_usd) * 100), 100)
      : 0;

  const saveBudget = async (values: {
    monthlyUsd: number | null;
    onExceed: string;
    rateUsd: number | null;
  }) => {
    try {
      await updateBudget({
        monthly_budget_usd: values.monthlyUsd ?? 0,
        on_budget_exceed: values.onExceed as "fallback_local" | "reject",
        gpu_hourly_rate_usd: values.rateUsd ?? undefined,
      });
      message.success("预算护栏已保存并热生效");
      setBudgetOpen(false);
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const running = items.filter((r) => r.status === "queued" || r.status === "running");

  const local = health?.local;
  const modal = health?.modal;
  // 容器数面板（M4-2）：后端 45s 缓存 + 前端 60s 轮询，跟随 Modal 通道卡片展示
  const { data: containers } = useModalContainers();
  const containersLine =
    containers?.ok && containers.count != null ? ` · 容器 ${containers.count} 个运行中` : "";

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
      {budget?.enabled && budget.exceeded && (
        <Alert
          type="warning"
          showIcon
          message={
            budget.on_exceed === "reject"
              ? `本月云端估算 $${budget.used_usd.toFixed(2)} 已达预算上限 $${budget.monthly_usd.toFixed(2)}，新请求将按「超预算动作 = 拒绝执行」被拦截`
              : `本月云端估算 $${budget.used_usd.toFixed(2)} 已达预算上限 $${budget.monthly_usd.toFixed(2)}，新云端请求将自动回落本地执行`
          }
          description="这是 ComfyBridge 本地护栏，只约束经桥接器的请求，不是 Modal 账户级硬上限；账户级预算与消费告警请在 Modal 平台设置。"
          action={
            <Button size="small" danger onClick={() => {
              budgetForm.setFieldsValue({
                monthlyUsd: budget.monthly_usd,
                onExceed: budget.on_exceed,
                rateUsd: cost?.gpu_hourly_rate_usd,
              });
              setBudgetOpen(true);
            }}>
              调整预算
            </Button>
          }
        />
      )}

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
              {containersLine}
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
            <Suspense fallback={null}><TrendChart data={trend} /></Suspense>
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

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={16}>
          <Card
            size="small"
            title={
              <Space size={6}>
                <span>额度与成本（云端估算，近 14 天）</span>
                <Tooltip title={cost?.note}>
                  <InfoCircleOutlined style={{ color: "#8a90a2" }} />
                </Tooltip>
              </Space>
            }
            extra={
              <Link to="/cost" style={{ fontSize: 12 }}>
                成本报表
              </Link>
            }
          >
            <Suspense fallback={null}><CostChart data={costTrend} /></Suspense>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card
            size="small"
            title={
              <span>
                <DollarOutlined style={{ color: "#8a90a2", marginRight: 6 }} />
                本月额度
              </span>
            }
            extra={
              <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => {
                budgetForm.setFieldsValue({
                  monthlyUsd: budget?.monthly_usd ?? 0,
                  onExceed: budget?.on_exceed ?? "fallback_local",
                  rateUsd: cost?.gpu_hourly_rate_usd ?? 1.95,
                });
                setBudgetOpen(true);
              }}>
                设置
              </Button>
            }
          >
            <Statistic
              title="本月云端估算花费"
              valueRender={() => (
                <span style={{ fontSize: 24, fontVariantNumeric: "tabular-nums" }}>
                  ${(cost?.month.est_cost_usd ?? 0).toFixed(4)}
                  <span style={{ fontSize: 13, color: "#8a90a2", marginLeft: 8 }}>
                    今日 ${(cost?.today.est_cost_usd ?? 0).toFixed(4)}
                  </span>
                </span>
              )}
            />
            <div style={{ color: "#8a90a2", fontSize: 12, margin: "4px 0 10px" }}>
              本月云端 {(cost?.month.requests ?? 0)} 次 · GPU {(cost?.month.gpu_seconds ?? 0).toFixed(0)}s
              {" · "}{cost?.gpu_type ?? "L40S"} 单价 ${cost?.gpu_hourly_rate_usd?.toFixed(2) ?? "1.95"}/h
            </div>
            {cost?.modal && (cost.modal.ok ? (
              <div style={{ borderTop: "1px dashed #e4e7ef", margin: "0 0 10px", paddingTop: 8, fontSize: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <Tooltip title={modalBreakdownTip(cost.modal)}>
                    <span style={{ color: "#8a90a2", borderBottom: "1px dotted #b9bfcf", cursor: "help" }}>
                      Modal 账单（{cost.modal.period_start} 起，UTC）
                    </span>
                  </Tooltip>
                  <b style={{ fontVariantNumeric: "tabular-nums", fontSize: 14 }}>
                    ${(cost.modal.metered_cost_usd ?? 0).toFixed(2)}
                  </b>
                </div>
                <div style={{ color: "#8a90a2", marginTop: 2 }}>
                  积分抵扣 ${(cost.modal.credits_applied_usd ?? 0).toFixed(2)}
                  {" · "}实际计费 ${(cost.modal.billed_cost_usd ?? 0).toFixed(2)}
                  {(cost.modal.billed_cost_usd ?? 0) > 0 && (
                    <span style={{ color: "#cf1322" }}> · 赠金/额度已耗尽</span>
                  )}
                </div>
              </div>
            ) : (
              <div style={{ color: "#8a90a2", fontSize: 12, margin: "0 0 10px" }}>
                Modal 账单暂不可获取：{cost.modal.error}
              </div>
            ))}
            {budget?.enabled ? (
              <>
                <Progress
                  percent={budgetPercent}
                  status={budget.exceeded ? "exception" : "normal"}
                  format={() => `$${budget.used_usd.toFixed(2)} / $${budget.monthly_usd.toFixed(2)}`}
                />
                <div style={{ color: "#8a90a2", fontSize: 12 }}>
                  {budget.exceeded
                    ? "已超限：新云端请求将回落本地或拒绝（见顶部提示）"
                    : `剩余 $${budget.remaining_usd?.toFixed(2)} · 超限动作：${budget.on_exceed === "reject" ? "拒绝执行" : "回落本地"}`}
                </div>
              </>
            ) : (
              <div style={{ color: "#8a90a2", fontSize: 12 }}>
                未启用预算护栏 —— 点「设置」填写月度上限后，超限请求将自动回落本地或拒绝。
              </div>
            )}
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

      <Modal
        title="预算护栏设置"
        open={budgetOpen}
        onCancel={() => setBudgetOpen(false)}
        onOk={() => budgetForm.submit()}
        okText="保存"
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          估算 = GPU 单价 × 请求墙钟秒数（含冷启动等待）。月度上限设为 0 = 关闭护栏；
          启用后本月云端估算花费达到上限时，路由到云端的请求将按「超限动作」回落本地或拒绝。
        </Typography.Paragraph>
        <Form form={budgetForm} layout="vertical" onFinish={saveBudget}>
          <Form.Item
            label="月度预算上限（USD）"
            name="monthlyUsd"
            rules={[{ required: true, message: "请填写月度预算（0 = 关闭护栏）" }]}
            extra="Modal Starter 每月赠 $30 免费额度可作参考基线"
          >
            <InputNumber min={0} step={5} prefix="$" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item label="超预算动作" name="onExceed" extra="回落本地 = 请求继续在本地执行；拒绝 = 直接拦截">
            <Select
              options={[
                { value: "fallback_local", label: "回落本地执行（fallback_local）" },
                { value: "reject", label: "拒绝请求（reject）" },
              ]}
            />
          </Form.Item>
          <Form.Item
            label="GPU 单价（USD/容器·小时）"
            name="rateUsd"
            rules={[{ required: true, message: "请填写单价" }]}
            extra="随 Modal 价格调整；仅用于估算，不参与计费"
          >
            <InputNumber min={0.01} step={0.05} prefix="$" style={{ width: 180 }} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
