import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";
import { usePageVisible } from "../hooks/usePageVisible";

export interface LocalLaunch {
  detected: boolean;
  managed_by: string | null;
  command: string;
  pid?: number | null;
  extra_args: string[];
  fallback: boolean;
  control?: LocalProcState;
}

export interface ModalLaunch {
  base_args: string[];
  extra_args: string[];
  volume: string;
  volume_file: string;
  app_state: string | null;
}

export interface LaunchView {
  local: LocalLaunch;
  modal: ModalLaunch;
}

export interface LaunchSaveResult extends LaunchView {
  saved: boolean;
  volume_push: { ok: boolean; detail: string };
}

export function useLaunch() {
  return useQuery({
    queryKey: ["launch"],
    queryFn: ({ signal }) => apiGet<LaunchView>("/api/v1/launch", signal),
    staleTime: 30_000,
  });
}

export function saveLaunch(localExtra: string[], modalExtra: string[]) {
  return apiSend<LaunchSaveResult>("/api/v1/launch", "PUT", {
    local_extra: localExtra,
    modal_extra: modalExtra,
  });
}

/** 本地 ComfyUI 进程状态（桥接管管 / 外部托管 / 启动中 / 未运行） */
export interface LocalProcState {
  port: number;
  comfy_url?: string;
  pid?: number | null;
  running?: boolean;
  owned?: boolean;
  starting?: boolean;
  managed_by?: string | null;
  command?: string | null;
  started_ms?: number | null;
  uptime_ms?: number | null;
  log_tail?: string[];
}

/** 进程控制动作的统一返回 */
export interface ProcResult {
  ok: boolean;
  error?: string;
  detail?: string;
  pid?: number | null;
  port_pid?: number | null;
  seconds?: number;
  ready?: boolean;
  stopped?: boolean;
  conflict?: boolean;
  external?: boolean;
  starting?: boolean;
  stage?: string;
  command?: string;
  log_tail?: string[];
}

/** 云端容器停止结果 */
export interface ModalStopResult {
  ok: boolean;
  running: number;
  stopped: string[];
  failed?: string[];
  detail?: string;
}

/** 本地实例状态（5s 轮询） */
export function useLocalProcState() {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["launch", "local-proc"],
    queryFn: ({ signal }) => apiGet<LocalProcState>("/api/v1/launch/local/status", signal),
    refetchInterval: visible ? 5_000 : false,
    staleTime: 2_000,
  });
}

export const startLocalProc = (takeover: boolean) =>
  apiSend<ProcResult>("/api/v1/launch/local/start", "POST", { takeover, timeout: 180 });

export const stopLocalProc = (force: boolean) =>
  apiSend<ProcResult>("/api/v1/launch/local/stop", "POST", { force });

export const restartLocalProc = (takeover: boolean) =>
  apiSend<ProcResult>("/api/v1/launch/local/restart", "POST", { takeover, timeout: 180 });

/** 停止云端运行中的容器（新参数下次冷启动生效） */
export const stopModalContainers = () =>
  apiSend<ModalStopResult>("/api/v1/launch/modal/stop", "POST");

/* ---------------- 云端容器清单（M4-2 面板；后端 45s 进程内缓存） ---------------- */

export interface ModalContainerRow {
  id: string;
  started_at?: string;
  cpu?: string;
  memory?: string;
  gpu?: string;
  region?: string;
}

export interface ModalContainers {
  ok: boolean;
  count: number | null;
  containers: ModalContainerRow[];
  unknown_rows?: number;
  detail: string;
}

export function useModalContainers() {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["launch", "modal-containers"],
    queryFn: ({ signal }) => apiGet<ModalContainers>("/api/v1/launch/modal/containers", signal),
    staleTime: 45_000,
    refetchInterval: visible ? 60_000 : false,
  });
}
