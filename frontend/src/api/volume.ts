import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

export interface VolumeMeta {
  volume: string;
  credentials_ok: boolean;
  cli_found: boolean;
}

export function useVolumeMeta() {
  return useQuery({
    queryKey: ["volume", "meta"],
    queryFn: ({ signal }) => apiGet<VolumeMeta>("/api/v1/volume/meta", signal),
  });
}

/* ---------------- 全部卷 + 总用量（实时统计） ---------------- */

export interface VolumeUsageItem {
  name: string;
  created_at: string;
  total_bytes: number;
  file_count: number;
  error: string | null;
}

export interface VolumeUsage {
  computing: boolean;
  computed_ms: number;
  total_bytes: number;
  file_count: number;
  status: "ok" | "warn" | "over"; // ok=正常 warn=≥512GiB 黄 over=≥1024GiB 红
  volumes: VolumeUsageItem[];
  error: string | null;
}

export function useVolumeUsage() {
  return useQuery({
    queryKey: ["volume", "usage"],
    queryFn: ({ signal }) => apiGet<VolumeUsage>("/api/v1/volume/volumes", signal),
    refetchInterval: (query) =>
      query.state.data?.computing ? 3_000 : 30_000, // 统计中快速轮询，平时 30s 保活
  });
}

/** 强制后台重算所有卷用量（绕过 60s 缓存；返回 computing 快照，轮询直到完成）。 */
export function forceVolumeUsageRefresh() {
  return apiGet<VolumeUsage>("/api/v1/volume/volumes?refresh=1");
}

export function createVolume(name: string) {
  return apiSend<{ ok: boolean; name: string }>("/api/v1/volume/create", "POST", { name });
}

export function deleteVolume(name: string) {
  return apiSend<{ ok: boolean; name: string }>(`/api/v1/volume/volumes/${name}/delete`, "POST");
}

/* ---------------- 单卷目录浏览与删除 ---------------- */

export interface VolumeEntry {
  name: string;
  type: string; // file | dir
  size: string;
  mtime: string;
}

export interface VolumeList {
  ok: boolean;
  error: string | null;
  path: string;
  volume: string;
  entries: VolumeEntry[];
}

export function useVolumeList(path: string, volume: string) {
  return useQuery({
    queryKey: ["volume", "list", volume, path],
    queryFn: ({ signal }) =>
      apiGet<VolumeList>(
        `/api/v1/volume/list?path=${encodeURIComponent(path)}&volume=${encodeURIComponent(volume)}`,
        signal,
      ),
    placeholderData: (prev) => prev,
  });
}

export interface PathOpResult {
  ok: boolean;
  results: { path: string; ok: boolean; error?: string }[];
  note?: string;
}

export function deleteVolumePaths(paths: string[], volume: string) {
  return apiSend<PathOpResult>("/api/v1/volume/delete", "POST", { paths, volume });
}

/* ---------------- 云端直下（仅主卷：runner 只挂载 comfyui-data） ---------------- */

export interface DownloadJob {
  id: string;
  url: string;
  name: string;
  dest_dir: string;
  filename: string | null;
  volume: string;
  status: "running" | "done" | "error";
  error: string | null;
  log_tail: string[];
  result_files: string[];
  created_ms: number;
  finished_ms: number | null;
}

/** 创建云端直下作业：把 URL 下载进所选数据卷的 dest_dir（后台 modal run，可取消）。 */
export function createVolumeDownload(url: string, destDir: string, volume: string, filename?: string) {
  return apiSend<{ job: DownloadJob }>("/api/v1/volume/download", "POST", {
    url,
    dest_dir: destDir,
    volume,
    filename: filename || null,
  });
}

export function useDownloadJobs() {
  return useQuery({
    queryKey: ["volume", "downloads"],
    queryFn: ({ signal }) => apiGet<{ jobs: DownloadJob[] }>("/api/v1/volume/downloads", signal),
    refetchInterval: (query) =>
      query.state.data?.jobs.some((j) => j.status === "running") ? 3_000 : 15_000,
  });
}

export function cancelVolumeDownload(jobId: string) {
  return apiSend<{ cancelled: boolean }>(`/api/v1/volume/downloads/${jobId}/cancel`, "POST");
}

/* ---------------- 文件夹创建 / 卷内移动 ---------------- */

/** 新建文件夹（后端经 .keep 标记创建真空目录；云端清单有 ~1 分钟延迟） */
export function mkdirVolumeFolder(path: string, volume: string) {
  return apiSend<{ ok: boolean; path: string; note?: string }>("/api/v1/volume/mkdir", "POST", {
    path,
    volume,
  });
}

export interface VolumeDirs {
  ok: boolean;
  dirs: string[];
  error: string | null;
  volume: string;
}

/** 全卷目录清单（移动目标选择用；打开移动弹窗时才拉取） */
export function useVolumeDirs(volume: string, enabled: boolean) {
  return useQuery({
    queryKey: ["volume", "dirs", volume],
    queryFn: ({ signal }) =>
      apiGet<VolumeDirs>(`/api/v1/volume/dirs?volume=${encodeURIComponent(volume)}`, signal),
    enabled,
    staleTime: 10_000,
  });
}

export interface MoveEntry {
  src: string;
  dst: string;
  kind: string; // dir | file
  status: "pending" | "running" | "done" | "error";
  error: string | null;
  files_total: number;
  files_done: number;
}

export interface MoveJob {
  id: string;
  volume: string;
  dest_dir: string;
  status: "running" | "done" | "error";
  error: string | null;
  log_tail: string[];
  created_ms: number;
  finished_ms: number | null;
  entries: MoveEntry[];
}

/** 创建卷内移动作业（云端容器内服务端复制+删源，不占本地带宽） */
export function createVolumeMove(paths: string[], destDir: string, volume: string) {
  return apiSend<{ job: MoveJob }>("/api/v1/volume/move", "POST", {
    paths,
    dest_dir: destDir,
    volume,
  });
}

export function useMoveJobs() {
  return useQuery({
    queryKey: ["volume", "moves"],
    queryFn: ({ signal }) => apiGet<{ jobs: MoveJob[] }>("/api/v1/volume/moves", signal),
    refetchInterval: (query) =>
      query.state.data?.jobs.some((j) => j.status === "running") ? 3_000 : 15_000,
  });
}

export function cancelVolumeMove(jobId: string) {
  return apiSend<{ cancelled: boolean }>(`/api/v1/volume/moves/${jobId}/cancel`, "POST");
}
