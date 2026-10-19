import { useEffect, useState } from "react";

/** 页面可见性（document.visibilityState），供轮询降频使用；SSR 安全。 */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() =>
    typeof document === "undefined" ? true : document.visibilityState === "visible",
  );
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}
