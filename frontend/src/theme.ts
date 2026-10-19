import type { ThemeConfig } from "antd";
import { theme as antdTheme } from "antd";

// —— 暗色语义色板：与亮色同键，值按 dark algorithm 适配 ——
const darkColors = {
  primary: "#6c7bf5",
  accent: "#2fd08c",
  accentSoft: "#8f9af0",
  accentWarm: "#f0a53f",
  accentRed: "#f07a6e",
  accentViolet: "#a190ff",
  textPrimary: "#e6e9f0",
  textSecondary: "#8b93a8",
  textMuted: "#6b7280",
  surface: "#141824",
  border: "#30363d",
  surfaceSoft: "#161b27",
  surfaceWash: "#1a1f2e",
  surfaceFaint: "#141824",
  surfaceHighlight: "#1f2438",
  muted: "#6b7280",
  danger: "#f85149",
  warning: "#f0a53f",
  warningDeep: "#d4b106",
  console: {
    background: "#0d1117",
    text: "#c9d1d9",
    dim: "#6e7681",
    red: "#ff7b72",
    yellow: "#e3b341",
    green: "#2fd08c",
  },
} as const;

// —— 设计 token：语义色板与间距阶梯，页面组件从这里取色，禁止在 JSX 里写魔法色值 ——
// 注：spacing 结构亮色/暗色完全一致，类型上抽取共用；改间距只需动一处
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
    surface: "#ffffff",
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
};

export type BridgeTokens = typeof bridgeTokens;

/** 暗色模式下的语义色板：与 bridgeTokens 同键，组件用 useThemeTokens 按模式取色 */
export const bridgeTokensDark: BridgeTokens = {
  colors: darkColors,
  spacing: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 },
};

/** 亮色模式下的语义色板（即默认 bridgeTokens） */
export const bridgeTokensLight: BridgeTokens = bridgeTokens;

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

/** 暗色 AntD 主题：token 走 dark 语义色板，组件背景/边框覆盖 */
export const bridgeThemeDark: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: darkColors.primary,
    borderRadius: 8,
    fontSize: 13,
    colorBgBase: "#0d1117",
    colorTextBase: darkColors.textPrimary,
    colorBorder: darkColors.border,
    colorBorderSecondary: darkColors.border,
  },
  components: {
    Layout: {
      siderBg: "#0d1117",
      headerBg: "#0d1117",
      headerHeight: 52,
      headerPadding: "0 16px",
      bodyBg: "#0d1117",
    },
    Menu: {
      itemHeight: 38,
      darkItemBg: "#0d1117",
      darkSubMenuItemBg: "#0d1117",
      darkItemColor: darkColors.textSecondary,
      darkItemHoverColor: darkColors.textPrimary,
      darkItemSelectedBg: darkColors.surfaceHighlight,
    },
    Table: { headerBg: darkColors.surfaceHighlight },
    Card: {
      colorBgContainer: darkColors.surfaceSoft,
    },
  },
};
