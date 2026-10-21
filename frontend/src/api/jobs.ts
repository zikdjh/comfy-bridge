import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

/** 同步任务（M2-6）：任务化长操作的统一视图，JobDrawer / 顶栏角标共用。 */
export interface SyncJobItem {
  name: string;
  ok: boolean;
  error?: string;
  action?: string;
  remote?: string;
  conflict_copy?: string;
}

export interface SyncJob {
  id: string;
  kind: string; // workflow_sync | node_sync（后续其他长任务可接入）
  label: string;
  state: "running" | "ok" | "error" | "cancelled";
  /** 当前阶段（旧任务快照可能没有该字段） */
  phase?: string;
  total: number;
  done: number;
  items: SyncJobItem[];
  logs: string[];
  error: string | null;
  cancellable: boolean;
  created_ms: number;
  started_ms?: number | null;
  finished_ms: number | null;
}

export interface JobsSnapshot {
  jobs: SyncJob[];
  running: number;
}

export function useJobs() {
  return useQuery({
    queryKey: ["jobs"],
    queryFn: ({ signal }) =>
      apiGet<JobsSnapshot>("/api/v1/jobs?limit=50", signal),
    // 统一事件流负责实时更新；这里只做断线兜底（P1-5）。
    refetchInterval: (query) => ((query.state.data?.running ?? 0) > 0 ? 15000 : 30000),
  });
}

export function useJob(id: string | null) {
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: async ({ signal }) => (await apiGet<{ job: SyncJob }>(`/api/v1/jobs/${id}`, signal)).job,
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.state === "running" ? 15000 : false),
  });
}

export function cancelJob(id: string) {
  return apiSend<{ cancelled: boolean }>(`/api/v1/jobs/${id}/cancel`, "POST", {});
}

/* ---------------- 任务化提交入口 ---------------- */

export function startWorkflowSync(actions: { name: string; action: string }[]) {
  return apiSend<{ ok: boolean; error?: string; job?: SyncJob }>("/api/v1/workflows/sync", "POST", { actions });
}

export function startNodeSync(names: string[]) {
  return apiSend<{ ok: boolean; error?: string; job?: SyncJob; note?: string }>("/api/v1/nodes/sync", "POST", { names });
}

/* ---------------- 全局任务抽屉开关（Shell 监听，页面只管唤起） ---------------- */

export const JOB_DRAWER_EVENT = "cb-open-jobs";

export function openJobDrawer(jobId?: string) {
  window.dispatchEvent(new CustomEvent(JOB_DRAWER_EVENT, { detail: { jobId } }));
}
