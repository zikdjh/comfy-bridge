import { useEffect, useState, type ReactNode } from "react";
import { Button, Drawer, List, Popconfirm, Progress, Space, Spin, Tag, Tooltip, Typography, message } from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, MinusCircleOutlined, RedoOutlined, SyncOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import {
  cancelJob,
  startNodeSync,
  startWorkflowSync,
  useJob,
  useJobs,
  type JobsSnapshot,
  type SyncJob,
} from "../../api/jobs";
import { useDarkMode } from "../../hooks/useDarkMode";
import { InlineError } from "./ActionableError";

const KIND_META: Record<string, { text: string; color: string }> = {
  workflow_sync: { text: "工作流同步", color: "geekblue" },
  node_sync: { text: "节点同步", color: "purple" },
};

const STATE_META: Record<SyncJob["state"], { text: string; color: string; icon: ReactNode }> = {
  running: { text: "进行中", color: "processing", icon: <SyncOutlined spin /> },
  ok: { text: "已完成", color: "success", icon: <CheckCircleOutlined /> },
  error: { text: "失败", color: "error", icon: <CloseCircleOutlined /> },
  cancelled: { text: "已取消", color: "default", icon: <MinusCircleOutlined /> },
  interrupted: { text: "已中断", color: "warning", icon: <MinusCircleOutlined /> },
};

