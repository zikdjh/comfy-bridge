import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

export interface LocalWorkflow {
  name: string;
  file: string;
  size_bytes: number;
  mtime: string;
  hash: string;
}

export interface AssetsDbInfo {
  path: string;
  tables?: string[];
  assets_count?: number | null;
  error?: string;
}

export interface LocalWorkflows {
  workflows_dir: string;
  dir_exists: boolean;
  workflows: LocalWorkflow[];
  assets_db: AssetsDbInfo | null;
}

export function useLocalWorkflows() {
  return useQuery({
    queryKey: ["workflows", "local"],
    queryFn: ({ signal }) => apiGet<LocalWorkflows>("/api/v1/workflows/local", signal),
    refetchInterval: 30_000,
  });
}
