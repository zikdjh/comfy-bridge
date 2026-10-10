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

export interface ModalCliFailure {
  category: "not_installed" | "not_logged_in" | "network" | "permission" | "uncertain" | "command";
  label: string;
  message: string;
  retryable: boolean;
  next_action: string | null;
  command: string | null;
  exit_code: number | null;
  ts_ms?: number;
}

export interface ModalCliHealth {
  found: boolean;
  path: string | null;
  version: string | null;
  logged_in: boolean | null;
  latency_ms: number | null;
  app_state: string | null;
  error: ModalCliFailure | null;
  last_failure: ModalCliFailure | null;
  last_success_ms: number | null;
}

export function useModalCliHealth() {
  return useQuery({
    queryKey: ["health", "modal-cli"],
    queryFn: ({ signal }) => apiGet<ModalCliHealth>("/api/v1/health/modal-cli", signal),
    refetchInterval: 30_000,
  });
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
