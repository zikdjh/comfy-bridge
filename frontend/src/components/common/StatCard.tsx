import type { ReactNode } from "react";
import { Card, Statistic } from "antd";
import { bridgeTokens as t } from "../../theme";

/** 数字大号展示（Statistic 的 valueRender 专用），等宽数字防止跳动 */
export function BigValue({ children }: { children: ReactNode }) {
  return <span style={{ fontSize: 24, fontVariantNumeric: "tabular-nums" }}>{children}</span>;
}

/**
 * 统计卡：图标 + 标题 + 主指标 + 脚注说明。
 * 用于仪表盘等需要一排同类指标的页面；图标统一使用次要色。
 */
export function StatCard(props: {
  icon?: ReactNode;
  title: ReactNode;
  value: ReactNode;
  footer?: ReactNode;
  extra?: ReactNode;
}) {
  const title = props.icon ? (
    <span>
      <span style={{ color: t.colors.textSecondary, marginRight: 6 }}>{props.icon}</span>
      {props.title}
    </span>
  ) : (
    props.title
  );
  return (
    <Card size="small" extra={props.extra}>
      <Statistic title={title} valueRender={() => <>{props.value}</>} />
      {props.footer != null && (
        <div style={{ color: t.colors.textSecondary, fontSize: 12 }}>{props.footer}</div>
      )}
    </Card>
  );
}
