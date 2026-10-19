import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import { usePageVisible } from "../hooks/usePageVisible";

export interface LocalWorkflow {
  name: string;
  file: string;
  size_bytes: number;
  mtime: string;
  hash: string;
}

export interface AssetsDbInfo {
  path: string;
  tables?: string[];
  assets_count?: number | null;
  error?: string;
}

export interface LocalWorkflows {
  workflows_dir: string;
  dir_exists: boolean;
  workflows: LocalWorkflow[];
  assets_db: AssetsDbInfo | null;
}

export function useLocalWorkflows() {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["workflows", "local"],
    queryFn: ({ signal }) => apiGet<LocalWorkflows>("/api/v1/workflows/local", signal),
    refetchInterval: visible ? 30_000 : false,
  });
}

/* ---------------- 双向同步（M2-4）：本地 ⇄ 数据卷 user/default/workflows/ ---------------- */

export type WfSyncAction = "push" | "pull" | "keep_both";

export interface WfSyncRow {
  name: string;
  state: "synced" | "local_new" | "cloud_new" | "local_changed" | "cloud_changed" | "conflict";
  suggested: WfSyncAction | null;
  local_hash: string | null;
  cloud_hash: string | null;
}

export interface WfSyncStatus {
  ok: boolean;
  error: string | null;
  local_dir: string;
  cloud_dir: string;
  volume: string;
  rows: WfSyncRow[];
  counts: Partial<Record<WfSyncRow["state"], number>>;
}

export function useWorkflowSync() {
  return useQuery({
    queryKey: ["workflows", "sync"],
    queryFn: ({ signal }) => apiGet<WfSyncStatus>("/api/v1/workflows/sync", signal),
  });
}

/* ---------------- 冲突双列对比（M2 收尾） ---------------- */

export interface WfCompareSide {
  exists: boolean;
  error?: string;
  size_bytes?: number;
  mtime?: string; // 云端版经卷取回，无可靠 mtime，缺省
  hash?: string; // sha256 前 12 位
  summary_error?: string;
  ok?: boolean; // 摘要解析是否成功
  node_count?: number;
  types?: [string, number][]; // 节点类型计数（降序，最多 24）
  models?: string[]; // 引用的模型文件名
}

export interface WfCompare {
  name: string;
  local: WfCompareSide;
  cloud: WfCompareSide;
  identical: boolean;
}

/** 冲突双列对比：本地版 vs 云端版元数据与内容摘要（云端版现读数据卷）。 */
export function fetchWorkflowCompare(name: string, signal?: AbortSignal) {
  return apiGet<WfCompare>(`/api/v1/workflows/compare?name=${encodeURIComponent(name)}`, signal);
}

/* ---------------- 运行统计（requests 表按工作流聚合，SQL 现算） ---------------- */

export interface WfRunStat {
  name: string;
  runs: number;
  done: number;
  errored: number;
  modal_runs: number;
  local_runs: number;
  avg_ms: number | null;
  max_ms: number | null;
  est_cost_usd: number;
  last_ms: number;
}

export interface WfRunStats {
  days: number;
  rows: WfRunStat[];
  note: string;
}

export function useWorkflowRunStats(days: number) {
  return useQuery({
    queryKey: ["workflows", "run-stats", days],
    queryFn: ({ signal }) => apiGet<WfRunStats>(`/api/v1/workflows/run-stats?days=${days}`, signal),
    staleTime: 15_000,
  });
}
