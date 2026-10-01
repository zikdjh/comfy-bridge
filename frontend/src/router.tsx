import { createBrowserRouter } from "react-router-dom";
import { Result } from "antd";
import { Shell } from "./components/layout/Shell";
import DashboardPage from "./pages/dashboard";
import RoutingPage from "./pages/routing";
import NodesPage from "./pages/nodes";
import WorkflowsPage from "./pages/workflows";
import ModelsPage from "./pages/models";
import VolumePage from "./pages/volume";
import LogsPage from "./pages/logs";
import MediaPage from "./pages/media";
import CostPage from "./pages/cost";
import SettingsPage from "./pages/settings";

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
        { index: true, element: <DashboardPage /> },
        { path: "routing", element: <RoutingPage /> },
        { path: "nodes", element: <NodesPage /> },
        { path: "workflows", element: <WorkflowsPage /> },
        { path: "models", element: <ModelsPage /> },
        { path: "volume", element: <VolumePage /> },
        { path: "media", element: <MediaPage /> },
        { path: "cost", element: <CostPage /> },
        { path: "logs", element: <LogsPage /> },
        { path: "settings", element: <SettingsPage /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL },
);
