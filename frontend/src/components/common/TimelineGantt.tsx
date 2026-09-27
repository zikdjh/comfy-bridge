import { Tooltip } from "antd";

export interface GanttPhase {
  label: string;
  ms: number;
}

const COLORS = ["#4f5be8", "#7c86e8", "#1a9e6c", "#e8a23f", "#e8635a", "#8a7bff"];

function fmt(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** 六阶段耗时甘特（纯 CSS 条形，悬停显毫秒）——见组件选型 §2.5 */
export function TimelineGantt({ phases }: { phases: GanttPhase[] }) {
  const total = phases.reduce((s, p) => s + p.ms, 0) || 1;
  return (
    <div>
      <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", background: "#f0f2fa" }}>
        {phases.map((p, i) => (
          <Tooltip key={p.label} title={`${p.label} · ${fmt(p.ms)}`}>
            <div
              style={{
                width: `${(p.ms / total) * 100}%`,
                minWidth: 4,
                background: COLORS[i % COLORS.length],
              }}
            />
          </Tooltip>
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", marginTop: 8, fontSize: 12, color: "#5a6272" }}>
        {phases.map((p, i) => (
          <span key={p.label}>
            <span style={{ color: COLORS[i % COLORS.length] }}>■</span> {p.label} {fmt(p.ms)}
          </span>
        ))}
      </div>
    </div>
  );
}
