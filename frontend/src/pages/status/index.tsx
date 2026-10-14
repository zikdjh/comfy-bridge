import { Alert, Badge, Button, Card, Descriptions, Space, Table, Tag, Tooltip, Typography } from "antd";
import { ApiOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useModalCliHealth, type ModalCliFailure } from "../../api/health";

const CATEGORY_COLOR: Record<ModalCliFailure["category"], string> = {
  not_installed: "red",
  not_logged_in: "orange",
  network: "gold",
  permission: "volcano",
  rate_limited: "geekblue",
  uncertain: "purple",
  command: "default",
};

function stateTag(value: boolean | null, okText: string, badText: string) {
  if (value == null) return <Tag>未知</Tag>;
  return value ? <Tag color="green">{okText}</Tag> : <Tag color="red">{badText}</Tag>;
}

function FailureTable({ rows }: { rows: ModalCliFailure[] }) {
  const columns: ColumnsType<ModalCliFailure> = [
    {
      title: "分类",
      dataIndex: "category",
      width: 120,
      render: (value: ModalCliFailure["category"], row) => (
        <Tag color={CATEGORY_COLOR[value]}>{row.label}</Tag>
      ),
    },
    {
      title: "错误",
      dataIndex: "message",
      render: (value: string) => (
        <Typography.Text style={{ fontSize: 12 }} ellipsis={{ tooltip: value }}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "建议动作",
      dataIndex: "next_action",
      width: 320,
      render: (value: string | null) => value ?? "—",
    },
    {
      title: "时间",
      dataIndex: "ts_ms",
      width: 150,
      render: (value: number | undefined) => (value ? dayjs(value).format("YYYY-MM-DD HH:mm:ss") : "—"),
    },
  ];
  return (
    <Table
      rowKey={(row) => `${row.category}-${row.ts_ms ?? row.message}`}
      columns={columns}
      dataSource={rows}
      pagination={false}
      size="small"
      locale={{ emptyText: "当前进程还没有 SDK 失败记录" }}
    />
  );
}

export default function StatusPage() {
  const { data, isLoading, isFetching, refetch } = useModalCliHealth();
  const failures = [data?.error, data?.last_failure].filter(
    (item): item is ModalCliFailure => !!item,
  );
  const uniqueFailures = failures.filter(
    (item, index) =>
      failures.findIndex((other) => other.message === item.message && other.category === item.category) === index,
  );
  const healthy = !!data?.found && data.logged_in === true && !data.error;

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type={healthy ? "success" : data?.error ? "error" : "warning"}
        showIcon
        message={healthy ? "Modal SDK 自检通过" : "Modal SDK 需要处理"}
        description={
          healthy
            ? "SDK 可加载、登录态有效；卷、应用和账单操作应可正常使用。"
            : data?.error?.next_action ?? "点「重新自检」获取最新状态。"
        }
        action={
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => void refetch()}>
            重新自检
          </Button>
        }
      />

      <Card
        size="small"
        title={
          <Space size={6}>
            <ApiOutlined />
            Modal SDK
          </Space>
        }
        loading={isLoading && !data}
      >
        <Descriptions column={{ xs: 1, md: 2 }} size="small">
          <Descriptions.Item label="SDK 状态">
            <Badge
              status={data?.found ? "success" : "error"}
              text={data?.found ? "可用" : "不可用"}
            />
          </Descriptions.Item>
          <Descriptions.Item label="登录状态">
            {stateTag(data?.logged_in ?? null, "已登录", "未登录")}
          </Descriptions.Item>
          <Descriptions.Item label="版本">
            <Typography.Text code>{data?.version ?? "—"}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="命令延迟">
            {data?.latency_ms != null ? `${data.latency_ms} ms` : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="应用状态">
            {data?.app_state ? <Tag color="geekblue">{data.app_state}</Tag> : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="最近成功">
            {data?.last_success_ms ? dayjs(data.last_success_ms).format("YYYY-MM-DD HH:mm:ss") : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Modal SDK" span={2}>
            <Tooltip title={data?.path ?? ""}>
              <Typography.Text code copyable={!!data?.path} style={{ fontSize: 12 }}>
                {data?.path ?? "—"}
              </Typography.Text>
            </Tooltip>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card size="small" title="最近失败与处理建议">
        <FailureTable rows={uniqueFailures} />
      </Card>

      <Card size="small" title="错误分类说明">
        <Space direction="vertical" size={6}>
          <span><Tag color="red">不可用</Tag>Modal Python SDK 不可用：安装 `modal&gt;=1.5` 后重启桥接器。</span>
          <span><Tag color="orange">未登录</Tag>凭证失效或未登录：`modal token new`，或在设置页填写 token。</span>
          <span><Tag color="gold">网络失败</Tag>只读命令自动快速重试；写命令不会自动重发。</span>
          <span><Tag color="volcano">权限不足</Tag>当前账户缺少资源权限：检查 workspace / token 权限。</span>
          <span><Tag color="geekblue">平台限流</Tag>Modal 控制面限流（rate limit）：只读命令按 1s/4s/10s 退避自动重试，稍候即可恢复。</span>
          <span><Tag color="purple">状态不确定</Tag>超时或远端未确认：先在 Modal 控制台核对，再决定是否重试。</span>
        </Space>
      </Card>
    </Space>
  );
}
