import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "./client";

/* ---------------- 类型（与后端 routing/engine.py 对应） ---------------- */

export interface RuleRow {
  id: string;
  name: string;
  kind: string; // workflow_tag | gen_type | min_pixels
  params: Record<string, unknown>;
  target: string; // local | modal
  enabled: boolean;
}

export interface RuleKindInfo {
  label: string;
  param_key: string;
  param_label: string;
}

export interface RoutingRulesResp {
  rules: RuleRow[];
  kinds: Record<string, RuleKindInfo>;
  suggest: Record<string, { kind: string; params: Record<string, unknown>; target: string; name: string }>;
}

/* ---------------- 规则链 CRUD（M3-1，集合级整链替换） ---------------- */

export function useRoutingRules() {
  return useQuery({
    queryKey: ["routing", "rules"],
    queryFn: ({ signal }) => apiGet<RoutingRulesResp>("/api/v1/routing/rules", signal),
  });
}

export function useSaveRoutingRules() {
  const qc = useQueryClient();
  return async (rules: RuleRow[]) => {
    const res = await apiSend<RoutingRulesResp>("/api/v1/routing/rules", "PUT", { rules });
    await qc.invalidateQueries({ queryKey: ["routing", "rules"] });
    return res;
  };
}
