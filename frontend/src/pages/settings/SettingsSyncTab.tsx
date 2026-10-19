/** 设置同步（M2-5）：两端 comfy.settings.json 键级差异、方向选择与推拉执行。 */
import { useState } from "react";
import { Alert, Button, Card, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { message } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import {
  applySettingsSync,
  useSettingsSyncDiff,
  type SettingsDiffRow,
  type SyncDirection,
} from "../../api/settingsSync";
import { bridgeTokens as t } from "../../theme";

type Choice = SyncDirection | "skip";

const statusTag = (status: SettingsDiffRow["status"]) =>
  status === "same" ? (
    <Tag color="green">一致</Tag>
  ) : status === "diff" ? (
    <Tag color="orange">不同</Tag>
  ) : status === "only_local" ? (
    <Tag color="blue">仅本地</Tag>
  ) : (
    <Tag color="geekblue">仅云端</Tag>
  );

const yamlPreview = `# 由桥接器自动生成（路径映射表驱动）
comfyui:
  base_path: /data
  checkpoints: models/checkpoints/
  loras: models/loras/
  vae: models/vae/
  controlnet: models/controlnet/`;

export function SettingsSyncTab() {
  const qc = useQueryClient();
  const { data, isLoading, refetch, isFetching } = useSettingsSyncDiff();
  const [directions, setDirections] = useState<Record<string, Choice>>({});
  const [applying, setApplying] = useState(false);

  const rows = data?.rows ?? [];
  const dirOf = (row: SettingsDiffRow): Choice => directions[row.key] ?? row.default_direction ?? "skip";
  const pending = rows.filter((r) => r.status !== "same" && dirOf(r) !== "skip");

  const apply = async () => {
    if (pending.length === 0) {
      message.info("没有待同步的键：两端一致，或方向均设为跳过");
      return;
    }
    setApplying(true);
    try {
      const payload = Object.fromEntries(pending.map((r) => [r.key, dirOf(r) as SyncDirection]));
      const result = await applySettingsSync(payload);
      if (result.pushed.length) message.success(`已推送 ${result.pushed.length} 个键到云端数据卷`);
      if (result.pulled.length) message.success(`已从云端拉取 ${result.pulled.length} 个键到本地`);
      if (result.skipped.length) message.warning(`${result.skipped.length} 个键被跳过：另一端缺失或方向无效`);
      await qc.invalidateQueries({ queryKey: ["settings-sync-diff"] });
    } catch (err) {
      message.error(`同步执行失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setApplying(false);
    }
  };

  const columns: ColumnsType<SettingsDiffRow> = [
    {
      title: "设置键",
      dataIndex: "key",
      render: (key: string, row) => (
        <Space size={4}>
          <Typography.Text code>{key}</Typography.Text>
          {row.sensitive && <Tag color="purple">凭证·掩码</Tag>}
        </Space>
      ),
    },
    { title: "本地值", dataIndex: "local", render: (v: string | null) => (v === null ? "—" : <Typography.Text code>{v}</Typography.Text>) },
    { title: "云端值", dataIndex: "cloud", render: (v: string | null) => (v === null ? "—" : <Typography.Text code>{v}</Typography.Text>) },
    { title: "状态", dataIndex: "status", width: 90, render: (_, row) => statusTag(row.status) },
    {
      title: "方向",
      width: 150,
      render: (_, row) =>
        row.status === "same" ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>无需同步</Typography.Text>
        ) : (
          <Select<Choice>
            size="small"
            style={{ width: 130 }}
            value={dirOf(row)}
            onChange={(v) => setDirections((prev) => ({ ...prev, [row.key]: v }))}
            options={[
              { value: "push", label: "→ 推送到云端" },
              { value: "pull", label: "← 从云端拉取" },
              { value: "skip", label: "跳过" },
            ]}
          />
        ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Card
        size="small"
        title="两端设置差异（键级）"
        extra={
          <Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              待同步 {pending.length} 键{isFetching ? " · 刷新中…" : ""}
            </Typography.Text>
            <Button size="small" type="primary" loading={applying} onClick={apply}>
              执行同步
            </Button>
            <Button size="small" onClick={() => refetch()}>刷新</Button>
          </Space>
        }
      >
        {data && !data.cloud_available && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message="云端设置不可读"
            description={data.cloud_error ?? "请检查 Modal SDK 与凭证配置"}
          />
        )}
        {data && data.cloud_available && !data.cloud_exists && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="云端设置文件尚未生成（首次部署 runner 后自动创建），当前按空设置对待"
          />
        )}
        {data && !data.local_exists && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`本地设置文件不存在：${data.local_path}（拉取时会自动创建）`}
          />
        )}
        <Table<SettingsDiffRow>
          columns={columns}
          dataSource={rows}
          rowKey="key"
          size="small"
          loading={isLoading}
          pagination={false}
          locale={{ emptyText: data?.cloud_available ? "两端暂无任何设置键" : "云端不可读，无法对比" }}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 10 }}>
          推送=把本地值写入云端数据卷（容器运行中时下次冷启动生效）；拉取=把云端值写入本地（ComfyUI 读取设置时生效）。
          键名含 token/key/secret/password 的值仅掩码展示（{data?.cloud_path ?? "user/default/comfy.settings.json"}），推送/拉取使用真实值。
        </Typography.Text>
      </Card>
      <Card size="small" title="extra_model_paths.yaml 结构示意（云端数据卷模板）">
        <Typography.Paragraph copyable={{ text: yamlPreview }}>
          <pre style={{ background: t.colors.surfaceWash, padding: 12, borderRadius: 8, fontSize: 12, margin: 0 }}>
            {yamlPreview}
          </pre>
        </Typography.Paragraph>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          实际文件位于数据卷 /config/extra_model_paths.yaml，由 runner 首次启动生成，可在「数据卷」页查看编辑。
        </Typography.Text>
      </Card>
    </Space>
  );
}
