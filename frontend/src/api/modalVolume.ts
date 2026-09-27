import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

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

export function useVolumeMeta() {
  return useQuery({
    queryKey: ["volume-meta"],
    queryFn: ({ signal }) => apiGet<VolumeMeta>("/api/v1/modal-volume", signal),
  });
}

export function useVolumeOverview(category: string) {
  return useQuery({
    queryKey: ["volume-overview", category],
    queryFn: ({ signal }) =>
      apiGet<OverviewResponse>(
        `/api/v1/modal-volume/overview?category=${encodeURIComponent(category)}`,
        signal,
      ),
    refetchInterval: 15_000,
  });
}

export function useSyncJobs() {
  return useQuery({
    queryKey: ["sync-jobs"],
    queryFn: ({ signal }) =>
      apiGet<{ jobs: SyncJobDto[] }>("/api/v1/modal-volume/jobs", signal),
    // 有进行中的作业时 2 秒快轮询，空闲时 10 秒慢轮询
    refetchInterval: (query) => {
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
