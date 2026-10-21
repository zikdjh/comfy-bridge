import { useRef, useState } from "react";
import { Alert, Button, Card, Descriptions, InputNumber, Popconfirm, Space, Table, Tag, Typography, Upload, message } from "antd";
import {
  CloudDownloadOutlined,
  DeleteOutlined,
  HistoryOutlined,
  SafetyCertificateOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  cleanupRetention,
  createBackup,
  stageRestore,
  useMaintenance,
  useSaveRetention,
  type BackupInfo,
} from "../../api/maintenance";

const bytes = (n: number) => `${(n / 1024 / 1024).toFixed(1)} MiB`;
const when = (ms: number) => new Date(ms).toLocaleString("zh-CN");
const version = (value: string | null | undefined) =>
  value === "untracked" ? "旧版（无版本状态）" : value ?? "无";

export function MaintenanceTab() {
  const { data, isLoading, refetch } = useMaintenance();
  const saveRetention = useSaveRetention();
  const [days, setDays] = useState<number | null>(null);
  const [busy, setBusy] = useState<"backup" | "cleanup" | null>(null);
  const [uploading, setUploading] = useState(false);
  const picked = useRef<File | null>(null);

  const retentionDays = days ?? data?.retention.days ?? 90;
  const release = data?.release;

  const backup = async () => {
    setBusy("backup");
    try {
      const result = await createBackup();
      await refetch();
      message.success(`备份已创建：${result.name}`);
    } catch (err) {
      message.error(`备份失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    try {
      await saveRetention(retentionDays);
      message.success(`保留周期已设为 ${retentionDays} 天`);
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const cleanup = async () => {
    setBusy("cleanup");
    try {
      const result = await cleanupRetention();
      await refetch();
      message.success(
        `清理完成：请求 ${result.requests_removed} 条，作业 ${result.jobs_removed} 条，图快照 ${result.graphs_removed} 个`,
      );
    } catch (err) {
      message.error(`清理失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
    }
  };

  const restore = async () => {
    const file = picked.current;
    if (!file) {
      message.warning("请先选择 .zip 备份文件");
      return;
    }
    setUploading(true);
    try {
      const result = await stageRestore(file);
      await refetch();
      message.success(`已校验并暂存 ${result.name}；重启 ComfyBridge 后恢复`);
    } catch (err) {
      message.error(`恢复文件校验失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setUploading(false);
    }
  };

  const columns: ColumnsType<BackupInfo> = [
    { title: "备份", dataIndex: "name", render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
    { title: "大小", dataIndex: "size", width: 100, render: (v: number) => bytes(v) },
    { title: "创建时间", dataIndex: "created_ms", width: 180, render: (v: number) => when(v) },
    { title: "原因", dataIndex: "reason", width: 140, render: (v: string) => <Tag>{v}</Tag> },
    {
      title: "",
      width: 80,
      render: (_, row) => (
        <Button
          type="link"
          size="small"
          icon={<CloudDownloadOutlined />}
          href={`/api/v1/maintenance/backups/${encodeURIComponent(row.name)}`}
          download
        >
          下载
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Card size="small" title="版本与升级">
        <Descriptions
          size="small"
          column={2}
          items={[
            {
              key: "version",
              label: "当前版本",
              children: release?.version ? <Tag color="blue">{release.version}</Tag> : "源码运行（无 version.txt）",
            },
            {
              key: "installed",
              label: "安装/升级时间",
              children: release?.installed_at_ms ? when(release.installed_at_ms) : "未记录",
            },
            {
              key: "previous",
              label: "上一版本",
              children: version(release?.previous_version),
            },
            {
              key: "selfcheck",
              label: "最近启动自检",
              children: release?.last_self_check ? (
                <Space size={6}>
                  <Tag color={release.last_self_check.ok ? "success" : "error"}>
                    {release.last_self_check.ok ? "通过" : "失败"}
                  </Tag>
                  <Typography.Text type="secondary">
                    {when(release.last_self_check.checked_at_ms)}
                  </Typography.Text>
                </Space>
              ) : "尚未记录",
            },
          ]}
        />
        {release?.last_self_check?.error && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 10 }}
            message="上次启动未通过自检"
            description={release.last_self_check.error}
          />
        )}
        {release && release.upgrade_backups.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text strong><HistoryOutlined /> 版本迁移备份</Typography.Text>
            <Table
              size="small"
              pagination={false}
              rowKey={(row) => `${row.version}-${row.name}`}
              dataSource={release.upgrade_backups}
              columns={[
                { title: "升级到", dataIndex: "version", render: (v: string) => <Typography.Text code>{v}</Typography.Text> },
                { title: "来自", dataIndex: "previous_version", render: (v: string) => version(v) },
                { title: "时间", dataIndex: "created_ms", render: (v: number) => when(v) },
                {
                  title: "备份",
                  dataIndex: "name",
                  render: (v: string) => (
                    <Button
                      type="link"
                      size="small"
                      icon={<CloudDownloadOutlined />}
                      href={`/api/v1/maintenance/backups/${encodeURIComponent(v)}`}
                      download
                    >
                      下载
                    </Button>
                  ),
                },
              ]}
            />
          </div>
        )}
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
          新版本首次启动并检测到迁移时会自动创建备份；如新版本自检失败，可下载对应迁移备份并在本页校验、暂存恢复。
          回滚只恢复用户数据与配置，程序文件请保留并重新打开上一版解压目录。
        </Typography.Paragraph>
      </Card>
      {data?.restore_pending && (
        <Alert
          type="warning"
          showIcon
          message="已有备份等待恢复"
          description="退出并重新打开 ComfyBridge 后应用。如暂不恢复，可删除配置目录中的 restore-staging.zip。"
        />
      )}
      {data?.last_restore?.applied_ms && (
        <Alert
          type="success"
          showIcon
          message={`最近一次恢复：${data.last_restore.source ?? "备份"}（${when(data.last_restore.applied_ms)}）`}
        />
      )}
      <Card size="small" title="本地数据备份">
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            icon={<SafetyCertificateOutlined />}
            message="备份包含 Modal 凭证、请求记录和上传会话元数据"
            description="请只保存在可信位置。备份不含模型权重、生成成片或 ComfyUI 本体。"
          />
          <Space wrap>
            <Button type="primary" loading={busy === "backup"} onClick={backup}>
              立即备份
            </Button>
            <Typography.Text type="secondary">
              保存位置：{data?.backup_dir ?? "~/.comfybridge/backups"}
            </Typography.Text>
          </Space>
          <Table<BackupInfo>
            columns={columns}
            dataSource={data?.backups ?? []}
            rowKey="name"
            size="small"
            loading={isLoading}
            pagination={false}
            locale={{ emptyText: "还没有备份" }}
          />
        </Space>
      </Card>
      <Card size="small" title="从备份恢复">
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            文件会先校验结构、哈希和数据库完整性，再在下次启动时应用。当前正在运行的请求不会被热替换。
          </Typography.Paragraph>
          <Space wrap>
            <Upload
              accept=".zip"
              maxCount={1}
              beforeUpload={(file) => {
                picked.current = file;
                return false;
              }}
              onRemove={() => {
                picked.current = null;
              }}
            >
              <Button icon={<UploadOutlined />}>选择备份文件</Button>
            </Upload>
            <Popconfirm
              title="恢复将在下次启动生效"
              description="当前请求历史、配置和上传会话会被备份内容替换。"
              onConfirm={restore}
              okText="暂存并重启后恢复"
              cancelText="取消"
            >
              <Button danger loading={uploading}>
                校验并暂存恢复
              </Button>
            </Popconfirm>
          </Space>
        </Space>
      </Card>
      <Card size="small" title="数据保留">
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Space wrap align="center">
            <Typography.Text>保留已结束请求</Typography.Text>
            <InputNumber
              min={7}
              max={3650}
              value={retentionDays}
              onChange={(v) => setDays(v ?? 90)}
              addonAfter="天"
              style={{ width: 150 }}
            />
            <Button onClick={save}>保存周期</Button>
            <Popconfirm
              title={`立即清理 ${retentionDays} 天前的数据？`}
              description="只删除已结束的请求与作业、对应过期图快照；不会删除模型、成片或上传会话。"
              onConfirm={cleanup}
              okText="清理"
              cancelText="取消"
            >
              <Button danger icon={<DeleteOutlined />} loading={busy === "cleanup"}>
                立即按周期清理
              </Button>
            </Popconfirm>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            数据目录：{data?.data_dir ?? "backend/data"}。保留策略只控制可重建的请求诊断数据。
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            自动保留最近 10 份手动或恢复前备份，超出后删除最旧文件。
          </Typography.Text>
        </Space>
      </Card>
    </Space>
  );
}
