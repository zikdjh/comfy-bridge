import { useState } from "react";
import { Alert, Button, Card, Space, Typography } from "antd";
import { ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { checkUpdate, type UpdateCheckResp } from "../../api/config";
import { ActionableError } from "../../components/common/ActionableError";

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
  const [error, setError] = useState<unknown>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setInfo(await checkUpdate(true));
    } catch (err) {
      setError(err);
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
        {!!error && <ActionableError error={error} />}
        {info?.available && (
          <Alert
            type="info"
            showIcon
            message={`发现新版本 ${info.latest}`}
            description="覆盖前请先退出 ComfyBridge。配置和请求数据保存在用户目录，不会随解压目录覆盖；建议保留上一版解压目录，便于回退。"
            action={
              <Button size="small" type="primary" href={info.release_url} target="_blank">
                前往下载
              </Button>
            }
          />
        )}
        {info && !info.available && reasonText && <Alert type="warning" showIcon message={reasonText} />}
        {info && !info.available && info.reason === "ok" && (
          <Typography.Text type="secondary">已是最新版本{info.latest ? `（远端 ${info.latest}）` : ""}。</Typography.Text>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          <SafetyCertificateOutlined /> 用户数据位于 ~/.comfybridge；升级只替换程序文件，不覆盖配置、日志和上传会话。
        </Typography.Text>
      </Space>
    </Card>
  );
}
