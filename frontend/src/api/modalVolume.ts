import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import { usePageVisible } from "../hooks/usePageVisible";

export interface VolumeMeta {
  volume: string;
  credentials_ok: boolean;
  cli_found: boolean;
  categories: string[];
}

export interface SyncRow {
  name: string;
  status: "only_local" | "only_cloud" | "both";
  local_size?: string | null;
  cloud_size?: string | null;
}

export interface OverviewResponse {
  ok: boolean;
  reason?: string;
  cloud_error?: string | null;
  category: string;
  local_dir: string;
  volume: string;
  local_count?: number;
  cloud_count?: number;
  rows: SyncRow[];
}

export interface SyncJobDto {
  id: string;
  direction: "push" | "pull";
  category: string;
  status: "running" | "done" | "error";
  error?: string | null;
  created_ms: number;
  finished_ms?: number | null;
  total: number;
  done: number;
  failed: number;
  files: { name: string; status: string; error?: string | null }[];
}

export interface UploadSession {
  id: string;
  filename: string;
  volume: string;
  category: string | null;
  dest_dir: string | null;
  remote_dir: string;
  remote_path: string;
  size: number;
  md5: string;
  actual_md5: string | null;
  chunk_size: number;
  total_chunks: number;
  uploaded_chunks: number[];
  missing_chunks: number[];
  missing_count: number;
  uploaded_bytes: number;
  ready: boolean;
  status: "receiving" | "verifying" | "pushing" | "done" | "error";
  phase: "not_started" | "in_progress" | "done" | "error";
  error_stage: "verify" | "push" | null;
  error: string | null;
  created_ms: number;
  updated_ms: number;
  finished_ms: number | null;
}

export function useVolumeMeta() {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["volume-meta"],
    queryFn: ({ signal }) => apiGet<VolumeMeta>("/api/v1/modal-volume", signal),
    // 与 overview 同档慢轮询，页面隐藏时停止，避免后台 Modal 卷接口撞限流
    refetchInterval: visible ? 15_000 : false,
  });
}

export function useVolumeOverview(category: string) {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["volume-overview", category],
    queryFn: ({ signal }) =>
      apiGet<OverviewResponse>(
        `/api/v1/modal-volume/overview?category=${encodeURIComponent(category)}`,
        signal,
      ),
    // 15s 慢轮询但页面隐藏时停止，避免后台 Modal 卷列表撞限流
    refetchInterval: visible ? 15_000 : false,
  });
}

export function useSyncJobs() {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["sync-jobs"],
    queryFn: ({ signal }) =>
      apiGet<{ jobs: SyncJobDto[] }>("/api/v1/modal-volume/jobs", signal),
    // 有进行中的作业时 2 秒快轮询，空闲时 10 秒慢轮询
    refetchInterval: (query) => {
      if (!visible) return false;
      const list = query.state.data?.jobs;
      return list?.some((j) => j.status === "running") ? 2_000 : 10_000;
    },
  });
}

export function useStartSync() {
  const qc = useQueryClient();
  return async (direction: "push" | "pull", category: string, names: string[]) => {
    const result = await apiSend<{ job: SyncJobDto }>(
      "/api/v1/modal-volume/sync",
      "POST",
      { direction, category, names },
    );
    await qc.invalidateQueries({ queryKey: ["sync-jobs"] });
    return result.job;
  };
}

export function useCancelJob() {
  const qc = useQueryClient();
  return async (jobId: string) => {
    await apiSend(`/api/v1/modal-volume/jobs/${jobId}/cancel`, "POST");
    await qc.invalidateQueries({ queryKey: ["sync-jobs"] });
  };
}

export function initUpload(input: {
  filename: string;
  size: number;
  md5: string;
  category?: string;
  volume?: string;
}) {
  return apiSend<{ upload: UploadSession }>("/api/v1/uploads/init", "POST", input);
}

export function useUploadSessions() {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["uploads"],
    queryFn: ({ signal }) =>
      apiGet<{ uploads: UploadSession[] }>("/api/v1/uploads", signal),
    refetchInterval: (query) => {
      if (!visible) return false;
      return query.state.data?.uploads.some((u) =>
        ["receiving", "verifying", "pushing"].includes(u.status),
      )
        ? 2_000
        : 10_000;
    },
  });
}

export function getUpload(uploadId: string) {
  return apiGet<{ upload: UploadSession }>(`/api/v1/uploads/${uploadId}`);
}

export function putUploadChunk(
  uploadId: string,
  index: number,
  data: Blob,
  chunkMd5: string,
) {
  return fetch(`/api/v1/uploads/${uploadId}/chunks/${index}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/octet-stream",
      "x-chunk-md5": chunkMd5,
      Accept: "application/json",
    },
    body: data,
  }).then(async (res) => {
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail.slice(0, 200)}` : ""}`);
    }
    return (await res.json()) as { upload: UploadSession };
  });
}

export function completeUpload(uploadId: string) {
  return apiSend<{ upload: UploadSession }>(
    `/api/v1/uploads/${uploadId}/complete`,
    "POST",
  );
}

export function deleteUpload(uploadId: string) {
  return apiSend<{ deleted: boolean }>(`/api/v1/uploads/${uploadId}`, "DELETE");
}

export function pruneNotStartedUploads(olderThanMs = 0) {
  return apiSend<{ deleted: number }>(
    `/api/v1/uploads/prune?older_than_ms=${olderThanMs}`,
    "POST",
  );
}
