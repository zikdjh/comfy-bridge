import { useState } from "react";
import { Alert, Button, Card, Space, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { checkUpdate, type UpdateCheckResp } from "../../api/config";

function describeReason(reason: string): string | null {
  switch (reason) {
    case "not_packaged":
      return "当前以源码方式运行（未找到 version.txt），无更新检查。";
    case "check_failed":
      return "检查失败：无法访问 GitHub。若你的网络需要代理才能访问 GitHub，请在系统环境变量设置 HTTP_PROXY/HTTPS_PROXY 后重启应用再试。";
    default:
      return null;
  }
}

export function UpdateCheckCard() {
  const [loading, setLoading] = useState(false);
  const [info, setInfo] = useState<UpdateCheckResp | null>(null);

  const run = async () => {
    setLoading(true);
    try {
      setInfo(await checkUpdate(true));
    } catch (err) {
      message.error(`检查失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setLoading(false);
    }
  };

  const reasonText = info ? describeReason(info.reason) : null;
  return (
    <Card size="small" title="检查更新" style={{ marginTop: 16 }}>
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Space size={12} wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={run}>
            立即检查
          </Button>
          {info?.current && <Typography.Text type="secondary">当前版本：{info.current}</Typography.Text>}
          {info?.cached && <Typography.Text type="secondary">（24h 内缓存结果）</Typography.Text>}
        </Space>
        {info?.available && (
          <Alert
            type="info"
            showIcon
            message={
              <Space size={8} wrap>
                <span>发现新版本 {info.latest}</span>
                <Button size="small" type="primary" href={info.release_url} target="_blank">
                  前往下载
                </Button>
              </Space>
            }
          />
        )}
        {info && !info.available && reasonText && <Alert type="warning" showIcon message={reasonText} />}
        {info && !info.available && info.reason === "ok" && (
          <Typography.Text type="secondary">已是最新版本{info.latest ? `（远端 ${info.latest}）` : ""}。</Typography.Text>
        )}
      </Space>
    </Card>
  );
}
