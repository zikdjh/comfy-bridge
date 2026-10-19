import { Tooltip } from "antd";
import { bridgeTokens } from "../../theme";

export interface GanttPhase {
  label: string;
  ms: number;
}

const COLORS = [
  bridgeTokens.colors.primary,
  bridgeTokens.colors.accentSoft,
  bridgeTokens.colors.accent,
  bridgeTokens.colors.accentWarm,
  bridgeTokens.colors.accentRed,
  bridgeTokens.colors.accentViolet,
];

function fmt(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** 六阶段耗时甘特（纯 CSS 条形，悬停显毫秒）——见组件选型 §2.5 */
export function TimelineGantt({ phases }: { phases: GanttPhase[] }) {
  const total = phases.reduce((s, p) => s + p.ms, 0) || 1;
  return (
    <div>
      <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", background: bridgeTokens.colors.surfaceSoft }}>
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
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", marginTop: 8, fontSize: 12, color: bridgeTokens.colors.textMuted }}>
        {phases.map((p, i) => (
          <span key={p.label}>
            <span style={{ color: COLORS[i % COLORS.length] }}>■</span> {p.label} {fmt(p.ms)}
          </span>
        ))}
      </div>
    </div>
  );
}
