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

export interface ModalBilling {
  ok: boolean;
  error?: string;
  period_start?: string; // UTC 计费窗口起（YYYY-MM-DD）
  period_end?: string;
  metered_cost_usd?: number | null; // 本期计量消耗（容器/应用/卷等，Modal 账单口径）
  billed_cost_usd?: number | null; // 积分抵扣后实际计费；> 0 即赠金/额度已耗尽
  credits_applied_usd?: number | null; // 本期积分抵扣（正数）
  plan_cost_usd?: number | null; // 套餐月费（付费计划）
  breakdown?: Record<string, number>;
}

export interface CostSummary {
  gpu_type: string;
  gpu_hourly_rate_usd: number;
  today: CostBucket;
  month: CostBucket;
  daily: CostDailyPoint[];
  budget: BudgetState;
  note: string;
  modal?: ModalBilling | null;
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

// —— 报表 v2（规划 §8.3）：按工作流/模型聚合 + 逐项账单对账 + CSV 导出 ——

export interface CostReportRow extends CostBucket {
  key: string; // 维度值：工作流名（未命名）或主模型文件名（未记录）
  share: number; // 占总额比例 0~1
}

export interface CostReport {
  by: "workflow" | "model";
  days: number;
  rows: CostReportRow[];
  total_est_cost_usd: number;
}

export function useCostReport(by: "workflow" | "model", days: number) {
  return useQuery({
    queryKey: ["cost-report", by, days],
    queryFn: ({ signal }) =>
      apiGet<CostReport>(`/api/v1/cost/report?by=${by}&days=${days}`, signal),
  });
}

export interface ReconciliationDailyPoint {
  date: string; // YYYY-MM-DD（UTC 日界，与 Modal 账单口径一致）
  local_requests: number;
  local_usd: number;
  modal_gpu_usd: number;
  modal_total_usd: number;
}

export interface Reconciliation {
  cycle: string;
  local: CostBucket;
  modal: {
    ok: boolean;
    error?: string;
    gpu_usd?: number; // GPU 型号资源行的真实计量（与本地估算同口径）
    total_usd?: number; // 全资源计量（含 CPU/内存/卷等）
    resources?: Record<string, number>;
    apps?: { name: string; usd: number }[];
  };
  daily: ReconciliationDailyPoint[];
  ratio_gpu_vs_est: number | null; // Modal GPU ÷ 本地估算；持续偏离 1 说明单价需校准
  ratio_warn_factor: number; // 比例告警阈值（1/factor ~ factor 之外告警）
  diverged: boolean;
  divergence_message: string | null;
  note: string;
}

export function useCostReconciliation(cycle: string) {
  return useQuery({
    queryKey: ["cost-reconciliation", cycle],
    queryFn: ({ signal }) =>
      apiGet<Reconciliation>(`/api/v1/cost/reconciliation?cycle=${cycle}`, signal),
  });
}

/** CSV 导出直链（Content-Disposition 附件下载；同源相对路径，dev 经 vite 代理）。 */
export function costExportUrl(scope: "entries" | "workflow" | "model", days: number): string {
  return `/api/v1/cost/export?scope=${scope}&days=${days}`;
}
