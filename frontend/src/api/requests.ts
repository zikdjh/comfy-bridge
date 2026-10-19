import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";
import { usePageVisible } from "../hooks/usePageVisible";

export interface RuleStep {
  rule: string;
  matched: boolean;
  detail: string;
  target: string | null;
}

export type GenType = "image" | "video" | "none";
export type RequestStatus = "queued" | "running" | "done" | "error" | "cancelled" | "blocked" | "preflight";

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
  /** 云端请求的成本记账（M4-1）：提交成功才入账；本地请求为 null */
  gpu_seconds?: number | null;
  est_cost_usd?: number | null;
}

export function useRequests(backend = "all", status = "all", realtime = true, limit = 200) {
  const visible = usePageVisible();
  return useQuery({
    queryKey: ["requests", backend, status, limit],
    queryFn: ({ signal }) =>
      apiGet<{ total: number; items: BridgeRequest[] }>(
        `/api/v1/requests?backend=${backend}&status=${status}&limit=${limit}`,
        signal,
      ),
    // 页面不可见时停轮询，回到前台立即恢复
    refetchInterval: realtime && visible ? 5_000 : false,
  });
}
