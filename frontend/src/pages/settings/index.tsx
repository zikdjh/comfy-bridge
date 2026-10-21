import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, AutoComplete, Button, Card, Form, Input, InputNumber, Popconfirm, Segmented, Select, Space, Switch, Table, Tabs, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { message } from "antd";
import { applyModalResources, fetchModalPricing, fetchModalResourceJob, useConfig, useSaveConfig, fetchNodeEnv, validateComfyRoot } from "../../api/config";
import type { DeployJobDto, ModalPricingDto, NodeEnvDto } from "../../api/config";
import { LaunchArgsPanel } from "./LaunchArgsPanel";
import { ComfyScanModal } from "./ComfyScanModal";
import { SettingsSyncTab } from "./SettingsSyncTab";
import { ConfigWizard } from "./ConfigWizard";
import AccountProfilesCard from "./AccountProfilesCard";
import { UpdateCheckCard } from "./UpdateCheckCard";
import { useDarkMode } from "../../hooks/useDarkMode";
import type { ThemeMode } from "../../hooks/useDarkMode";
import { normalizeSettingsTab, SETTINGS_TAB_KEYS } from "../../utils/appNavigation";

/** Modal 常用 GPU 型号建议（控件允许自定义输入；单价随平台调整，估算值在「护栏与预算」维护） */
const GPU_OPTIONS = [
  { value: "L40S" },
  { value: "L4" },
  { value: "A10G" },
  { value: "T4" },
  { value: "A100" },
  { value: "A100-80GB" },
  { value: "H100" },
  { value: "H100:2" },
  { value: "H200" },
  { value: "B200" },
];

