/** 设置键级推拉（M2-5）API：两端 comfy.settings.json 的 diff 与按方向执行。 */
import { useQuery } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

export type SyncStatus = "same" | "diff" | "only_local" | "only_cloud";
export type SyncDirection = "push" | "pull";

export interface SettingsDiffRow {
  key: string;
  /** 掩码后的显示值（凭证类键 first5+****+last4）；缺省端为 null */
  local: string | null;
  cloud: string | null;
  status: SyncStatus;
  sensitive: boolean;
  default_direction: SyncDirection | null;
}

export interface SettingsDiff {
  volume: string;
  cloud_path: string;
  local_path: string;
  local_exists: boolean;
  /** false = 云端通道不可用（CLI 未装/凭证/网络），看 cloud_error */
  cloud_available: boolean;
  /** false = 通道可用但文件未生成（首次部署前），按空设置对待 */
  cloud_exists: boolean;
  cloud_error: string | null;
  rows: SettingsDiffRow[];
}

export interface ApplyResult {
  pushed: string[];
  pulled: string[];
  skipped: string[];
  local_written: boolean;
  cloud_written: boolean;
}

export function useSettingsSyncDiff() {
  return useQuery({
    queryKey: ["settings-sync-diff"],
    queryFn: ({ signal }) => apiGet<SettingsDiff>("/api/v1/settings-sync", signal),
  });
}

export function applySettingsSync(directions: Record<string, SyncDirection>) {
  return apiSend<ApplyResult>("/api/v1/settings-sync/apply", "POST", { directions });
}
