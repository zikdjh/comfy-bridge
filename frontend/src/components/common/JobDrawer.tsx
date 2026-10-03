import { useEffect, useState, type ReactNode } from "react";
import { Button, Drawer, List, Popconfirm, Progress, Space, Spin, Tag, Tooltip, Typography, message } from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, MinusCircleOutlined, SyncOutlined } from "@ant-design/icons";
import { cancelJob, useJob, useJobs, type SyncJob } from "../../api/jobs";

const KIND_META: Record<string, { text: string; color: string }> = {
  workflow_sync: { text: "工作流同步", color: "geekblue" },
  node_sync: { text: "节点同步", color: "purple" },
};

const STATE_META: Record<SyncJob["state"], { text: string; color: string; icon: ReactNode }> = {
  running: { text: "进行中", color: "processing", icon: <SyncOutlined spin /> },
  ok: { text: "已完成", color: "success", icon: <CheckCircleOutlined /> },
  error: { text: "失败", color: "error", icon: <CloseCircleOutlined /> },
  cancelled: { text: "已取消", color: "default", icon: <MinusCircleOutlined /> },
};

function jobTime(ms: number) {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

function JobCard({ job }: { job: SyncJob }) {
  const [cancelling, setCancelling] = useState(false);
  const state = STATE_META[job.state] ?? STATE_META.cancelled;
  const kind = KIND_META[job.kind] ?? { text: job.kind, color: "default" };
  const running = job.state === "running";
  const percent = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
  const failed = job.items.filter((i) => !i.ok);

  const doCancel = async () => {
    setCancelling(true);
    try {
      const res = await cancelJob(job.id);
      if (res.cancelled) message.info("已请求取消，当前条目完成后停止");
      else message.warning("该任务当前不可取消");
    } catch (exc) {
      message.error(`取消失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setCancelling(false);
    }
  };

  return (
    <List.Item style={{ display: "block", padding: "10px 0" }}>
      <Space size={8} wrap style={{ width: "100%", justifyContent: "space-between" }}>
        <Space size={8} wrap>
          <Tag color={kind.color}>{kind.text}</Tag>
          <Typography.Text strong>{job.label}</Typography.Text>
          <Tag color={state.color} icon={state.icon}>
            {state.text}
          </Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {jobTime(job.created_ms)} 提交
          </Typography.Text>
        </Space>
        {running && job.cancellable && (
          <Popconfirm title="取消该任务？" description="当前条目完成后停止，已完成条目保留。" onConfirm={doCancel}>
            <Button size="small" danger loading={cancelling}>
              取消
            </Button>
          </Popconfirm>
        )}
      </Space>
      <Progress
        size="small"
        percent={running ? percent : 100}
        status={running ? "active" : job.state === "error" ? "exception" : job.state === "ok" ? "success" : "normal"}
        format={() => `${job.done}/${job.total}`}
        style={{ maxWidth: 480, margin: "4px 0" }}
      />
      {job.error && (
        <Typography.Text type="danger" style={{ display: "block", fontSize: 12 }}>
          {job.error}
        </Typography.Text>
      )}
      {job.items.length > 0 && (
        <div style={{ maxHeight: 180, overflow: "auto", margin: "6px 0", padding: "4px 8px", background: "#fafafa", borderRadius: 6 }}>
          {job.items.map((item, idx) => (
            <div key={`${item.name}-${idx}`} style={{ fontSize: 12, lineHeight: "20px" }}>
              {item.ok ? (
                <CheckCircleOutlined style={{ color: "#1a9e6c", marginRight: 6 }} />
              ) : (
                <CloseCircleOutlined style={{ color: "#c0392b", marginRight: 6 }} />
              )}
              <Typography.Text code style={{ fontSize: 12 }}>
                {item.name}
              </Typography.Text>
              {item.conflict_copy && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {" "}
                  → 双副本 {item.conflict_copy}
                </Typography.Text>
              )}
              {!item.ok && (
                <Typography.Text type="danger" style={{ fontSize: 12 }}>
                  {" "}
                  {item.error ?? "失败"}
                </Typography.Text>
              )}
            </div>
          ))}
        </div>
      )}
      {failed.length > 0 && job.state !== "running" && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          失败条目可修正后重新发起；已完成条目不受影响。
        </Typography.Text>
      )}
      {job.logs.length > 0 && (
        <Tooltip title="任务日志（最近 12 条）">
          <pre
            style={{
              fontSize: 11,
              lineHeight: "16px",
              margin: "6px 0 0",
              maxHeight: 96,
              overflow: "auto",
              background: "#f6f6f6",
              padding: "4px 8px",
              borderRadius: 6,
            }}
          >
            {job.logs.slice(-12).join("\n")}
          </pre>
        </Tooltip>
      )}
    </List.Item>
  );
}

/** 全局任务抽屉（M2-6）：任务化同步操作的统一进度/日志/取消面，Shell 头部角标唤起。 */
export function JobDrawer({ open, onClose, focusId, onFocusHandled }: {
  open: boolean;
  onClose: () => void;
  focusId: string | null;
  onFocusHandled?: () => void;
}) {
  const { data } = useJobs();
  const [selected, setSelected] = useState<string | null>(null);
  const currentId = focusId ?? selected;
  const { data: focusData, isLoading: focusLoading } = useJob(open && focusId ? focusId : null);

  // 焦点作业数据到位后转内部选中态（Shell 清掉 focusId，之后由抽屉自持）
  useEffect(() => {
    if (focusId && focusData) {
      setSelected(focusId);
      onFocusHandled?.();
    }
  }, [focusId, focusData, onFocusHandled]);

  const jobs = data?.jobs ?? [];
  const focused = focusData ?? jobs.find((j) => j.id === currentId) ?? null;
  const others = jobs.filter((j) => j.id !== focused?.id);

  return (
    <Drawer
      title={`任务（${data?.running ?? 0} 个进行中）`}
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {focusId && focusLoading && <Spin style={{ display: "block", margin: "32px auto" }} />}
      {focused && (
        <>
          <List split={false} style={{ background: "#fff" }}>
            <JobCard job={focused} />
          </List>
          {others.length > 0 && (
            <Typography.Title level={5} style={{ marginTop: 8 }}>
              其他任务
            </Typography.Title>
          )}
        </>
      )}
      {others.length > 0 ? (
        <List
          split={false}
          dataSource={others}
          renderItem={(job) => (
            <List.Item
              style={{ padding: "8px 0", cursor: "pointer" }}
              onClick={() => setSelected(job.id)}
            >
              <Space size={8} wrap>
                <Tag color={(KIND_META[job.kind] ?? { color: "default" }).color}>
                  {(KIND_META[job.kind] ?? { text: job.kind }).text}
                </Tag>
                <Typography.Text>{job.label}</Typography.Text>
                <Tag color={STATE_META[job.state].color} icon={STATE_META[job.state].icon}>
                  {STATE_META[job.state].text}
                </Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {job.done}/{job.total} · {jobTime(job.created_ms)}
                </Typography.Text>
              </Space>
            </List.Item>
          )}
        />
      ) : (
        !focused && (
          <Typography.Text type="secondary">
            暂无任务 —— 工作流同步、节点同步等长操作提交后在这里显示进度与日志。
          </Typography.Text>
        )
      )}
    </Drawer>
  );
}
