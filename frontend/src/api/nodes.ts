import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

export interface CustomNodePackage {
  name: string;
  repo: string | null;
  commit: string | null;
  has_requirements: boolean;
  enabled: boolean;
}

export interface CustomNodeScan {
  path: string;
  packages: CustomNodePackage[];
  single_files: string[];
}

export interface LocalNodes {
  online: boolean;
  version: string | null;
  error: string | null;
  comfy_url: string;
  total_class_types: number;
  class_types: string[];
  class_types_sample: string[];
  custom_nodes: CustomNodeScan;
}

export function useLocalNodes() {
  return useQuery({
    queryKey: ["nodes", "local"],
    queryFn: ({ signal }) => apiGet<LocalNodes>("/api/v1/nodes/local", signal),
    refetchInterval: 30_000,
  });
}

/* ---------------- 云端清单与差异矩阵 ---------------- */

export interface CloudNodeEntry {
  name: string;
  size: string;
}

export interface CloudNodes {
  ok: boolean;
  error: string | null;
  volume: string;
  app_state: string | null;
  packages: CloudNodeEntry[];
  single_files: CloudNodeEntry[];
}

export function useCloudNodes() {
  return useQuery({
    queryKey: ["nodes", "cloud"],
    queryFn: ({ signal }) => apiGet<CloudNodes>("/api/v1/nodes/cloud", signal),
    refetchInterval: 30_000,
  });
}

export interface NodeDiffRow {
  name: string;
  kind: "pkg" | "single";
  repo: string | null;
  commit: string | null;
  has_requirements: boolean;
  enabled: boolean;
  local: boolean;
  cloud: boolean;
  cloud_size: string | null;
}

export interface NodeDiff {
  ok: boolean;
  cloud_error: string | null;
  app_state: string | null;
  volume: string;
  rows: NodeDiffRow[];
  counts: { synced: number; only_local: number; only_cloud: number };
  class_types: { local: string[] | null; cloud: string[] | null; error: string | null };
}

/**
 * deep=true 时同时拉取云端 /object_info 做 class_type 级对比；
 * 容器未运行会触发冷启动（计费），由 UI 按钮显式触发。
 */
export function useNodeDiff(deep: boolean) {
  return useQuery({
    queryKey: ["nodes", "diff", deep],
    queryFn: ({ signal }) => apiGet<NodeDiff>(`/api/v1/nodes/diff${deep ? "?deep=1" : ""}`, signal),
    placeholderData: (prev) => prev,
  });
}

export interface NodeOpResult {
  ok: boolean;
  results: { name: string; ok: boolean; error?: string; remote?: string }[];
  note?: string;
}

/** 安装本地节点包/单文件到云端数据卷（依赖在云端容器下次冷启动时自动安装）。 */
export function syncCloudNodes(names: string[]) {
  return apiSend<NodeOpResult>("/api/v1/nodes/sync", "POST", { names });
}

/** 从云端数据卷移除节点。 */
export function removeCloudNodes(names: string[]) {
  return apiSend<NodeOpResult>("/api/v1/nodes/cloud/remove", "POST", { names });
}
