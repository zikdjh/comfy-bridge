import type { ThemeConfig } from "antd";

// —— 设计 token：语义色板与间距阶梯，页面组件从这里取色，禁止在 JSX 里写魔法色值 ——
export const bridgeTokens = {
  colors: {
    primary: "#4f5be8",
    accent: "#1a9e6c",
    accentSoft: "#7c86e8",
    accentWarm: "#e8a23f",
    accentRed: "#e8635a",
    accentViolet: "#8a7bff",
    textPrimary: "#1f2430",
    textSecondary: "#8a90a2",
    textMuted: "#5a6272",
    border: "#e4e7ef",
    surfaceSoft: "#f2f3fb",
    surfaceWash: "#f6f7fb",
    surfaceFaint: "#fafafa",
    surfaceHighlight: "#eef0fe",
    muted: "#5a6272",
    danger: "#cf1322",
    warning: "#fa8c16",
    warningDeep: "#d4b106",
    console: {
      background: "#0d1117",
      text: "#c9d1d9",
      dim: "#6e7681",
      red: "#ff7b72",
      yellow: "#e3b341",
      green: "#1a9e6c",
    },
  },
  spacing: {
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 24,
  },
} as const;

/** 本地 ComfyUI 原版界面地址（健康接口不可用时兜底；正常情况下跟随 /api/v1/health 的 local.url） */
export const LOCAL_COMFY_URL = "http://127.0.0.1:8188";

export const bridgeTheme: ThemeConfig = {
  token: {
    colorPrimary: "#4f5be8",
    borderRadius: 8,
    fontSize: 13,
  },
  components: {
    Layout: {
      siderBg: "#ffffff",
      headerBg: "#ffffff",
      headerHeight: 52,
      headerPadding: "0 16px",
    },
    Menu: { itemHeight: 38 },
    Table: { headerBg: "#eef0fe" },
  },
};
