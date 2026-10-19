import { useEffect, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { checkUpdate, type UpdateCheckResp } from "../../api/config";

const DISMISS_KEY = "cb_update_dismissed";

export function UpdateBanner() {
  const [info, setInfo] = useState<UpdateCheckResp | null>(null);
  const [hidden, setHidden] = useState(() => {
    try {
      return sessionStorage.getItem(DISMISS_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    let alive = true;
    checkUpdate().then((r) => {
      if (alive) setInfo(r);
    }).catch(() => {
      /* 检查失败静默：横幅不出现在网络异常时 */
    });
    return () => {
      alive = false;
    };
  }, []);

  if (!info?.available || hidden) return null;

  const dismiss = () => {
    setHidden(true);
    try {
      sessionStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* ignore */
    }
  };

  return (
    <Alert
      type="info"
      showIcon
      style={{ marginBottom: 12 }}
      message={
        <Space size={8} wrap>
          <span>
            新版本 <Typography.Text strong>{info.latest}</Typography.Text> 可用（当前 {info.current}）
          </span>
        </Space>
      }
      action={
        <Space size={8}>
          <Button size="small" type="primary" href={info.release_url} target="_blank">
            前往下载
          </Button>
          <Button size="small" type="text" onClick={dismiss}>
            本次会话不再提示
          </Button>
        </Space>
      }
    />
  );
}
