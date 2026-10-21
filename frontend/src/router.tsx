import { createBrowserRouter } from "react-router-dom";
import { Result } from "antd";
import { Shell } from "./components/layout/Shell";
import { lazy, Suspense } from "react";

// 路由级代码分割：所有页面懒加载按需拉取，减小首屏 JS 体积；
// 页面切换由 Suspense 兜底（本地 chunk 加载毫秒级，不闪加载态）。
const DashboardPage = lazy(() => import("./pages/dashboard"));
const RoutingPage = lazy(() => import("./pages/routing"));
const NodesPage = lazy(() => import("./pages/nodes"));
const WorkflowsPage = lazy(() => import("./pages/workflows"));
const ModelsPage = lazy(() => import("./pages/models"));
const VolumePage = lazy(() => import("./pages/volume"));
const LogsPage = lazy(() => import("./pages/logs"));
const MediaPage = lazy(() => import("./pages/media"));
const CostPage = lazy(() => import("./pages/cost"));
const SettingsPage = lazy(() => import("./pages/settings"));
const StatusPage = lazy(() => import("./pages/status"));

const PREFETCH_PAGES = [
  () => import("./pages/routing"),
  () => import("./pages/nodes"),
  () => import("./pages/workflows"),
  () => import("./pages/models"),
  () => import("./pages/volume"),
  () => import("./pages/logs"),
  () => import("./pages/media"),
  () => import("./pages/cost"),
  () => import("./pages/settings"),
  () => import("./pages/status"),
];

if (typeof window !== "undefined") {
  const prefetch = () => {
    for (const load of PREFETCH_PAGES) void load().catch(() => undefined);
  };
  const idle = window.requestIdleCallback as
    | ((callback: () => void, options?: { timeout?: number }) => number)
    | undefined;
  if (idle) {
    idle(prefetch, { timeout: 2_000 });
  } else {
    window.setTimeout(prefetch, 300);
  }
}

function NotFoundPage() {
  return (
    <Result
      status="404"
      title="404"
      subTitle="页面不存在"
    />
  );
}

// basename 跟随 vite base：dev 与普通构建为 "/"，打包嵌入构建为 /bridge/（后端挂载前缀）
export const router = createBrowserRouter(
  [
    {
      path: "/",
      element: <Shell />,
      children: [
        { index: true, element: <Suspense fallback={null}><DashboardPage /></Suspense> },
        { path: "routing", element: <Suspense fallback={null}><RoutingPage /></Suspense> },
        { path: "nodes", element: <Suspense fallback={null}><NodesPage /></Suspense> },
        { path: "workflows", element: <Suspense fallback={null}><WorkflowsPage /></Suspense> },
        { path: "models", element: <Suspense fallback={null}><ModelsPage /></Suspense> },
        { path: "volume", element: <Suspense fallback={null}><VolumePage /></Suspense> },
        { path: "media", element: <Suspense fallback={null}><MediaPage /></Suspense> },
        { path: "cost", element: <Suspense fallback={null}><CostPage /></Suspense> },
        { path: "logs", element: <Suspense fallback={null}><LogsPage /></Suspense> },
        { path: "status", element: <Suspense fallback={null}><StatusPage /></Suspense> },
        { path: "settings", element: <Suspense fallback={null}><SettingsPage /></Suspense> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL },
);
