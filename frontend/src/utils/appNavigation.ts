export const SETTINGS_TAB_KEYS = ["local", "launch", "bridge", "modal", "budget", "sync", "update"] as const;

export type SettingsTabKey = (typeof SETTINGS_TAB_KEYS)[number];

/** 设置页内部别名，集中在这里避免错误提示与页签 key 逐渐漂移。 */
export function normalizeSettingsTab(tab?: string): SettingsTabKey | null {
  if (!tab) return null;
  if (tab === "status") return null;
  return (SETTINGS_TAB_KEYS as readonly string[]).includes(tab) ? (tab as SettingsTabKey) : null;
}

export function openAppPath(path: string) {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  window.location.assign(`${base}${path}`);
}

export function openSettings(tab?: string) {
  const key = normalizeSettingsTab(tab);
  openAppPath(key ? `/settings?tab=${key}` : "/settings");
}

export function openStatus() {
  openAppPath("/status");
}
