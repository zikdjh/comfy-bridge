import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

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
  return useQuery({
    queryKey: ["workflows", "local"],
    queryFn: ({ signal }) => apiGet<LocalWorkflows>("/api/v1/workflows/local", signal),
    refetchInterval: 30_000,
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

export interface WfSyncResult {
  name: string;
  ok: boolean;
  action?: string;
  conflict_copy?: string;
  error?: string;
}

/** 执行同步动作：push 本地覆盖云端 / pull 云端覆盖本地 / keep_both 冲突双副本。 */
export function applyWorkflowSync(actions: { name: string; action: WfSyncAction }[]) {
  return apiSend<{ ok: boolean; results: WfSyncResult[] }>("/api/v1/workflows/sync", "POST", { actions });
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
