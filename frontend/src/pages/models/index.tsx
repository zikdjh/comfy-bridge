import { useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { CloudDownloadOutlined, CloudUploadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { message } from "antd";
import { EmptyGuide } from "../../components/common/EmptyGuide";
import { ModelUploadCard } from "../../components/models/ModelUploadCard";
import {
  useCancelJob,
  useStartSync,
  useSyncJobs,
  useVolumeMeta,
  useVolumeOverview,
  type SyncJobDto,
  type SyncRow,
} from "../../api/modalVolume";
import { bridgeTokens as t } from "../../theme";

/** 表格横向滚动兜底：窄屏下列宽超出自动出横向滚动条 */
const TABLE_SCROLL_X = { x: true } as const;

const statusTag = (status: SyncRow["status"]) =>
  status === "both" ? (
    <Tag color="green">已同步</Tag>
  ) : status === "only_local" ? (
    <Tag color="orange">仅本地</Tag>
  ) : (
    <Tag color="geekblue">仅云端</Tag>
  );

const jobStatusTag = (job: SyncJobDto) =>
  job.status === "running" ? (
    <Tag color="processing">进行中</Tag>
  ) : job.status === "done" ? (
    <Tag color="green">已完成</Tag>
  ) : (
    <Tooltip title={job.error}>
      <Tag color="red">失败</Tag>
    </Tooltip>
  );

const jobColumns: ColumnsType<SyncJobDto> = [
  {
    title: "时间",
    dataIndex: "created_ms",
    width: 90,
    render: (ms: number) => new Date(ms).toTimeString().slice(0, 8),
  },
  {
    title: "方向",
    dataIndex: "direction",
    width: 80,
    render: (d: string) =>
      d === "push" ? (
        <Tag icon={<CloudUploadOutlined />} color="blue">上传</Tag>
      ) : (
        <Tag icon={<CloudDownloadOutlined />} color="cyan">下载</Tag>
      ),
  },
  { title: "分类", dataIndex: "category", width: 140 },
  {
    title: "进度",
    key: "progress",
    width: 200,
    render: (_, job) => (
      <Space size={8}>
        <span style={{ fontSize: 12 }}>
          {job.done + job.failed}/{job.total}
        </span>
        <Progress
          percent={job.total ? Math.round(((job.done + job.failed) / job.total) * 100) : 0}
          size="small"
          style={{ width: 120 }}
          strokeColor={job.failed ? t.colors.warning : t.colors.primary}
        />
      </Space>
    ),
  },
  { title: "状态", dataIndex: "status", width: 90, render: (_, job) => jobStatusTag(job) },
  {
    title: "操作",
    key: "action",
    width: 80,
    render: (_, job) =>
      job.status === "running" ? <CancelButton jobId={job.id} /> : null,
  },
];

function CancelButton({ jobId }: { jobId: string }) {
  const cancelJob = useCancelJob();
  return (
    <Popconfirm title="终止该同步作业？" onConfirm={() => cancelJob(jobId).catch((e) => message.error(String(e)))}>
      <Button size="small" danger type="link">
        取消
      </Button>
    </Popconfirm>
  );
}

function JobTable({ jobs }: { jobs: SyncJobDto[] }) {
  if (jobs.length === 0) {
    return <Typography.Text type="secondary">暂无同步作业</Typography.Text>;
  }
  return (
    <Table<SyncJobDto>
      columns={jobColumns}
      dataSource={jobs}
      rowKey="id"
      size="small"
      scroll={TABLE_SCROLL_X}
      pagination={{
        pageSize: 10,
        showTotal: (total) => `共 ${total} 条作业`,
      }}
    />
  );
}

export default function ModelsPage() {
  const meta = useVolumeMeta();
  const [category, setCategory] = useState("checkpoints");
  const overview = useVolumeOverview(category);
  const startSync = useStartSync();

  const jobs = useSyncJobs().data?.jobs ?? [];
  const running = jobs.some((j) => j.status === "running");

  const rows = overview.data?.rows ?? [];
  const onlyLocal = useMemo(() => rows.filter((r) => r.status === "only_local").map((r) => r.name), [rows]);
  const onlyCloud = useMemo(() => rows.filter((r) => r.status === "only_cloud").map((r) => r.name), [rows]);

  const syncingRef = useRef(false);
  const sync = async (direction: "push" | "pull", names: string[]) => {
    if (syncingRef.current) return; // 防双击/连点：上一笔创建请求未返回前忽略再次触发
    syncingRef.current = true;
    try {
      await startSync(direction, category, names);
      message.success(`已创建同步作业（${names.length} 个文件）`);
    } catch (err) {
      message.error(`创建作业失败：${err instanceof Error ? err.message : err}`);
    } finally {
      syncingRef.current = false;
    }
  };

  const columns: ColumnsType<SyncRow> = [
    { title: "文件名", dataIndex: "name" },
    { title: "本地", dataIndex: "local_size", width: 110, render: (v?: string | null) => v ?? "—" },
    { title: "云端", dataIndex: "cloud_size", width: 110, render: (v?: string | null) => v ?? "—" },
    { title: "状态", dataIndex: "status", width: 100, render: (s: SyncRow["status"]) => statusTag(s) },
    {
      title: "操作",
      key: "action",
      width: 140,
      render: (_, row) => (
        <Space size={4}>
          {row.local_size && (
            <SyncButton
              label="上传"
              confirm={row.status === "both" ? "云端已有同名文件，覆盖上传？" : `上传「${row.name}」到云端数据卷？`}
              onConfirm={() => sync("push", [row.name])}
            />
          )}
          {row.cloud_size && (
            <SyncButton
              label="下载"
              confirm={row.status === "both" ? "本地已有同名文件，覆盖下载？" : `从云端下载「${row.name}」到本地？`}
              onConfirm={() => sync("pull", [row.name])}
            />
          )}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Card size="small">
        <Space size={24} wrap>
          <Tag color={meta.data?.credentials_ok ? "green" : "orange"}>
            {meta.data?.credentials_ok ? "Modal 凭证已配置" : "未配置 Modal 凭证"}
          </Tag>
          <span>数据卷：<Typography.Text code>{meta.data?.volume ?? "—"}</Typography.Text></span>
          <Select
            value={category}
            style={{ width: 200 }}
            onChange={setCategory}
            options={(meta.data?.categories ?? [category]).map((c) => ({ value: c, label: c }))}
          />
        </Space>
      </Card>

      {meta.data && !meta.data.credentials_ok && (
        <Alert type="warning" showIcon message="未配置 Modal 凭证" description="请先到「设置 → Modal」填写 Token，再回来同步模型。" />
      )}
      {meta.data && meta.data.credentials_ok && !meta.data.cli_found && (
        <Alert
          type="warning"
          showIcon
          message="Modal Python SDK 不可用"
          description="同步通过 Modal SDK 执行，请安装：pip install 'modal>=1.5'"
        />
      )}

      <Card
        size="small"
        title={
          <Space>
            本地 ↔ 云端对比
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {overview.data?.local_dir}
            </Typography.Text>
          </Space>
        }
        extra={
          <Space>
            <SyncButton
              label={`上传缺云端(${onlyLocal.length})`}
              disabled={onlyLocal.length === 0}
              confirm={`确认上传 ${onlyLocal.length} 个文件到云端数据卷？`}
              description="大文件上传耗时取决于上行带宽"
              onConfirm={() => sync("push", onlyLocal)}
            />
            <SyncButton
              label={`下载到本地(${onlyCloud.length})`}
              disabled={onlyCloud.length === 0}
              confirm={`确认从云端下载 ${onlyCloud.length} 个文件到本地？`}
              description="大文件下载耗时取决于下行带宽"
              onConfirm={() => sync("pull", onlyCloud)}
            />
          </Space>
        }
      >
        {overview.data && !overview.data.ok && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message="云端数据卷读取失败"
            description={overview.data.reason ?? overview.data.cloud_error}
          />
        )}
        {overview.isLoading ? (
          <Typography.Text type="secondary">加载中…</Typography.Text>
        ) : rows.length === 0 ? (
          <EmptyGuide description="该分类本地与云端都没有模型" />
        ) : (
          <Table<SyncRow>
            key={category}
            columns={columns}
            dataSource={rows}
            rowKey="name"
            size="small"
            scroll={TABLE_SCROLL_X}
            pagination={{
              pageSize: 10,
              hideOnSinglePage: true,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50, 100],
              showTotal: (total) => `共 ${total} 个文件`,
            }}
          />
        )}
      </Card>

      <Card size="small" title="同步作业">
        <JobTable jobs={jobs} />
        {running && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            有作业进行中，列表每 2 秒自动刷新；大文件上传耗时取决于上行带宽。
          </Typography.Text>
        )}
      </Card>

      <ModelUploadCard categories={meta.data?.categories ?? Object.keys({ checkpoints: 1 })} />
    </Space>
  );
}

function SyncButton({
  label,
  confirm,
  description,
  onConfirm,
  disabled,
}: {
  label: string;
  confirm: string;
  description?: string;
  onConfirm: () => void;
  disabled?: boolean;
}) {
  return (
    <Popconfirm
      title={confirm}
      description={description}
      onConfirm={onConfirm}
      disabled={disabled}
      okText="确认"
      cancelText="取消"
    >
      <Button size="small" type="link" disabled={disabled}>
        {label}
      </Button>
    </Popconfirm>
  );
}