export default function SettingsPage() {
  const [searchParams] = useSearchParams();
  const requestedTab = normalizeSettingsTab(searchParams.get("tab") ?? undefined) ?? "local";
  const [activeTab, setActiveTab] = useState<string>(requestedTab);
  const { mode, setMode, tokens: t } = useDarkMode();
  const { data: config, isLoading } = useConfig();
  const saveConfig = useSaveConfig();
  const [localForm] = Form.useForm();
  const [bridgeForm] = Form.useForm();
  const [modalForm] = Form.useForm();
  const [execForm] = Form.useForm();
  const [guardForm] = Form.useForm();
  const [scanOpen, setScanOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const [nodeEnv, setNodeEnv] = useState<NodeEnvDto | null>(null);
  const [nodeChecking, setNodeChecking] = useState(false);
  const [resourceJob, setResourceJob] = useState<DeployJobDto | null>(null);
  const [applyingResources, setApplyingResources] = useState(false);
  const [pricing, setPricing] = useState<ModalPricingDto | null>(null);
  const [pricingError, setPricingError] = useState(false);
  const pricingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setActiveTab(requestedTab);
  }, [requestedTab]);

  /** 表单变化时防抖请求预估价：只预览，不保存、不触发部署 */
  const refreshPricing = (values: { gpu?: string; cpu?: number; memoryGb?: number }) => {
    const gpu = (values.gpu ?? "").trim();
    const { cpu, memoryGb } = values;
    if (!gpu || cpu == null || memoryGb == null) {
      setPricing(null);
      return;
    }
    if (pricingTimer.current) clearTimeout(pricingTimer.current);
    pricingTimer.current = setTimeout(() => {
      fetchModalPricing({
        gpu,
        cpu,
        memory_mb: Math.round(memoryGb * 1024),
      })
        .then((out) => {
          setPricing(out);
          setPricingError(false);
        })
        .catch(() => setPricingError(true));
    }, 250);
  };

  /** Node.js 环境检测：手动指定路径 → PATH → 常见安装目录快扫（dev.ps1/pnpm 前置条件可视化） */
  const checkNode = async () => {
    setNodeChecking(true);
    try {
      setNodeEnv(await fetchNodeEnv());
    } catch (err) {
      message.error(`Node 检测失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setNodeChecking(false);
    }
  };

  useEffect(() => {
    checkNode();
  }, []);

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
      nodePath: config.local.node_path ?? "",
      modelsDirs: Object.entries(config.local.models_dirs).map(([category, path]) => ({ category, path })),
    });
    bridgeForm.setFieldsValue({
      listen: config.bridge.listen,
      defaultBackend: config.bridge.default_backend,
      videoBackend: config.bridge.video_backend,
      largeSidePixels: config.bridge.large_side_pixels,
      preflight: config.bridge.preflight,
      privacyConfirm: config.bridge.privacy_confirm,
      objectInfoMerge: config.bridge.object_info_merge,
    });
    modalForm.setFieldsValue({
      tokenId: config.modal.token_id,
      environment: config.modal.environment,
      tokenSecret: "", // secret 永不回显明文，留空 = 不修改
      proxyTokenId: config.modal.proxy_token_id,
      proxyTokenSecret: "",
    });
    execForm.setFieldsValue({
      volume: config.modal.volume,
      gpu: config.modal.gpu,
      cpu: config.modal.cpu,
      memoryGb: Math.round((config.modal.memory_mb / 1024) * 100) / 100,
    });
    refreshPricing({
      gpu: config.modal.gpu,
      cpu: config.modal.cpu,
      memoryGb: config.modal.memory_mb / 1024,
    });
    guardForm.setFieldsValue({
      onModalUnavailable: config.bridge.on_modal_unavailable,
      maxConcurrent: config.bridge.max_cloud_concurrent,
      taskTimeout: config.modal.task_timeout,
      monthlyBudgetUsd: config.bridge.monthly_budget_usd,
      onBudgetExceed: config.bridge.on_budget_exceed,
      rateUsd: config.bridge.gpu_hourly_rate_usd,
      useSystemProxy: config.bridge.use_system_proxy,
    });
  }, [config, localForm, bridgeForm, modalForm, execForm, guardForm]);

  const saveLocal = async (values: {
    comfyRoot: string;
    comfyUrl: string;
    outputDir: string;
    nodePath?: string;
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
          node_path: (values.nodePath ?? "").trim(),
          models_dirs: modelsDirs,
        },
      });
      message.success("已保存并热生效");
      checkNode();
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const saveBridge = async (values: {
    listen: string;
    defaultBackend: string;
    videoBackend: string;
    largeSidePixels: number | null;
    preflight: string;
    privacyConfirm: boolean;
    objectInfoMerge: string;
  }) => {
    try {
      await saveConfig({
        bridge: {
          listen: values.listen,
          default_backend: values.defaultBackend,
          video_backend: values.videoBackend,
          large_side_pixels: values.largeSidePixels ?? 0,
          preflight: values.preflight,
          privacy_confirm: values.privacyConfirm,
          object_info_merge: values.objectInfoMerge,
        },
      });
      message.success("已保存（监听地址需重启桥接器进程生效，路由参数立即生效）");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  type ExecFormValues = { volume: string; gpu: string; cpu: number; memoryGb: number };

  /** 表单值 → 配置字段：内存以 GB 展示、以 MiB 存储（Modal 口径） */
  const execPayload = (values: ExecFormValues) => ({
    volume: values.volume.trim(),
    gpu: (values.gpu ?? "").trim(),
    cpu: values.cpu,
    memory_mb: Math.round((values.memoryGb ?? 0) * 1024),
  });

  const saveExec = async (values: ExecFormValues) => {
    try {
      await saveConfig({ modal: execPayload(values) });
      message.success("执行参数已保存；资源规格需重新部署后生效");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  /** 保存当前表单值并触发一次 modal deploy，使 GPU/CPU/内存规格作用到云端容器 */
  const applyResources = async () => {
    let values: ExecFormValues;
    try {
      values = (await execForm.validateFields()) as ExecFormValues;
    } catch {
      return; // 表单校验失败，错误已由 Form.Item 展示
    }
    setApplyingResources(true);
    try {
      await saveConfig({ modal: execPayload(values) });
      const { job } = await applyModalResources();
      setResourceJob(job);
      message.success("已开始重新部署；完成后资源规格生效");
    } catch (err) {
      message.error(`应用失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setApplyingResources(false);
    }
  };

  // 进页面时同步一次部署任务（可能是别处发起的），进行中每 3s 轮询到结束
  useEffect(() => {
    fetchModalResourceJob()
      .then(({ job }) => setResourceJob(job))
      .catch(() => {
        /* 后端未就绪时忽略，用户操作时会重试 */
      });
  }, []);

  useEffect(() => {
    if (resourceJob?.state !== "running") return;
    const timer = setInterval(async () => {
      try {
        const { job } = await fetchModalResourceJob();
        setResourceJob(job);
      } catch {
        /* 单次轮询失败保留上次状态，下个周期继续 */
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [resourceJob?.state]);

  const saveGuard = async (values: {
    onModalUnavailable: string;
    taskTimeout: number;
    monthlyBudgetUsd: number | null;
    onBudgetExceed: string;
    rateUsd: number | null;
    maxConcurrent: number | null;
    useSystemProxy: boolean;
  }) => {
    try {
      await saveConfig({
        bridge: {
          on_modal_unavailable: values.onModalUnavailable,
          monthly_budget_usd: values.monthlyBudgetUsd ?? 0,
          on_budget_exceed: values.onBudgetExceed,
          gpu_hourly_rate_usd: values.rateUsd ?? 1.95,
          max_cloud_concurrent: values.maxConcurrent ?? 0,
          use_system_proxy: values.useSystemProxy,
        },
        modal: { task_timeout: values.taskTimeout ?? 3600 },
      });
      message.success("护栏与预算已保存并热生效");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const saveModal = async (values: {
    tokenId: string;
    tokenSecret?: string;
    environment?: string;
    proxyTokenId?: string;
    proxyTokenSecret?: string;
  }) => {
    try {
      // secret 留空不提交（后端语义：缺省/空 = 保留现有；null = 清除）
      await saveConfig({
        modal: {
          token_id: values.tokenId,
          environment: values.environment ?? "",
          ...(values.tokenSecret ? { token_secret: values.tokenSecret } : {}),
          proxy_token_id: values.proxyTokenId ?? "",
          ...(values.proxyTokenSecret ? { proxy_token_secret: values.proxyTokenSecret } : {}),
        },
      });
      message.success("Modal 认证信息已保存并热生效");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    }
  };

  const clearModal = async () => {
    try {
      await saveConfig({
        modal: { token_id: "", environment: "", token_secret: null, proxy_token_id: "", proxy_token_secret: null },
      });
      message.success("已清除 Modal 凭证");
    } catch (err) {
      message.error(`清除失败：${err instanceof Error ? err.message : err}`);
    }
  };

  return (
    <Card
      size="small"
      extra={
        <Button size="small" onClick={() => setWizardOpen(true)}>
          配置向导
        </Button>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={(key) => {
          if ((SETTINGS_TAB_KEYS as readonly string[]).includes(key)) setActiveTab(key);
        }}
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
                <Form.Item
                  label="Node.js 路径（可选）"
                  name="nodePath"
                  extra="开发前端（pnpm/Vite）依赖 node；留空 = 自动按 PATH 与常见安装目录检测。手动指定后保存，检测将优先使用该路径"
                >
                  <Input allowClear placeholder="如 E:\node\version\v24.20.0\node.exe，留空自动检测" />
                </Form.Item>
                <Form.Item label="Node.js 环境">
                  <Space wrap>
                    <Button size="small" loading={nodeChecking} onClick={checkNode}>检测</Button>
                    {nodeEnv === null ? (
                      <Typography.Text type="secondary">未检测</Typography.Text>
                    ) : nodeEnv.ok ? (
                      <Typography.Text>
                        <Tag color="success">{nodeEnv.version}</Tag>
                        <Tag>
                          {nodeEnv.source === "config"
                            ? "手动指定"
                            : nodeEnv.source === "PATH"
                              ? "PATH 自动"
                              : "目录扫描"}
                        </Tag>
                        <Typography.Text code copyable={{ text: nodeEnv.path ?? "" }}>
                          {nodeEnv.path}
                        </Typography.Text>
                      </Typography.Text>
                    ) : (
                      <Typography.Text type="danger">{nodeEnv.error}</Typography.Text>
                    )}
                  </Space>
                </Form.Item>
                <Form.Item label="界面主题" extra="「跟随系统」实时响应操作系统的深浅色切换；手动选择后固定不变">
                  <Segmented
                    value={mode}
                    onChange={(v) => setMode(v as ThemeMode)}
                    options={[
                      { label: "跟随系统", value: "system" },
                      { label: "亮色", value: "light" },
                      { label: "暗色", value: "dark" },
                    ]}
                  />
                </Form.Item>
                <Form.Item label="本地模型目录映射（分类 → 路径）">
                  <Form.List name="modelsDirs">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map((field) => (
                          <Space key={field.key} style={{ display: "flex", marginBottom: 4, flexWrap: "wrap", maxWidth: "100%" }} align="baseline">
                            <Form.Item name={[field.name, "category"]} noStyle>
                              <Input placeholder="分类" style={{ width: 160, maxWidth: "100%" }} />
                            </Form.Item>
                            <Form.Item name={[field.name, "path"]} noStyle>
                              <Input placeholder="路径" style={{ width: 340, maxWidth: "100%" }} />
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
                <Form.Item
                  label="提交前预校验（缺节点/缺模型拦截）"
                  name="preflight"
                  extra="off = 不校验（历史行为）；strict = 目标端缺节点/缺模型时直接拦截；ask = 拦截并附可操作选项；auto = 缺模型且本地有副本时自动上传云端卷后放行（缺节点仍拦截）。拦截发生在执行前，零费用。"
                >
                  <Select
                    options={[
                      { value: "off", label: "off —— 不校验（默认）" },
                      { value: "strict", label: "strict —— 拦截" },
                      { value: "ask", label: "ask —— 拦截 + 可操作选项" },
                      { value: "auto", label: "auto —— 自动供给缺失模型" },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  label="云端提交前隐私确认"
                  name="privacyConfirm"
                  valuePropName="checked"
                  extra="开启后，首次把工作流分流到 Modal 会返回一次数据边界说明（图快照/输入文件/元数据/云端日志将离开本机），确认后放行。关闭可撤销，之后云端请求不再提示。"
                >
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
                <Form.Item
                  label="/object_info 合并策略"
                  name="objectInfoMerge"
                  extra="local-first = 同名节点以本地 schema 为准并追加云端独有节点（默认，最稳）；union = 同名节点保留本地结构，但把云端独有模型并入本地模型下拉；local-only = 完全使用本地清单。切换后立即清空合并缓存。"
                >
                  <Select
                    options={[
                      { value: "local-first", label: "local-first —— 本地优先（默认）" },
                      { value: "union", label: "union —— 本地结构 + 云端模型并集" },
                      { value: "local-only", label: "local-only —— 仅本地" },
                    ]}
                  />
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
                    <Form.Item
                      label="代理鉴权 Token ID（wk-…）"
                      name="proxyTokenId"
                      extra="云端端点已开启 Modal 代理鉴权，访问需独立于账户 token 的代理凭证；在 modal.com/settings/proxy-auth-tokens 创建"
                    >
                      <Input placeholder="wk-…" autoComplete="off" />
                    </Form.Item>
                    <Form.Item
                      label="代理鉴权 Token Secret（ws-…）"
                      name="proxyTokenSecret"
                      extra={config?.modal.has_proxy_secret ? `已保存：${config.modal.proxy_secret_masked}（留空表示不修改）` : "云端提交/取图/进度 WS 都依赖它；未配置时 Runner 通道判定为不可用"}
                    >
                      <Input.Password placeholder={config?.modal.has_proxy_secret ? "留空不修改" : "ws-…"} autoComplete="new-password" />
                    </Form.Item>
                    <Space>
                      <Button type="primary" htmlType="submit">保存认证信息</Button>
                      <Popconfirm
                        title="确定清除已保存的 Modal 凭证（含代理鉴权凭证）？"
                        onConfirm={clearModal}
                        disabled={!config?.modal.has_secret && !config?.modal.has_proxy_secret}
                      >
                        <Button danger disabled={!config?.modal.has_secret && !config?.modal.has_proxy_secret}>清除凭证</Button>
                      </Popconfirm>
                    </Space>
                  </Form>
                </Card>
                <AccountProfilesCard />
                <Card size="small" title="执行参数与云端资源规格">
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16, maxWidth: 640 }}
                    message="GPU / CPU / 内存由 Modal 在部署时固化：保存后需点「应用并重新部署」触发一次 modal deploy 才作用于云端容器（不影响进行中的任务）。更换 GPU 型号后请在「护栏与预算」同步更新单价，成本估算才准确。"
                  />
                  <Form
                    layout="vertical"
                    form={execForm}
                    onFinish={saveExec}
                    style={{ maxWidth: 640 }}
                    disabled={isLoading}
                    onValuesChange={(_, all) => refreshPricing(all as ExecFormValues)}
                  >
                    <Form.Item
                      label="数据卷名"
                      name="volume"
                      rules={[{ required: true, message: "请填写数据卷名" }]}
                      extra="模型/节点持久化的 Modal 数据卷；保存后热生效。注意：云端 runner 按部署时挂载的卷运行，改卷名需同步重建部署才能对上"
                    >
                      <Input />
                    </Form.Item>
                    <Form.Item
                      label="GPU 类型"
                      name="gpu"
                      rules={[{ required: true, message: "请选择或填写 GPU 型号" }]}
                      extra="Modal GPU 型号或规格串，例如 L40S / A100-80GB / H100 / H100:2（多卡）"
                    >
                      <AutoComplete
                        options={GPU_OPTIONS}
                        placeholder="L40S"
                        filterOption={(input, option) =>
                          String(option?.value ?? "").toLowerCase().includes(input.toLowerCase())
                        }
                      />
                    </Form.Item>
                    <Space size={12} align="start" wrap>
                      <Form.Item
                        label="CPU 核心数"
                        name="cpu"
                        rules={[{ required: true, message: "请填写 CPU 核心数" }]}
                        extra="物理核心数，可填小数（Modal 最小 0.125）"
                      >
                        <InputNumber min={0.125} max={64} step={1} style={{ width: 160 }} />
                      </Form.Item>
                      <Form.Item
                        label="内存（GB）"
                        name="memoryGb"
                        rules={[{ required: true, message: "请填写内存" }]}
                        extra="Modal 建议每核心 1–8 GB，超出比例可能部署失败"
                      >
                        <InputNumber min={1} max={256} step={1} style={{ width: 160 }} />
                      </Form.Item>
                    </Space>
                    <Form.Item
                      label="保活策略"
                      extra="单容器（max_containers=1），空闲后自动缩容到零；自定义 scaledown_window 规划中"
                    >
                      <Input value="容器空闲自动缩容到 0" disabled />
                    </Form.Item>
                    <Space>
                      <Button type="primary" htmlType="submit">保存</Button>
                      <Button
                        onClick={applyResources}
                        loading={applyingResources}
                        disabled={isLoading || resourceJob?.state === "running"}
                      >
                        应用并重新部署
                      </Button>
                    </Space>
                    <div
                      data-testid="modal-pricing"
                      style={{ marginTop: 16, paddingTop: 12, borderTop: `1px solid ${t.colors.border}` }}
                    >
                      <Space size={24} wrap align="start">
                        <div>
                          <Typography.Text type="secondary">运行预估（每小时）</Typography.Text>
                          <div style={{ fontSize: 24, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                            {pricing ? `$${pricing.total_hourly_usd.toFixed(2)}` : "—"}
                          </div>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {pricing ? `约 $${pricing.total_daily_usd.toFixed(2)} / 天（24h 持续运行）` : "按当前输入实时估算"}
                          </Typography.Text>
                        </div>
                        <div>
                          <Typography.Text type="secondary">GPU</Typography.Text>
                          <div style={{ fontVariantNumeric: "tabular-nums" }}>
                            {pricing?.gpu_hourly_usd == null
                              ? "未匹配参考价"
                              : `$${pricing.gpu_hourly_usd.toFixed(2)} / h`}
                          </div>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {pricing ? `${pricing.gpu_model ?? "未知型号"} × ${pricing.gpu_cards} 卡` : "—"}
                          </Typography.Text>
                        </div>
                        <div>
                          <Typography.Text type="secondary">CPU + 内存</Typography.Text>
                          <div style={{ fontVariantNumeric: "tabular-nums" }}>
                            {pricing ? `$${(pricing.cpu_hourly_usd + pricing.memory_hourly_usd).toFixed(2)} / h` : "—"}
                          </div>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {pricing ? `CPU $${pricing.cpu_hourly_usd.toFixed(2)} + 内存 $${pricing.memory_hourly_usd.toFixed(2)}` : "—"}
                          </Typography.Text>
                        </div>
                      </Space>
                      {pricing && !pricing.priced && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 10 }}
                          message="该 GPU 型号不在内置参考价表中，合计仅含 CPU 与内存；请在 Modal 官网确认 GPU 价格。"
                        />
                      )}
                      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
                        {pricingError
                          ? "预估价获取失败，保存与部署不受影响。"
                          : pricing?.note ?? "按 Modal 参考价估算，非账单金额。"}
                        {pricing ? ` ${pricing.source}` : ""}
                      </Typography.Paragraph>
                    </div>
                    {resourceJob && resourceJob.state !== "idle" && (
                      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
                        部署任务：
                        {resourceJob.state === "running" ? "进行中" : resourceJob.state === "ok" ? "已完成" : "失败"}
                        {resourceJob.label ? ` · ${resourceJob.label}` : ""}
                        {resourceJob.error ? ` · ${resourceJob.error}` : ""}
                      </Typography.Paragraph>
                    )}
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
                  message="「云端不可用动作」「单次运行超时」与「月度预算护栏」均已生效：预算按本月云端估算花费（单价 × 请求墙钟秒数）判定，超限请求自动回落本地或拒绝；使用曲线与快捷设置见仪表盘「额度与成本」卡片。云端最大并发护栏已生效：在途云端任务（排队/执行中）达到上限时，按「云端不可用动作」回落本地或拒绝。注意：预算是 ComfyBridge 本地护栏，只约束经桥接器的请求，不是 Modal 账户级硬上限；请同时在 Modal 平台设置预算与消费告警。"
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
                  <Form.Item
                    label="月度预算上限（USD）"
                    name="monthlyBudgetUsd"
                    extra="0 = 关闭护栏；本月云端估算花费达到上限时触发超限动作。这是本地估算护栏，不阻断绕过桥接器或 Modal 侧其他消费；账户级上限请在 Modal 平台设置。Modal Starter 每月赠 $30 免费额度可作参考"
                  >
                    <InputNumber min={0} step={5} prefix="$" style={{ width: 160 }} />
                  </Form.Item>
                  <Form.Item label="超预算动作" name="onBudgetExceed" extra="回落本地 = 请求继续在本地执行；拒绝 = 直接拦截">
                    <Select
                      options={[
                        { value: "fallback_local", label: "回落本地执行（fallback_local）" },
                        { value: "reject", label: "拒绝请求（reject）" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item
                    label="GPU 单价（USD/容器·小时）"
                    name="rateUsd"
                    extra="随 Modal 价格调整；仅用于估算，不参与计费"
                  >
                    <InputNumber min={0.01} step={0.05} prefix="$" style={{ width: 160 }} />
                  </Form.Item>
                  <Form.Item
                    label="云端最大并发数"
                    name="maxConcurrent"
                    extra="0 = 不限制；在途云端任务（排队/执行中）达到上限时按「云端不可用动作」处理"
                  >
                    <InputNumber min={0} max={10} style={{ width: 160 }} />
                  </Form.Item>
                  <Form.Item
                    label="使用系统代理（出站请求）"
                    name="useSystemProxy"
                    valuePropName="checked"
                    extra="开启后云端 Runner 请求与 Modal CLI 读取 HTTP_PROXY / HTTPS_PROXY 等系统代理变量；本地 ComfyUI 与桥接器内部通信始终直连，不受影响。默认关闭"
                  >
                    <Switch />
                  </Form.Item>
                  <Button type="primary" htmlType="submit">保存</Button>
                </Form>
              </Space>
            ),
          },
          {
            key: "sync",
            label: "设置同步",
            children: <SettingsSyncTab />,
          },
          {
            key: "update",
            label: "检查更新",
            children: <UpdateCheckCard />,
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
      <ConfigWizard open={wizardOpen} onClose={() => setWizardOpen(false)} />
    </Card>
  );
}
