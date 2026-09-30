/** 额度与成本 API（M4-1）：仪表盘成本卡片与设置页「护栏与预算」的数据源。 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

export interface CostBucket {
  requests: number;
  gpu_seconds: number;
  est_cost_usd: number;
}

export interface CostDailyPoint extends CostBucket {
  date: string; // YYYY-MM-DD（本地时区日界）
}

export interface BudgetState {
  enabled: boolean;
  monthly_usd: number;
  used_usd: number;
  remaining_usd: number | null;
  exceeded: boolean;
  on_exceed: "fallback_local" | "reject";
}

export interface CostSummary {
  gpu_type: string;
  gpu_hourly_rate_usd: number;
  today: CostBucket;
  month: CostBucket;
  daily: CostDailyPoint[];
  budget: BudgetState;
  note: string;
}

export function useCostSummary() {
  return useQuery({
    queryKey: ["cost-summary"],
    queryFn: ({ signal }) => apiGet<CostSummary>("/api/v1/cost/summary", signal),
    refetchInterval: 30_000,
  });
}

export interface BudgetUpdate {
  monthly_budget_usd?: number;
  on_budget_exceed?: "fallback_local" | "reject";
  gpu_hourly_rate_usd?: number;
}

export function useUpdateBudget() {
  const qc = useQueryClient();
  return async (body: BudgetUpdate) => {
    const updated = await apiSend<CostSummary>("/api/v1/cost/budget", "PUT", body);
    await qc.invalidateQueries({ queryKey: ["cost-summary"] });
    await qc.invalidateQueries({ queryKey: ["config"] }); // 设置页表单初值同步
    return updated;
  };
}
