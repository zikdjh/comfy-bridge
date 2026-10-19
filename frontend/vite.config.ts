import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // 三方库独立分包：业务代码改动不影响 vendor 缓存，首屏只拉必要 vendor
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("echarts") || id.includes("zrender")) return "vendor-echarts";
          if (id.includes("antd") || id.includes("@ant-design") || id.includes("rc-")) return "vendor-antd";
          if (id.includes("react") || id.includes("scheduler")) return "vendor-react";
          return "vendor-misc";
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(path.dirname(fileURLToPath(import.meta.url)), "src"),
    },
  },
  server: {
    port: 15173,
    strictPort: true,
    proxy: {
      // 管理面请求代理到本地桥接器；数据面（ComfyUI 界面）不经过 Vite
      "/api": { target: "http://127.0.0.1:18189", changeOrigin: true },
    },
  },
  preview: {
    // 固定绑定 IPv4 回环，避免 Windows 上 vite 只绑 ::1 导致 127.0.0.1 访问失败
    host: "127.0.0.1",
    port: 4173,
  },
});
