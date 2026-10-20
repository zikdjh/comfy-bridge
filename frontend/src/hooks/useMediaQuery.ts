import { useEffect, useState } from "react";

/** 监听 CSS 媒体查询（SSR 安全：初值取 false，挂载后再同步） */
export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia(query).matches : false,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatch(e.matches);
    setMatch(mq.matches); // 挂载时同步一次，避免 SSR/初始偏差
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);
  return match;
}

/** 低于桌面最低宽度的兜底断点；本项目布局只以桌面端为验收范围。 */
export function useIsNarrow(): boolean {
  return useMediaQuery("(max-width: 1023px)");
}
