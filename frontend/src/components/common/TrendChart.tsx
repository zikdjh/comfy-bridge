import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

export interface TrendPoint {
  date: string;
  local: number;
  modal: number;
}

/** 生成量趋势（按执行后端双色堆叠柱状），数据来自请求记录聚合。 */
export function TrendChart({ data }: { data: TrendPoint[] }) {
  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      legend: { data: ["本地", "云端"], top: 0 },
      grid: { left: 40, right: 12, top: 30, bottom: 24 },
      xAxis: { type: "category", data: data.map((d) => d.date) },
      yAxis: { type: "value", minInterval: 1, name: "次" },
      series: [
        {
          name: "本地",
          type: "bar",
          stack: "total",
          barMaxWidth: 26,
          itemStyle: { color: "#1a9e6c" },
          data: data.map((d) => d.local),
        },
        {
          name: "云端",
          type: "bar",
          stack: "total",
          barMaxWidth: 26,
          itemStyle: { color: "#4f5be8" },
          data: data.map((d) => d.modal),
        },
      ],
    }),
    [data],
  );

  return <ReactECharts option={option} style={{ height: 240 }} notMerge lazyUpdate />;
}
