import { createContext, createElement, useContext, useEffect, useState } from "react";
import {
  bridgeTokensDark,
  bridgeTokensLight,
} from "../theme";
import type { BridgeTokens } from "../theme";
export type { BridgeTokens } from "../theme";

export type ThemeMode = "light" | "dark" | "system";

/** 实际生效的主题：mode=system 时由系统偏好解析得到 */
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "cb_theme_mode";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia(DARK_QUERY).matches;
}

function readStoredMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch {
    /* localStorage 不可用时回落「跟随系统」 */
  }
  return "system";
}

export interface DarkModeState {
  /** 用户选择：跟随系统 / 亮色 / 暗色 */
  mode: ThemeMode;
  /** 实际生效的主题（跟随系统时为系统当前偏好） */
  resolvedMode: ResolvedTheme;
  isDark: boolean;
  setMode: (mode: ThemeMode) => void;
  /** 按当前生效模式取语义色板（bridgeTokens 的同键暗色/亮色版本） */
  tokens: BridgeTokens;
}

const DarkModeContext = createContext<DarkModeState>({
  mode: "system",
  resolvedMode: "light",
  isDark: false,
  setMode: () => {},
  tokens: bridgeTokensLight,
});

export function DarkModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode);
  const [systemDark, setSystemDark] = useState<boolean>(systemPrefersDark);

  const setMode = (next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* 忽略 */
    }
  };

  // 仅在「跟随系统」时消费系统偏好；手动选择亮/暗后系统变化不再影响页面
  useEffect(() => {
    const mql = window.matchMedia(DARK_QUERY);
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    // 首次挂载同步一次，覆盖预置脚本执行后到 React 挂载之间的系统变化
    setSystemDark(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  const resolvedMode: ResolvedTheme = mode === "system" ? (systemDark ? "dark" : "light") : mode;
  const isDark = resolvedMode === "dark";

  // 同步 <html data-theme>（生效主题）与 data-theme-mode（用户选择），供 CSS 选择器与调试使用；
  // colorScheme 让滚动条与原生表单控件跟随主题，背景兜住首屏与过滚动区域
  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", resolvedMode);
    root.setAttribute("data-theme-mode", mode);
    root.style.colorScheme = resolvedMode;
    root.style.background = isDark ? bridgeTokensDark.colors.console.background : "#f5f5f5";
  }, [mode, resolvedMode, isDark]);

  const tokens = isDark ? bridgeTokensDark : bridgeTokensLight;

  // .ts 文件无 JSX：用 createElement 包 Provider
  return createElement(
    DarkModeContext.Provider,
    { value: { mode, resolvedMode, isDark, setMode, tokens } },
    children,
  );
}

export function useDarkMode(): DarkModeState {
  return useContext(DarkModeContext);
}
