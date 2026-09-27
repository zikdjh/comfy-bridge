import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

export interface RuleStep {
  rule: string;
  matched: boolean;
  detail: string;
  target: string | null;
}

export type GenType = "image" | "video" | "none";
export type RequestStatus = "queued" | "running" | "done" | "error" | "cancelled" | "blocked";

export interface BridgeRequest {
  id: number;
  prompt_id: string;
  created_at: string;
  created_ms: number;
  backend: "local" | "modal";
  final_backend: "local" | "modal" | "";
  gen_type: GenType;
  workflow_name: string | null;
  graph_hash: string | null;
  graph_file: string | null;
  client_id: string | null;
  status: RequestStatus;
  rule_trace: RuleStep[] | null;
  error: string | null;
  started_ms: number | null;
  finished_ms: number | null;
  duration_ms?: number;
}

export function useRequests(backend = "all", status = "all", realtime = true, limit = 200) {
  return useQuery({
    queryKey: ["requests", backend, status, limit],
    queryFn: ({ signal }) =>
      apiGet<{ total: number; items: BridgeRequest[] }>(
        `/api/v1/requests?backend=${backend}&status=${status}&limit=${limit}`,
        signal,
      ),
    refetchInterval: realtime ? 5_000 : false,
  });
}
