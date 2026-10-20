import { useMediaQuery } from "./useMediaQuery";

/** 桌面端最低验收宽度：更窄的窗口不做布局适配。 */
export const DESKTOP_MIN_WIDTH = 1024;

/** 1024-1279px 使用紧凑侧栏，给主内容区保留足够宽度。 */
export function useIsCompactDesktop(): boolean {
  return useMediaQuery(`(min-width: ${DESKTOP_MIN_WIDTH}px) and (max-width: 1279px)`);
}

/** 1280px 起为常规桌面宽度，侧栏可默认展开。 */
export function useIsWideDesktop(): boolean {
  return useMediaQuery("(min-width: 1280px)");
}
