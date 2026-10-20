import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Segmented, Tabs } from "antd";
import { LiveConsole } from "./LiveConsole";
import { ModalConsole } from "./ModalConsole";
import { RequestLogPanel } from "./RequestLogPanel";
import { useMediaQuery } from "../../hooks/useMediaQuery";

type ConsoleLayout = "auto" | "row" | "column";

const LAYOUT_STORAGE_KEY = "console.layout";
/** 视口底部的留白（Content 的 padding-bottom），从总高里扣掉 */
const BOTTOM_PADDING = 16;
/** 高度测量到位前的兜底值（页头 64 + 内容边距 32 + 页签导航 ~46） */
const FALLBACK_CHROME = 158;

export default function LogsPage() {
  const [tab, setTab] = useState("requests");
  const [layout, setLayout] = useState<ConsoleLayout>(
    () => (localStorage.getItem(LAYOUT_STORAGE_KEY) as ConsoleLayout) || "auto",
  );
  // 1280px 桌面加 64px 紧凑侧栏时，每栏约 590px；工具行会在 700px 以下自动紧凑化。
  const wide = useMediaQuery("(min-width: 1280px)");
  const direction = layout === "auto" ? (wide ? "row" : "column") : layout;

  // 实测控制台区顶边的文档坐标（页头+页签导航），反推可用高度，避免硬编码页头尺寸
  const wrapRef = useRef<HTMLDivElement>(null);
  const [chrome, setChrome] = useState(FALLBACK_CHROME);
  useLayoutEffect(() => {
    const measure = () => {
      const el = wrapRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY;
      if (top > 40) setChrome(top);
    };
    measure();
    const timer = window.setTimeout(measure, 300); // 字体加载后页签高度可能微调，补测一次
    window.addEventListener("resize", measure);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", measure);
    };
  }, []);

  const pickLayout = (value: ConsoleLayout) => {
    setLayout(value);
    localStorage.setItem(LAYOUT_STORAGE_KEY, value);
  };

  return (
    <Tabs
      activeKey={tab}
      onChange={setTab}
      tabBarExtraContent={
        tab === "console" && {
          right: (
            <Segmented
              size="small"
              value={layout}
              onChange={(v) => pickLayout(v as ConsoleLayout)}
              options={[
                { value: "auto", label: "自适应" },
                { value: "row", label: "左右" },
                { value: "column", label: "上下" },
              ]}
            />
          ),
        }
      }
      items={[
        {
          key: "requests",
          label: "请求日志",
          children: <RequestLogPanel />,
        },
        {
          key: "console",
          label: "实时控制台",
          children: (
            <div
              ref={wrapRef}
              style={{
                display: "flex",
                flexDirection: direction,
                flexWrap: "nowrap",
                gap: 16,
                alignItems: "stretch",
                height: `calc(100vh - ${chrome + BOTTOM_PADDING}px)`,
                minHeight: direction === "row" ? 560 : 640,
              }}
            >
              <LiveConsole />
              <ModalConsole />
            </div>
          ),
        },
      ]}
    />
  );
}