function jobTime(ms: number) {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

function fmtElapsed(ms: number) {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m${Math.round((ms % 60_000) / 1000)}s`;
}

function estimateRemainingMs(job: SyncJob, elapsedMs: number): number | null {
  if (job.state !== "running" || job.done <= 0 || job.total <= job.done) return null;
  return (elapsedMs / job.done) * (job.total - job.done);
}

function JobCard({ job, onRetried }: { job: SyncJob; onRetried?: (jobId: string) => void }) {
  const { tokens: t } = useDarkMode();
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [now, setNow] = useState(Date.now());
  const state = STATE_META[job.state] ?? STATE_META.cancelled;
  const kind = KIND_META[job.kind] ?? { text: job.kind, color: "default" };
  const running = job.state === "running";
  const percent = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
  const failed = job.items.filter((i) => !i.ok);
  const startedMs = job.started_ms ?? job.created_ms;
  const elapsedMs = Math.max((job.finished_ms ?? now) - startedMs, 0);
  const remainingMs = estimateRemainingMs(job, elapsedMs);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

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

  const retryFailed = async () => {
    setRetrying(true);
    try {
      const names = [...new Set(failed.map((item) => item.name))];
      let res: Awaited<ReturnType<typeof startNodeSync>>;
    if (job.kind === "node_sync") {
        if (names.some((name) => name.startsWith("未完成条目（"))) {
          message.warning("重启中断的任务缺少条目明细，请重新发起原同步操作");
          return;
        }
        res = await startNodeSync(names);
      } else {
        const actions = failed
          .filter((item) => !item.name.startsWith("未完成条目（"))
          .map((item) => ({ name: item.name, action: item.action ?? "push" }));
        if (!actions.length) {
          message.warning("重启中断的任务缺少条目明细，请重新发起原同步操作");
          return;
        }
        res = await startWorkflowSync(actions);
      }
      if (!res.ok || !res.job) {
        message.error(res.error ?? "重试任务提交失败");
        return;
      }
      queryClient.setQueryData<JobsSnapshot>(["jobs"], (previous) => ({
        running: (previous?.running ?? 0) + 1,
        jobs: [res.job!, ...(previous?.jobs ?? []).filter((item) => item.id !== res.job!.id)],
      }));
      queryClient.setQueryData(["jobs", res.job.id], res.job);
      void queryClient.invalidateQueries({ queryKey: ["jobs"], exact: true });
      message.info(`已重试 ${names.length} 个失败条目，进度见任务抽屉`);
      onRetried?.(res.job.id);
    } catch (exc) {
      message.error(`重试提交失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setRetrying(false);
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
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {running ? "已运行" : "耗时"} {fmtElapsed(elapsedMs)}
          </Typography.Text>
          {running && job.total > 0 && job.done === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              预计等待：样本积累中
            </Typography.Text>
          )}
          {remainingMs != null && (
            <Tooltip title="根据本次已完成条目的平均速度估算；网络、依赖安装或单个条目大小差异可能使实际时间更长。">
              <Typography.Text type="secondary" style={{ fontSize: 12 }} data-testid="job-estimate">
                预计约 {fmtElapsed(remainingMs)}
              </Typography.Text>
            </Tooltip>
          )}
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
        status={running ? "active" : job.state === "error" || job.state === "interrupted" ? "exception" : job.state === "ok" ? "success" : "normal"}
        format={() => `${job.done}/${job.total}`}
        style={{ maxWidth: 480, margin: "4px 0" }}
      />
      <div style={{ fontSize: 12, marginBottom: 4 }}>
        <Typography.Text type="secondary">当前阶段：</Typography.Text>
        <Typography.Text strong>{job.phase ?? (running ? "执行中" : state.text)}</Typography.Text>
      </div>
      {job.error && <InlineError error={job.error} />}
      {job.items.length > 0 && (
        <div style={{ maxHeight: 180, overflow: "auto", margin: "6px 0", padding: "4px 8px", background: t.colors.surfaceFaint, borderRadius: 6 }}>
          {job.items.map((item, idx) => (
            <div key={`${item.name}-${idx}`} style={{ fontSize: 12, lineHeight: "20px" }}>
              {item.ok ? (
                <CheckCircleOutlined style={{ color: t.colors.accent, marginRight: 6 }} />
              ) : (
                <CloseCircleOutlined style={{ color: t.colors.danger, marginRight: 6 }} />
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
                <div style={{ marginTop: 2 }}>
                  <InlineError error={item.error ?? "该条目执行失败"} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {failed.length > 0 && job.state !== "running" && (
        <Space size={8} wrap>
          <Button
            size="small"
            icon={<RedoOutlined />}
            loading={retrying}
            disabled={retrying || !["node_sync", "workflow_sync"].includes(job.kind)}
            onClick={() => void retryFailed()}
          >
            重试失败项（{failed.length}）
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            只重跑失败条目；已完成条目不受影响。
          </Typography.Text>
        </Space>
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
              background: t.colors.surfaceWash,
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
export function JobDrawer({ open, onClose, focusId, onFocusHandled, onJobSelected }: {
  open: boolean;
  onClose: () => void;
  focusId: string | null;
  onFocusHandled?: () => void;
  onJobSelected?: (jobId: string) => void;
}) {
  const { tokens: t } = useDarkMode();
  const { data } = useJobs();
  const [selected, setSelected] = useState<string | null>(null);
  const { data: focusData, isLoading: focusLoading } = useJob(open ? (focusId ?? selected) : null);

  // 焦点作业数据到位后转内部选中态（Shell 清掉 focusId，之后由抽屉自持）
  useEffect(() => {
    if (focusId && focusData) {
      setSelected(focusId);
      onFocusHandled?.();
    }
  }, [focusId, focusData, onFocusHandled]);

  const jobs = data?.jobs ?? [];
  const selectedJob = selected ? jobs.find((job) => job.id === selected) : undefined;
  const defaultJob = jobs.find((job) => job.state === "running") ?? jobs[0];
  const focused = focusData
    ?? (focusId ? jobs.find((job) => job.id === focusId) : undefined)
    ?? selectedJob
    ?? defaultJob
    ?? null;
  const others = jobs.filter((j) => j.id !== focused?.id);

  return (
    <Drawer
      title={`任务（${data?.running ?? 0} 个进行中）`}
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      {focusId && focusLoading && <Spin style={{ display: "block", margin: "32px auto" }} />}
      {focused && (
        <>
          <List split={false} style={{ background: t.colors.surface }}>
            <JobCard job={focused} onRetried={onJobSelected} />
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
              role="button"
              tabIndex={0}
              aria-label={`查看任务 ${job.label}`}
              onClick={() => {
                setSelected(job.id);
                onJobSelected?.(job.id);
              }}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  setSelected(job.id);
                  onJobSelected?.(job.id);
                }
              }}
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
