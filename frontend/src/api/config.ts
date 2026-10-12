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
  gpu_hourly_rate_usd: number;
  monthly_budget_usd: number;
  on_budget_exceed: string;
  max_cloud_concurrent: number;
  preflight: string;
  privacy_confirm: boolean;
  object_info_merge: string;
}

/** Modal 认证与执行参数：secret 只回显脱敏值，明文仅保存时提交一次 */
export interface ModalConfigDto {
  token_id: string;
  environment: string;
  has_secret: boolean;
  secret_masked: string;
  proxy_token_id: string;
  has_proxy_secret: boolean;
  proxy_secret_masked: string;
  volume: string;
  task_timeout: number;
  runner_enabled: boolean;
  web_url: string;
  app_name: string;
  extra_launch_args: string[];
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
      proxy_token_id?: string;
      proxy_token_secret?: string | null;
      volume?: string;
      task_timeout?: number;
      web_url?: string;
    };
  }) => {
    const updated = await apiSend<ConfigResponse>("/api/v1/config", "PUT", body);
    await qc.invalidateQueries({ queryKey: ["config"] });
    return updated;
  };
}

/** 账号档案（多账号维护）：secret 只回显脱敏值 */
export interface AccountProfileDto {
  name: string;
  token_id: string;
  has_secret: boolean;
  secret_masked: string;
  environment: string;
  proxy_token_id: string;
  has_proxy_secret: boolean;
  proxy_secret_masked: string;
  volume: string;
  app_name: string;
  web_url: string;
  task_timeout: number;
  extra_launch_args: string[];
  active: boolean;
}

export interface AccountsResponse {
  accounts: AccountProfileDto[];
  active: string | null;
}

export function useAccounts() {
  return useQuery({
    queryKey: ["config", "accounts"],
    queryFn: ({ signal }) => apiGet<AccountsResponse>("/api/v1/config/accounts", signal),
  });
}

export interface AccountSaveInput {
  name: string;
  token_id?: string;
  token_secret?: string;
  environment?: string;
  proxy_token_id?: string;
  proxy_token_secret?: string;
  volume?: string;
  app_name?: string;
  web_url?: string;
  task_timeout?: number;
  extra_launch_args?: string[];
}

async function invalidateAccountViews(qc: ReturnType<typeof useQueryClient>) {
  await Promise.all([
    qc.invalidateQueries({ queryKey: ["config", "accounts"] }),
    qc.invalidateQueries({ queryKey: ["config"] }),
  ]);
}

export function useSaveAccount() {
  const qc = useQueryClient();
  return async (input: AccountSaveInput) => {
    const out = await apiSend<AccountsResponse>("/api/v1/config/accounts", "PUT", input);
    await invalidateAccountViews(qc);
    return out;
  };
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return async (name: string) => {
    const out = await apiSend<AccountsResponse>(`/api/v1/config/accounts/${encodeURIComponent(name)}`, "DELETE");
    await invalidateAccountViews(qc);
    return out;
  };
}

export function useActivateAccount() {
  const qc = useQueryClient();
  return async (name: string, force = false) => {
    const out = await apiSend<AccountsResponse & { modal: ModalConfigDto }>(
      `/api/v1/config/accounts/${encodeURIComponent(name)}/activate`,
      "POST",
      { force },
    );
    // 激活改变通道判定与全部云端视图：配置、账号、健康/通道状态全部失效重拉
    await qc.invalidateQueries();
    return out;
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
