import { useState } from "react";
import { Button, Card, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import { PlusOutlined, SwapOutlined, DeleteOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { ApiError } from "../../api/client";
import {
  useAccounts,
  useActivateAccount,
  useConfig,
  useDeleteAccount,
  useSaveAccount,
  type AccountProfileDto,
} from "../../api/config";

/** 后端 409 detail 是 JSON 串拼在错误消息里，抽出可读文本给二次确认弹窗。 */
function readableDetail(err: unknown): string {
  if (err instanceof ApiError) {
    const idx = err.message.indexOf('{"detail"');
    if (idx >= 0) {
      try {
        const parsed = JSON.parse(err.message.slice(idx)) as { detail?: string };
        if (parsed.detail) return parsed.detail;
      } catch {
        /* 保留原始消息 */
      }
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

export default function AccountProfilesCard() {
  const { data: config } = useConfig();
  const { data, isLoading } = useAccounts();
  const saveAccount = useSaveAccount();
  const deleteAccount = useDeleteAccount();
  const activateAccount = useActivateAccount();
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");

  const doActivate = async (name: string, force: boolean) => {
    try {
      await activateAccount(name, force);
      message.success(`已切换到账号「${name}」，通道与云端视图已刷新`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && !force) {
        Modal.confirm({
          title: "存在在途云端任务",
          content: readableDetail(err),
          okText: "强制切换",
          okButtonProps: { danger: true },
          cancelText: "取消",
          onOk: () => doActivate(name, true),
        });
        return;
      }
      message.error(`切换失败：${readableDetail(err)}`);
    }
  };

  const doSaveCurrent = async () => {
    const name = saveName.trim();
    if (!name || !config) return;
    try {
      await saveAccount({
        name,
        token_id: config.modal.token_id,
        environment: config.modal.environment,
        proxy_token_id: config.modal.proxy_token_id,
        volume: config.modal.volume,
        app_name: config.modal.app_name,
        web_url: config.modal.web_url,
        task_timeout: config.modal.task_timeout,
        extra_launch_args: config.modal.extra_launch_args,
      });
      message.success(`已保存账号档案「${name}」`);
      setSaveOpen(false);
      setSaveName("");
    } catch (err) {
      message.error(`保存失败：${readableDetail(err)}`);
    }
  };

  const columns: ColumnsType<AccountProfileDto> = [
    {
      title: "名称",
      dataIndex: "name",
      width: 140,
      render: (name: string, row) => (
        <Space size={6}>
          <Typography.Text strong>{name}</Typography.Text>
          {row.active && <Tag color="green">激活中</Tag>}
        </Space>
      ),
    },
    { title: "Token ID", dataIndex: "token_id", width: 140, ellipsis: true },
    { title: "数据卷", dataIndex: "volume", width: 130, ellipsis: true },
    {
      title: "Runner 端点",
      dataIndex: "web_url",
      ellipsis: true,
      render: (url: string) => url || <Tag>未部署</Tag>,
    },
    {
      title: "操作",
      key: "actions",
      width: 170,
      render: (_, row) => (
        <Space size={4}>
          <Popconfirm
            title={`切换到账号「${row.name}」？`}
            description="token、数据卷、Runner 端点将整体切换，云端视图按新账号重拉"
            onConfirm={() => doActivate(row.name, false)}
            disabled={row.active}
          >
            <Button size="small" icon={<SwapOutlined />} disabled={row.active}>激活</Button>
          </Popconfirm>
          <Popconfirm
            title={`删除账号档案「${row.name}」？`}
            onConfirm={() => deleteAccount(row.name).catch((err) => message.error(`删除失败：${readableDetail(err)}`))}
            disabled={row.active}
          >
            <Button size="small" danger icon={<DeleteOutlined />} disabled={row.active} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      size="small"
      title="账号档案"
      extra={
        <Button
          size="small"
          icon={<PlusOutlined />}
          disabled={!config?.modal.token_id}
          onClick={() => setSaveOpen(true)}
        >
          保存当前为档案
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12, maxWidth: 640 }}>
        每个账号档案绑定一份凭据（1 账号 = 1 份 token，轮换即覆盖）；激活时 token、代理凭证、
        数据卷、Runner 端点整体切换，节点差异缓存重置、在途云端任务未收尾时默认拒绝切换。
      </Typography.Paragraph>
      <Table
        size="small"
        rowKey="name"
        loading={isLoading}
        columns={columns}
        dataSource={data?.accounts ?? []}
        pagination={false}
        locale={{ emptyText: "暂无档案：配置好当前凭据后点「保存当前为档案」" }}
      />
      <Modal
        title="保存当前配置为账号档案"
        open={saveOpen}
        onOk={doSaveCurrent}
        okButtonProps={{ disabled: !saveName.trim() }}
        onCancel={() => { setSaveOpen(false); setSaveName(""); }}
        okText="保存"
        cancelText="取消"
      >
        <Typography.Paragraph type="secondary">
          密钥沿用当前已保存的凭据，不在表单中重复输入；同名档案将被覆盖更新。
        </Typography.Paragraph>
        <Input
          placeholder="档案名称，如 personal / team"
          value={saveName}
          onChange={(e) => setSaveName(e.target.value)}
          onPressEnter={doSaveCurrent}
        />
      </Modal>
    </Card>
  );
}
