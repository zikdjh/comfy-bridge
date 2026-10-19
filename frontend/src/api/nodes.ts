import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import type { SyncJob } from "./jobs";
import { usePageVisible } from "../hooks/usePageVisible";

export interface CustomNodePackage {
  name: string;
  repo: string | null;
  commit: string | null;
  has_requirements: boolean;
  enabled: boolean;
  /** Manager API 兜底采集补充的字段（可选，仅本地 ComfyUI 装有 ComfyUI-Manager 时出现） */
  repo_via_manager?: boolean;
  manager_version?: string;
  manager_title?: string;
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
  /** ComfyUI-Manager API 兜底采集状态（本机未装 Manager 或 ComfyUI 离线时 online=false） */
  manager: { online: boolean; entries: number; matched?: number; error: string | null };
  custom_nodes: CustomNodeScan;
}

export function useLocalNodes() {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["nodes", "local"],
    queryFn: ({ signal }) => apiGet<LocalNodes>("/api/v1/nodes/local", signal),
    refetchInterval: visible ? 30_000 : false,
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
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["nodes", "cloud"],
    queryFn: ({ signal }) => apiGet<CloudNodes>("/api/v1/nodes/cloud", signal),
    refetchInterval: visible ? 30_000 : false,
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
  cloud_commit?: string | null;
  cloud_repo?: string | null;
  cloud_enabled?: boolean | null;
  local_hash?: string | null; // 单文件节点内容 sha256 前 12 位（pkg 走 commit 对比，无哈希）
  cloud_hash?: string | null;
  state?: "missing_cloud" | "missing_local" | "same" | "changed" | "unknown";
}

export interface NodeDiff {
  ok: boolean;
  cloud_error: string | null;
  app_state: string | null;
  volume: string;
  rows: NodeDiffRow[];
  counts: { synced: number; only_local: number; only_cloud: number };
  /** 矩阵缓存命中标记（cached_at = 缓存生成时刻 ISO） */
  cached: boolean;
  cached_at: string | null;
  class_types: {
    local: string[] | null;
    cloud: string[] | null;
    error: string | null;
    conflicts?: Record<string, string[]>;
    merge_strategy?: string;
    cached?: boolean;
    cached_at?: string | null;
  };
}

/**
 * deep=true 时同时取两端 class_type 对比；矩阵短 TTL 缓存 + deep class_type 块持久缓存，
 * force=true 绕过缓存现算（云端 class_type 无缓存时会触发容器冷启动，由 UI 显式触发）。
 */
export function useNodeDiff(deep: boolean) {
  return useQuery({
    queryKey: ["nodes", "diff", deep],
    queryFn: ({ signal }) => apiGet<NodeDiff>(`/api/v1/nodes/diff${deep ? "?deep=1" : ""}`, signal),
    placeholderData: (prev) => prev,
  });
}

/** 强制刷新：绕过后端缓存现算矩阵（与 deep class_type 缓存），结果回写缓存后由调用方失效查询。 */
export async function refreshNodeDiff(deep: boolean) {
  return apiGet<NodeDiff>(`/api/v1/nodes/diff?deep=${deep ? 1 : 0}&force=1`);
}

export interface NodeOpResult {
  ok: boolean;
  results: { name: string; ok: boolean; error?: string; remote?: string }[];
  note?: string;
}

/** 安装本地节点包/单文件到云端数据卷（M2-6 任务化）：立即返回 job，进度经任务抽屉轮询。 */
export function syncCloudNodes(names: string[]) {
  return apiSend<{ ok: boolean; error?: string; job?: SyncJob; note?: string }>(
    "/api/v1/nodes/sync", "POST", { names },
  );
}

/** 从云端数据卷移除节点。 */
export function removeCloudNodes(names: string[]) {
  return apiSend<NodeOpResult>("/api/v1/nodes/cloud/remove", "POST", { names });
}

/* ---------------- ComfyUI 版本对齐（本地为权威 → 云端镜像 pin） ---------------- */

export interface VersionJob {
  state: "idle" | "running" | "ok" | "error";
  started_ms: number | null;
  finished_ms: number | null;
  label: string | null;
  error: string | null;
  log_tail: string[];
}

/** 对齐历史一条记录（version_align.HISTORY_FILE JSONL，新的在前）。 */
export interface VersionHistoryRecord {
  ts_ms: number;
  time: string;
  trigger: "align" | "rebuild";
  from_pin: string;
  to_tag: string;
  outcome: "ok" | "rolled_back" | "timeout" | "noop";
  error?: string | null;
  duration_ms?: number | null;
  local_commit?: string | null;
}

export interface NodeVersionStatus {
  local_version: string | null;
  local_commit: string | null;
  pinned: string;
  tag: string | null;
  drift: boolean;
  runner_file: string;
  app_name: string;
  job: VersionJob;
  history: VersionHistoryRecord[];
}

export function useNodeVersion() {
  return useQuery({
    queryKey: ["nodes", "version"],
    queryFn: ({ signal }) => apiGet<NodeVersionStatus>("/api/v1/nodes/version", signal),
    refetchInterval: (query) => (query.state.data?.job?.state === "running" ? 3000 : false),
  });
}

export interface DependencyConflict {
  package: string;
  requirements: string[];
  reason: string;
  nodes: string[];
}

export interface DependencyPrecheck {
  status: "ok" | "blocked" | "unavailable";
  conflicts: DependencyConflict[];
  nodes: string[];
  merged_requirements: string[];
  detail: string | null;
  log_tail: string[];
  cached: boolean;
}

/** 镜像重建前的节点依赖预检；不触发 Modal 容器或 deploy。force=true 绕过后端 5 分钟缓存。 */
export function checkNodeDependencies(force = false) {
  return apiGet<DependencyPrecheck>(
    `/api/v1/nodes/dependency-precheck${force ? "?force=1" : ""}`,
  );
}

/** 把云端镜像 pin 对齐到本地版本并 modal deploy 重建镜像（后台任务，失败自动回滚 pin）。 */
export function alignNodeVersion() {
  return apiSend<{ ok: boolean; job: VersionJob }>("/api/v1/nodes/version/align", "POST", {});
}

/** 镜像重建：固化本地节点依赖进镜像；align_version=true 并入库内版本对齐（一次 deploy，失败自动回滚）。 */
export function rebuildNodeImage(alignVersion: boolean) {
  return apiSend<{ ok: boolean; job: VersionJob }>("/api/v1/nodes/image/rebuild", "POST", {
    align_version: alignVersion,
  });
}
