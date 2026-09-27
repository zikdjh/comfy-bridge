import { Button, Empty } from "antd";

interface Props {
  description: string;
  actionLabel?: string;
  onAction?: () => void;
}

/** 统一空状态引导：说明 + 可选动作按钮 */
export function EmptyGuide({ description, actionLabel, onAction }: Props) {
  return (
    <Empty description={description} style={{ padding: "32px 0" }}>
      {actionLabel && (
        <Button type="primary" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </Empty>
  );
}
