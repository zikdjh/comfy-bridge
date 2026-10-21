import { useId, useState, type ReactNode } from "react";
import { Button, Popover } from "antd";
import { FilterOutlined } from "@ant-design/icons";

/** 低频筛选入口：默认收起，按钮保留当前条件摘要。 */
export function AdvancedFilters({ children, summary }: { children: ReactNode; summary?: string }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const label = summary ? `高级筛选，当前条件：${summary}` : "高级筛选";

  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={setOpen}
      content={
        <div
          id={panelId}
          role="group"
          aria-label="高级筛选选项"
          style={{ minWidth: 220, padding: "4px 2px" }}
          onKeyDown={(event) => {
            if (event.key === "Escape") setOpen(false);
          }}
        >
          {children}
        </div>
      }
    >
      <Button
        size="small"
        icon={<FilterOutlined />}
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
      >
        高级筛选{summary ? ` · ${summary}` : ""}
      </Button>
    </Popover>
  );
}
