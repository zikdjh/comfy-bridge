import { createContext, createElement, useContext, useEffect, useState } from "react";
import {
  bridgeTokensDark,
  bridgeTokensLight,
} from "../theme";
import type { BridgeTokens } from "../theme";
export type { BridgeTokens } from "../theme";

export type ThemeMode = "light" | "dark";

const STORAGE_KEY = "cb_theme_mode";

function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function readInitialMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* localStorage 不可用时回落系统偏好 */
  }
  return systemPrefersDark() ? "dark" : "light";
}

export interface DarkModeState {
  mode: ThemeMode;
  isDark: boolean;
  setMode: (mode: ThemeMode) => void;
  /** 按当前模式取语义色板（bridgeTokens 的同键暗色/亮色版本） */
  tokens: BridgeTokens;
}

const DarkModeContext = createContext<DarkModeState>({
  mode: "light",
  isDark: false,
  setMode: () => {},
  tokens: bridgeTokensLight,
});

export function DarkModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readInitialMode);

  const setMode = (next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* 忽略 */
    }
  };

  const isDark = mode === "dark";

  // 同步 <html data-theme>，供 CSS 选择器做少量原生样式覆盖；
  // colorScheme 让滚动条与原生表单控件跟随主题，背景兜住首屏与过滚动区域
  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", mode);
    root.style.colorScheme = mode;
    root.style.background = mode === "dark" ? bridgeTokensDark.colors.console.background : "#f5f5f5";
  }, [mode]);

  const tokens = isDark ? bridgeTokensDark : bridgeTokensLight;

  // .ts 文件无 JSX：用 createElement 包 Provider
  return createElement(
    DarkModeContext.Provider,
    { value: { mode, isDark, setMode, tokens } },
    children,
  );
}

export function useDarkMode(): DarkModeState {
  return useContext(DarkModeContext);
}
