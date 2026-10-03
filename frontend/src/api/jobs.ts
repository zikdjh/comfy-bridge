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
  total: number;
  done: number;
  items: SyncJobItem[];
  logs: string[];
  error: string | null;
  cancellable: boolean;
  created_ms: number;
  finished_ms: number | null;
}

export function useJobs() {
  return useQuery({
    queryKey: ["jobs"],
    queryFn: ({ signal }) =>
      apiGet<{ jobs: SyncJob[]; running: number }>("/api/v1/jobs?limit=50", signal),
    refetchInterval: (query) => ((query.state.data?.running ?? 0) > 0 ? 3000 : 15000),
  });
}

export function useJob(id: string | null) {
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: async ({ signal }) => (await apiGet<{ job: SyncJob }>(`/api/v1/jobs/${id}`, signal)).job,
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.state === "running" ? 2000 : false),
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
