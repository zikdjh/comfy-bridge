import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

export interface LocalHealth {
  url: string;
  online: boolean;
  version: string | null;
  latency_ms: number | null;
  python_version: string | null;
  gpu: string | null;
  error: string | null;
}

export interface ModalHealth {
  online: boolean;
  reason?: string;
  cloud_index_files?: number;
  app_state?: string | null;
  web_url?: string | null;
}

export interface HealthResponse {
  local: LocalHealth;
  modal: ModalHealth;
  bridge?: { url: string; listen: string };
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => apiGet<HealthResponse>("/api/v1/health", signal),
    refetchInterval: 10_000,
  });
}

/** 预热云端容器：阻塞直到就绪（冷启动可达数十秒），返回耗时。 */
export function warmupModal() {
  return apiSend<{ ok: boolean; seconds?: number; error?: string }>("/api/v1/modal/warmup", "POST");
}
