import type { ThemeConfig } from "antd";

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
