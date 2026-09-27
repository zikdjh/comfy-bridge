import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

export interface LocalConfigDto {
  comfy_url: string;
  comfy_root: string;
  output_dir: string;
  models_dirs: Record<string, string>;
}

export interface BridgeConfigDto {
  listen: string;
  default_backend: string;
  video_backend: string;
  large_side_pixels: number;
  on_modal_unavailable: string;
}

/** Modal 认证与执行参数：secret 只回显脱敏值，明文仅保存时提交一次 */
export interface ModalConfigDto {
  token_id: string;
  environment: string;
  has_secret: boolean;
  secret_masked: string;
  volume: string;
  task_timeout: number;
}

export interface ConfigResponse {
  config_path: string;
  local: LocalConfigDto;
  bridge: BridgeConfigDto;
  modal: ModalConfigDto;
}

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: ({ signal }) => apiGet<ConfigResponse>("/api/v1/config", signal),
  });
}

export function useSaveConfig() {
  const qc = useQueryClient();
  return async (body: {
    local?: Partial<LocalConfigDto>;
    bridge?: Partial<BridgeConfigDto>;
    modal?: {
      token_id?: string;
      environment?: string;
      token_secret?: string | null;
      volume?: string;
      task_timeout?: number;
    };
  }) => {
    const updated = await apiSend<ConfigResponse>("/api/v1/config", "PUT", body);
    await qc.invalidateQueries({ queryKey: ["config"] });
    return updated;
  };
}

/** 本机 ComfyUI 扫描（后端单槽后台线程；固定/可移动磁盘，排除 C 盘） */
export interface ComfyFindDto {
  path: string;
  drive: string;
  version: string | null;
  has_venv: boolean;
  complete: boolean;
  optional_missing: string[];
}

/** ComfyUI 根目录完整性校验报告：必需项缺失 = 不可用 */
export interface ComfyInspectDto {
  path: string;
  exists: boolean;
  version: string | null;
  has_venv: boolean;
  required_missing: string[];
  optional_missing: string[];
  valid: boolean;
}

export interface ComfyScanStateDto {
  status: "idle" | "running" | "done" | "error";
  drives: string[];
  excluded: string[];
  current_drive: string | null;
  scanned_dirs: number;
  found: ComfyFindDto[];
  truncated: boolean;
  cancelled: boolean;
  error: string | null;
  started_ms: number | null;
  finished_ms: number | null;
}

export const fetchComfyScan = () => apiGet<ComfyScanStateDto>("/api/v1/config/scan-comfy");
export const startComfyScan = () => apiSend<ComfyScanStateDto>("/api/v1/config/scan-comfy", "POST", {});
export const cancelComfyScan = () => apiSend<ComfyScanStateDto>("/api/v1/config/scan-comfy/cancel", "POST", {});
export const validateComfyRoot = (path: string) =>
  apiSend<ComfyInspectDto>("/api/v1/config/validate-comfy", "POST", { path });
