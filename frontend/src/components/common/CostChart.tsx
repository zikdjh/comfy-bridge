import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useDarkMode } from "../../hooks/useDarkMode";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

export interface CostPoint {
  date: string; // 展示用（如 09-30）
  est_cost_usd: number;
  requests?: number;
}

/** 云端估算成本逐日柱状（M4-1）；单序列蓝色柱，悬浮显示金额与请求数。 */
export function CostChart({ data }: { data: CostPoint[] }) {
  const { tokens } = useDarkMode();
  const option = useMemo(
    () => ({
      tooltip: {
        trigger: "axis",
        valueFormatter: (v: number) => `$${Number(v).toFixed(4)}`,
      },
      grid: { left: 56, right: 12, top: 16, bottom: 24 },
      xAxis: { type: "category", data: data.map((d) => d.date) },
      yAxis: {
        type: "value",
        name: "$",
        axisLabel: { formatter: (v: number) => (v >= 1 ? v.toFixed(1) : v.toFixed(2)) },
      },
      series: [
        {
          name: "云端估算成本",
          type: "bar",
          barMaxWidth: 26,
          itemStyle: { color: tokens.colors.primary },
          data: data.map((d) => d.est_cost_usd),
        },
      ],
    }),
    [data, tokens],
  );

  return <ReactECharts option={option} style={{ height: 220 }} notMerge lazyUpdate />;
}
