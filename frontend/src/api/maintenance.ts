import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorFromResponse, apiGet, apiSend } from "./client";

export interface BackupInfo {
  name: string;
  size: number;
  created_ms: number;
  reason: string;
}

export interface MaintenanceStatus {
  retention: { days: number };
  backups: BackupInfo[];
  restore_pending: boolean;
  last_restore: { applied_ms?: number; source?: string } | null;
  data_dir: string;
  backup_dir: string;
  release: ReleaseStatus;
}

export interface ReleaseHistoryItem {
  version: string;
  previous_version: string | null;
  installed_ms: number;
  upgrade: boolean;
  source: string | null;
}

export interface ReleaseStatus {
  version: string | null;
  previous_version: string | null;
  installed_at_ms: number | null;
  version_file: string | null;
  history: ReleaseHistoryItem[];
  upgrade_backups: {
    version: string;
    previous_version: string;
    name: string;
    created_ms: number;
  }[];
  last_self_check: {
    ok: boolean;
    version: string | null;
    checked_at_ms: number;
    checks: { name: string; ok: boolean; detail: string }[];
    error: string | null;
  } | null;
  packaged: boolean;
}

export function useMaintenance() {
  return useQuery({
    queryKey: ["maintenance"],
    queryFn: ({ signal }) => apiGet<MaintenanceStatus>("/api/v1/maintenance", signal),
  });
}

export function useSaveRetention() {
  const qc = useQueryClient();
  return async (days: number) => {
    const result = await apiSend<{ days: number }>("/api/v1/maintenance/retention", "PUT", { days });
    await qc.invalidateQueries({ queryKey: ["maintenance"] });
    return result;
  };
}

export function createBackup(reason = "manual") {
  return apiSend<{ name: string; size: number; path: string; contains_secrets: boolean }>(
    "/api/v1/maintenance/backup",
    "POST",
    { reason },
  );
}

export function cleanupRetention() {
  return apiSend<{
    days: number;
    requests_removed: number;
    jobs_removed: number;
    graphs_removed: number;
  }>("/api/v1/maintenance/cleanup", "POST", {});
}

export async function stageRestore(file: File) {
  const res = await fetch("/api/v1/maintenance/restore", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/zip",
    },
    body: file,
  });
  if (!res.ok) {
    throw await apiErrorFromResponse(res);
  }
  return (await res.json()) as { name: string; pending: boolean; applies_on: string };
}
