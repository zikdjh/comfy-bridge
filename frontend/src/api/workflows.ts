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
