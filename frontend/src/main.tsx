import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { bridgeTheme } from "./theme";

dayjs.locale("zh-cn");

const queryClient = new QueryClient({
  defaultOptions: {
    // staleTime 15s：页面导航往返命中缓存不重复请求；失效/轮询/refetch 仍照常触发
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={bridgeTheme}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);
