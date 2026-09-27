import { useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, InputNumber, Popconfirm, Select, Space, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { message } from "antd";
import { useConfig, useSaveConfig, validateComfyRoot } from "../../api/config";
import { LaunchArgsPanel } from "./LaunchArgsPanel";
import { ComfyScanModal } from "./ComfyScanModal";

const yamlPreview = `# 由桥接器自动生成（路径映射表驱动）
comfyui:
  base_path: /data
  checkpoints: models/checkpoints/
  loras: models/loras/
  vae: models/vae/
  controlnet: models/controlnet/`;

interface DiffRow {
  key: string;
  setting: string;
  local: string;
  cloud: string;
}

const diffRows: DiffRow[] = [
  { key: "1", setting: "Comfy.UseNewMenu", local: "Disabled", cloud: "Top" },
  { key: "2", setting: "Comfy.Workflow.Persist", local: "true", cloud: "true" },
  { key: "3", setting: "Comfy.TutorialCompleted", local: "true", cloud: "false" },
];

const diffColumns: ColumnsType<DiffRow> = [
  { title: "设置键", dataIndex: "setting" },
  { title: "本地值", dataIndex: "local" },
  { title: "云端值", dataIndex: "cloud" },
  {
    title: "方向",
    width: 130,
    render: () => (
      <Select
        size="small"
        style={{ width: 110 }}
        defaultValue="push"
        options={[
          { value: "push", label: "→ 推送到云端" },
          { value: "pull", label: "← 从云端拉取" },
          { value: "skip", label: "跳过" },
        ]}
      />
    ),
  },
];

export default function SettingsPage() {
  const { data: config, isLoading } = useConfig();
  const saveConfig = useSaveConfig();
  const [localForm] = Form.useForm();
  const [bridgeForm] = Form.useForm();
  const [modalForm] = Form.useForm();
  const [execForm] = Form.useForm();
  const [guardForm] = Form.useForm();
  const [scanOpen, setScanOpen] = useState(false);
  const [checking, setChecking] = useState(false);

  /** 手动指定目录的即时完整性校验（保存时后端还会再强制校验一次） */
  const checkRoot = async () => {
    const path = String(localForm.getFieldValue("comfyRoot") ?? "").trim();
    if (!path) {
      message.warning("请先填写 ComfyUI 根目录");
      return;
    }
    setChecking(true);
    try {
      const report = await validateComfyRoot(path);
      if (!report.exists) {
        message.error(`目录不存在：${path}`);
      } else if (!report.valid) {
        message.error(`不是可用的 ComfyUI 目录：缺少 ${report.required_missing.join("、")}`);
      } else {
        const extra = [
          report.version ? `v${report.version}` : null,
          report.has_venv ? "内置 venv" : "外部 Python",
          report.optional_missing.length ? `缺少可选：${report.optional_missing.join("、")}` : null,
        ].filter(Boolean).join("，");
        message.success(`完整性校验通过${extra ? `（${extra}）` : ""}`);
      }
    } catch (err) {
      message.error(`校验失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    if (!config) return;
    localForm.setFieldsValue({
      comfyRoot: config.local.comfy_root,
      comfyUrl: config.local.comfy_url,
      outputDir: config.local.output_dir,
      modelsDirs: Object.entries(config.local.models_dirs).map(([category, path]) => ({ category, path })),
    });
    bridgeForm.setFieldsValue({
      listen: config.bridge.listen,
      defaultBackend: config.bridge.default_backend,
      videoBackend: config.bridge.video_backend,
      largeSidePixels: config.bridge.large_side_pixels,
    });
    modalForm.setFieldsValue({
      tokenId: config.modal.token_id,
      environment: config.modal.environment,
      tokenSecret: "", // secret 永不回显明文，留空 = 不修改
    });
    execForm.setFieldsValue({ volume: config.modal.volume });
    guardForm.setFieldsValue({
      onModalUnavailable: config.bridge.on_modal_unavailable,
      taskTimeout: config.modal.task_timeout,
    });
  }, [config, localForm, bridgeForm, modalForm, execForm, guardForm]);

  const saveLocal = async (values: {
    comfyRoot: string;
    comfyUrl: string;
    outputDir: string;
    modelsDirs?: { category: string; path: string }[];
  }) => {
    try {
      // 保存前预校验（不拦截网络错误：后端 PUT 对 comfy_root 仍有强制校验）
      try {
        const report = await validateComfyRoot(values.comfyRoot.trim());
        if (!report.valid) {
          message.error(
            !report.exists
              ? `保存失败：目录不存在 ${values.comfyRoot.trim()}`
              : `保存失败：${values.comfyRoot.trim()} 缺少 ${report.required_missing.join("、")}`,
          );
          return;
        }
      } catch {
        // 预校验请求失败则交给后端校验
      }
      const modelsDirs = Object.fromEntries(
        (values.modelsDirs ?? [])
          .filter((m) => m.category?.trim() && m.path?.trim())
          .map((m) => [m.category.trim(), m.path.trim()]),
      );
      await saveConfig({
        local: {
          comfy_root: values.comfyRoot.trim(),
          comfy_url: values.comfyUrl,
          output_dir: values.outputDir,
          models_dirs: modelsDirs,
        },
      });
      message.success("已保存并热生效");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const saveBridge = async (values: {
    listen: string;
    defaultBackend: string;
    videoBackend: string;
    largeSidePixels: number | null;
  }) => {
    try {
      await saveConfig({
        bridge: {
          listen: values.listen,
          default_backend: values.defaultBackend,
          video_backend: values.videoBackend,
          large_side_pixels: values.largeSidePixels ?? 0,
        },
      });
      message.success("已保存（监听地址需重启桥接器进程生效，路由参数立即生效）");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const saveExec = async (values: { volume: string }) => {
    try {
      await saveConfig({ modal: { volume: values.volume.trim() } });
      message.success("执行参数已保存并热生效");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const saveGuard = async (values: { onModalUnavailable: string; taskTimeout: number }) => {
    try {
      await saveConfig({
        bridge: { on_modal_unavailable: values.onModalUnavailable },
        modal: { task_timeout: values.taskTimeout ?? 3600 },
      });
      message.success("护栏参数已保存并热生效");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const saveModal = async (values: { tokenId: string; tokenSecret?: string; environment?: string }) => {
    try {
      // secret 留空不提交（后端语义：缺省/空 = 保留现有；null = 清除）
      await saveConfig({
        modal: {
          token_id: values.tokenId,
          environment: values.environment ?? "",
          ...(values.tokenSecret ? { token_secret: values.tokenSecret } : {}),
        },
      });
      message.success("Modal 认证信息已保存并热生效");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const clearModal = async () => {
    try {
      await saveConfig({ modal: { token_id: "", environment: "", token_secret: null } });
      message.success("已清除 Modal 凭证");
    } catch (err) {
      message.error(`清除失败：${err instanceof Error ? err.message : err}`);
    }
  };

  return (
    <Card size="small">
      <Tabs
        defaultActiveKey="local"
        items={[
          {
            key: "local",
            label: "本地",
            children: (
              <Form
                layout="vertical"
                form={localForm}
                onFinish={saveLocal}
                style={{ maxWidth: 640 }}
                disabled={isLoading}
              >
                <Form.Item
                  label="ComfyUI 根目录"
                  required
                  extra="含 main.py 的 ComfyUI 仓库根；保存时自动校验完整性，保存后热生效。接管启动的基础命令如需跟随新目录，请到「启动命令」页重新保存"
                >
                  <Space.Compact style={{ width: "100%" }}>
                    <Form.Item
                      name="comfyRoot"
                      noStyle
                      rules={[{ required: true, message: "请填写或点「扫描本机」选择 ComfyUI 根目录" }]}
                    >
                      <Input placeholder="如 E:\AI\ComfyUI" />
                    </Form.Item>
                    <Button onClick={() => setScanOpen(true)}>扫描本机</Button>
                    <Button loading={checking} onClick={checkRoot}>校验</Button>
                  </Space.Compact>
                </Form.Item>
                <Form.Item label="本地 ComfyUI 地址" name="comfyUrl" rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
                <Form.Item
                  label="输出回传目录"
                  name="outputDir"
                  rules={[{ required: true }]}
                  extra="远端（Modal）执行结果回传落盘处；媒体库也读取该目录"
                >
                  <Input />
                </Form.Item>
                <Form.Item label="本地模型目录映射（分类 → 路径）">
                  <Form.List name="modelsDirs">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map((field) => (
                          <Space key={field.key} style={{ display: "flex", marginBottom: 4 }} align="baseline">
                            <Form.Item name={[field.name, "category"]} noStyle>
                              <Input placeholder="分类" style={{ width: 160 }} />
                            </Form.Item>
                            <Form.Item name={[field.name, "path"]} noStyle>
                              <Input placeholder="路径" style={{ width: 340 }} />
                            </Form.Item>
                            <Button size="small" type="text" danger onClick={() => remove(field.name)}>
                              移除
                            </Button>
                          </Space>
                        ))}
                        <Button size="small" icon={<PlusOutlined />} onClick={() => add()}>
                          添加分类
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Form.Item>
                <Button type="primary" htmlType="submit">保存</Button>
              </Form>
            ),
          },
          {
            key: "launch",
            label: "启动命令",
            children: <LaunchArgsPanel />,
          },
          {
            key: "bridge",
            label: "桥接",
            children: (
              <Form
                layout="vertical"
                form={bridgeForm}
                onFinish={saveBridge}
                style={{ maxWidth: 640 }}
                disabled={isLoading}
              >
                <Form.Item
                  label="桥接器监听地址"
                  name="listen"
                  rules={[{ required: true }]}
                  extra="修改后需重启桥接器进程生效"
                >
                  <Input />
                </Form.Item>
                <Form.Item label="默认后端（兜底规则）" name="defaultBackend">
                  <Select
                    options={[
                      { value: "local", label: "本地" },
                      { value: "modal", label: "云端" },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  label="视频类请求后端"
                  name="videoBackend"
                  extra="视频类输出节点（AnimateDiff / Video Combine 等）的请求走此通道"
                >
                  <Select
                    options={[
                      { value: "local", label: "本地" },
                      { value: "modal", label: "云端" },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  label="大分辨率上云阈值（图最大边，px）"
                  name="largeSidePixels"
                  extra="0 = 关闭；图最大边达到阈值时请求改走云端"
                >
                  <InputNumber min={0} step={128} style={{ width: 200 }} />
                </Form.Item>
                <Button type="primary" htmlType="submit">保存</Button>
              </Form>
            ),
          },
          {
            key: "modal",
            label: "Modal",
            children: (
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Card
                  size="small"
                  title={
                    <Space>
                      Modal 认证
                      <Tag color={config?.modal.has_secret ? "green" : "orange"}>
                        {config?.modal.has_secret ? "凭证已配置" : "未配置"}
                      </Tag>
                    </Space>
                  }
                >
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16, maxWidth: 640 }}
                    message="凭证保存在 ~/.comfybridge/config.toml（用户目录，不进代码库）；secret 保存后不再回显明文，接口只返回脱敏值。Token 来自 Modal 控制台（modal.com/settings → API Tokens）或命令行 modal token new。"
                  />
                  <Form
                    layout="vertical"
                    form={modalForm}
                    onFinish={saveModal}
                    style={{ maxWidth: 640 }}
                    disabled={isLoading}
                  >
                    <Form.Item label="Token ID（ak-…）" name="tokenId" rules={[{ required: true, message: "请填写 Token ID" }]}>
                      <Input placeholder="ak-…" autoComplete="off" />
                    </Form.Item>
                    <Form.Item
                      label="Token Secret（as-…）"
                      name="tokenSecret"
                      extra={config?.modal.has_secret ? `已保存：${config.modal.secret_masked}（留空表示不修改）` : "首次使用请填写后保存"}
                      rules={config?.modal.has_secret ? undefined : [{ required: true, message: "请填写 Token Secret" }]}
                    >
                      <Input.Password placeholder={config?.modal.has_secret ? "留空不修改" : "as-…"} autoComplete="new-password" />
                    </Form.Item>
                    <Form.Item label="环境 environment（可选）" name="environment" extra="留空使用默认环境">
                      <Input placeholder="默认环境" autoComplete="off" />
                    </Form.Item>
                    <Space>
                      <Button type="primary" htmlType="submit">保存认证信息</Button>
                      <Popconfirm title="确定清除已保存的 Modal 凭证？" onConfirm={clearModal} disabled={!config?.modal.has_secret}>
                        <Button danger disabled={!config?.modal.has_secret}>清除凭证</Button>
                      </Popconfirm>
                    </Space>
                  </Form>
                </Card>
                <Card size="small" title="执行参数">
                  <Form
                    layout="vertical"
                    form={execForm}
                    onFinish={saveExec}
                    style={{ maxWidth: 640 }}
                    disabled={isLoading}
                  >
                    <Form.Item
                      label="数据卷名"
                      name="volume"
                      rules={[{ required: true, message: "请填写数据卷名" }]}
                      extra="模型/节点持久化的 Modal 数据卷；保存后热生效。注意：云端 runner 按部署时挂载的卷运行，改卷名需同步重建部署才能对上"
                    >
                      <Input />
                    </Form.Item>
                    <Form.Item label="GPU 类型" extra="云端按固定 GPU 部署（L40S）；更换需修改 modal-runner/runner.py 后重新部署">
                      <Input value="L40S" disabled />
                    </Form.Item>
                    <Form.Item
                      label="保活策略"
                      extra="单容器（max_containers=1），空闲后自动缩容到零；自定义 scaledown_window 规划中"
                    >
                      <Input value="容器空闲自动缩容到 0" disabled />
                    </Form.Item>
                    <Button type="primary" htmlType="submit">保存</Button>
                  </Form>
                </Card>
              </Space>
            ),
          },
          {
            key: "budget",
            label: "护栏与预算",
            children: (
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Alert
                  type="info"
                  showIcon
                  style={{ maxWidth: 640 }}
                  message="「云端不可用动作」与「单次运行超时」为已生效配置；月度预算与并发护栏需费用统计/执行通道扩展后接入（规划中）。"
                />
                <Form
                  layout="vertical"
                  form={guardForm}
                  onFinish={saveGuard}
                  style={{ maxWidth: 640 }}
                  disabled={isLoading}
                >
                  <Form.Item
                    label="云端不可用时动作"
                    name="onModalUnavailable"
                    extra="路由决策目标为云端但 Modal 通道不可用时：回落本地执行，或直接拒绝请求"
                  >
                    <Select
                      options={[
                        { value: "fallback_local", label: "回落本地执行（fallback_local）" },
                        { value: "reject", label: "拒绝请求（reject）" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item
                    label="单次云端运行超时（秒）"
                    name="taskTimeout"
                    extra="含冷启动等待；超时任务标记失败"
                  >
                    <InputNumber min={60} step={60} style={{ width: 200 }} />
                  </Form.Item>
                  <Form.Item label="月度预算上限（USD）" name="monthlyUsd" initialValue={50} extra="规划中：费用统计接入后生效">
                    <InputNumber min={0} prefix="$" style={{ width: 160 }} disabled />
                  </Form.Item>
                  <Form.Item label="云端最大并发数" name="maxConcurrent" initialValue={2} extra="规划中：执行通道接入后生效">
                    <InputNumber min={1} max={10} disabled />
                  </Form.Item>
                  <Button type="primary" htmlType="submit">保存</Button>
                </Form>
              </Space>
            ),
          },
          {
            key: "sync",
            label: "设置同步",
            children: (
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Card size="small" title="两端设置差异（键级）">
                  <Table<DiffRow> columns={diffColumns} dataSource={diffRows} pagination={false} size="small" />
                  <Space style={{ marginTop: 10 }}>
                    <Button size="small" type="primary" onClick={() => message.info("静态占位：M2 接入设置推送")}>
                      执行同步
                    </Button>
                    <span style={{ color: "#8a90a2", fontSize: 12 }}>设置同步尚未实现（规划中）；当前配置以 ~/.comfybridge/config.toml 为准</span>
                  </Space>
                </Card>
                <Card size="small" title="extra_model_paths.yaml 结构示意（云端数据卷模板）">
                  <Typography.Paragraph copyable={{ text: yamlPreview }}>
                    <pre style={{ background: "#f6f7fb", padding: 12, borderRadius: 8, fontSize: 12, margin: 0 }}>
                      {yamlPreview}
                    </pre>
                  </Typography.Paragraph>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    实际文件位于数据卷 /config/extra_model_paths.yaml，由 runner 首次启动生成，可在「数据卷」页查看编辑。
                  </Typography.Text>
                </Card>
              </Space>
            ),
          },
        ]}
      />
      <ComfyScanModal
        open={scanOpen}
        onClose={() => setScanOpen(false)}
        onPick={(path) => {
          localForm.setFieldsValue({ comfyRoot: path });
          setScanOpen(false);
          message.success(`已填入 ComfyUI 根目录：${path}（点「保存」后生效）`);
        }}
      />
    </Card>
  );
}
