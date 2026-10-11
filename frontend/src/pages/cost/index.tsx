/** 成本报表页（报表 v2 · 规划 §8.3）：工作流/主模型维度聚合 + 逐项账单对账 + CSV 导出。 */
import { useState } from "react";
import {
  Alert,
  Card,
  DatePicker,
  Dropdown,
  Progress,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { DownloadOutlined, InfoCircleOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import {
  costExportUrl,
  useCostReconciliation,
  useCostReport,
  type CostReportRow,
  type ReconciliationDailyPoint,
} from "../../api/cost";

const DAYS = 30;

const usd = (v: number | null | undefined, digits = 4) => (v == null ? "—" : `$${v.toFixed(digits)}`);

function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.click();
}

function ReportCard() {
  const [by, setBy] = useState<"workflow" | "model">("workflow");
  const { data: report, isLoading } = useCostReport(by, DAYS);
  const dimLabel = by === "workflow" ? "工作流" : "主模型";

  const columns: ColumnsType<CostReportRow> = [
    { title: "#", width: 48, render: (_, __, i) => <span style={{ color: "#8a90a2" }}>{i + 1}</span> },
    {
      title: dimLabel,
      dataIndex: "key",
      ellipsis: { showTitle: false },
      render: (v: string) => (
        <Tooltip title={v} placement="topLeft">
          <Typography.Text style={{ maxWidth: 360 }}>{v}</Typography.Text>
        </Tooltip>
      ),
    },
    { title: "请求数", dataIndex: "requests", width: 90, align: "right" },
    { title: "GPU 秒", dataIndex: "gpu_seconds", width: 100, align: "right", render: (v: number) => v.toFixed(1) },
    { title: "估算成本", dataIndex: "est_cost_usd", width: 110, align: "right", render: (v: number) => usd(v) },
    {
      title: "占比",
      dataIndex: "share",
      width: 150,
      render: (v: number) => (
        <Space size={8}>
          <Progress
            percent={Math.round(v * 1000) / 10}
            size="small"
            showInfo={false}
            style={{ width: 72, marginBottom: 0 }}
          />
          <span style={{ fontSize: 12, color: "#5b6172" }}>{(v * 100).toFixed(1)}%</span>
        </Space>
      ),
    },
  ];

  return (
    <Card
      size="small"
      title={
        <Space size={6}>
          <span>成本聚合（云端已入账请求，近 {DAYS} 天）</span>
          <Tooltip title="按 workflow_name / 主模型（checkpoint/扩散模型权重）分组汇总估算成本；仅统计 final_backend=modal 且已入账的请求。">
            <InfoCircleOutlined style={{ color: "#8a90a2" }} />
          </Tooltip>
        </Space>
      }
      extra={
        <Space size={8}>
          <Segmented
            size="small"
            value={by}
            onChange={(v) => setBy(v as "workflow" | "model")}
            options={[
              { value: "workflow", label: "按工作流" },
              { value: "model", label: "按主模型" },
            ]}
          />
          <Dropdown
            menu={{
              items: [
                { key: "entries", label: "逐条流水" },
                { key: "workflow", label: "按工作流聚合" },
                { key: "model", label: "按主模型聚合" },
              ],
              onClick: ({ key }) => download(costExportUrl(key as "entries" | "workflow" | "model", DAYS)),
            }}
          >
            <DownloadOutlined style={{ color: "#5b6172" }} />
          </Dropdown>
        </Space>
      }
    >
      <Table
        size="small"
        rowKey="key"
        loading={isLoading}
        columns={columns}
        dataSource={report?.rows ?? []}
        locale={{ emptyText: "暂无云端成本记录（仅统计已入账的云端请求）" }}
        pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
      />
      {(report?.rows.length ?? 0) > 0 && (
        <div style={{ marginTop: 8, color: "#8a90a2", fontSize: 12 }}>
          前 {report!.rows.length} 项合计估算 ${report!.total_est_cost_usd.toFixed(4)}（未列出的条目见 CSV 导出）
        </div>
      )}
    </Card>
  );
}

function StatBlock({ label, value, sub, tooltip }: { label: string; value: string; sub?: string; tooltip?: string }) {
  return (
    <Tooltip title={tooltip}>
      <div>
        <div style={{ color: "#8a90a2", fontSize: 12 }}>{label}</div>
        <div style={{ fontSize: 20, fontWeight: 600, lineHeight: 1.3 }}>{value}</div>
        {sub && <div style={{ color: "#8a90a2", fontSize: 12 }}>{sub}</div>}
      </div>
    </Tooltip>
  );
}

function ReconCard() {
  const [cycle, setCycle] = useState(() => dayjs().format("YYYY-MM"));
  const { data: recon, isLoading } = useCostReconciliation(cycle);

  const dailyCols: ColumnsType<ReconciliationDailyPoint> = [
    { title: "日期（UTC）", dataIndex: "date", width: 110 },
    { title: "本地请求", dataIndex: "local_requests", width: 90, align: "right" },
    { title: "本地估算", dataIndex: "local_usd", width: 110, align: "right", render: (v: number) => usd(v) },
    { title: "Modal GPU", dataIndex: "modal_gpu_usd", width: 110, align: "right", render: (v: number) => usd(v) },
    { title: "Modal 全资源", dataIndex: "modal_total_usd", width: 120, align: "right", render: (v: number) => usd(v) },
  ];

  const resourceTags = Object.entries(recon?.modal.resources ?? {})
    .slice(0, 6)
    .map(([name, v]) => (
      <Tag key={name} style={{ marginInlineEnd: 4 }}>
        {name} {usd(v, 3)}
      </Tag>
    ));

  return (
    <Card
      size="small"
      title="账单对账（本地估算 vs Modal 官方明细）"
      extra={
        <DatePicker
          picker="month"
          size="small"
          allowClear={false}
          value={dayjs(cycle, "YYYY-MM")}
          disabledDate={(d) => d.isAfter(dayjs(), "month")}
          onChange={(d) => d && setCycle(d.format("YYYY-MM"))}
        />
      }
      loading={isLoading}
    >
      {recon?.modal.ok === false && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="Modal 账单明细获取失败"
          description={recon.modal.error}
        />
      )}
      {recon?.diverged && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`GPU 计量 ÷ 本地估算 = ${recon.ratio_gpu_vs_est?.toFixed(2)}，已偏离合理区间`}
          description={
            <>
              {recon.divergence_message}
              <br />
              这是本地估算与实际计费的校准提示；账户级消费上限请以 Modal 平台预算/告警设置为准。
            </>
          }
        />
      )}
      {recon && (
        <>
          <Space size={40} wrap style={{ marginBottom: 12 }}>
            <StatBlock
              label="本地估算"
              value={usd(recon.local.est_cost_usd)}
              sub={`${recon.local.requests} 次 · ${recon.local.gpu_seconds.toFixed(0)} GPU 秒`}
              tooltip="GPU 单价 × 请求墙钟秒数（含冷启动等待），按次直加的保守估算"
            />
            <StatBlock
              label="Modal GPU 计量"
              value={usd(recon.modal.gpu_usd)}
              sub="与估算同口径（仅 GPU 资源行）"
              tooltip={`Modal 账单明细中 GPU 资源（${recon.cycle} 月）的真实计量`}
            />
            <StatBlock
              label="Modal 全资源"
              value={usd(recon.modal.total_usd)}
              sub="含 CPU/内存/卷等全部计量"
            />
            <StatBlock
              label="GPU ÷ 估算"
              value={recon.ratio_gpu_vs_est == null ? "—" : recon.ratio_gpu_vs_est.toFixed(2)}
              sub="持续偏离 1 → 校准单价"
              tooltip="Modal GPU 计量 ÷ 本地估算。估算含冷启动等待、保活窗口不计入，短期偏离正常；持续偏高/偏低说明 gpu_hourly_rate_usd 设置需要校准"
            />
          </Space>
          {resourceTags.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <span style={{ color: "#8a90a2", fontSize: 12, marginRight: 8 }}>资源构成：</span>
              {resourceTags}
            </div>
          )}
          <Table
            size="small"
            rowKey="date"
            columns={dailyCols}
            dataSource={recon.daily}
            locale={{ emptyText: "该月暂无两侧数据" }}
            pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
          />
        </>
      )}
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
        {recon?.note ?? "对账口径：本地按次估算 vs Modal 账单明细（UTC 月）；日界为 UTC，与控制台逐日图（本地日界）最多差数小时。"}
      </Typography.Paragraph>
    </Card>
  );
}

export default function CostPage() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <ReportCard />
      <ReconCard />
    </div>
  );
}
