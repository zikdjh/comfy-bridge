import type { ReactNode } from "react";
import { Space } from "antd";
import { bridgeTokens as t } from "../../theme";

/**
 * 页面页首：标题 + 可选副标题 + 右侧操作区。
 * 各页面统一用这一行开场，保证扫读节奏一致。
 */
export function PageHeader(props: { title: string; sub?: ReactNode; extra?: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: t.spacing.lg,
        flexWrap: "wrap",
      }}
    >
      <div>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{props.title}</h2>
        {props.sub != null && (
          <div style={{ color: t.colors.textSecondary, fontSize: 12, marginTop: t.spacing.xs }}>
            {props.sub}
          </div>
        )}
      </div>
      {props.extra != null && (
        <Space size={t.spacing.sm} wrap>
          {props.extra}
        </Space>
      )}
    </div>
  );
}
