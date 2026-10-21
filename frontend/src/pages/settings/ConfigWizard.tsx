import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Space,
  Steps,
  Tag,
  Typography,
  message,
} from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, FireOutlined } from "@ant-design/icons";
import { useConfig, useSaveConfig, validateComfyRoot } from "../../api/config";
import { useHealth, warmupModal } from "../../api/health";
import { ComfyScanModal } from "./ComfyScanModal";
import { useDarkMode } from "../../hooks/useDarkMode";
import { SettingsErrorAlert } from "../../components/common/ActionableError";

/** 首次配置向导完成标记（localStorage，按浏览器记忆；设置页可随时重跑） */
export const WIZARD_DONE_KEY = "cb_wizard_done";

interface Props {
  open: boolean;
  onClose: () => void;
}

function CheckRow({
  ok,
  title,
  detail,
  action,
}: {
  ok: boolean;
  title: string;
  detail: string;
  action?: { label: string; onClick: () => void };
}) {
  const { tokens: t } = useDarkMode();
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "8px 0", borderBottom: `1px dashed ${t.colors.border}` }}>
      {ok ? (
        <CheckCircleOutlined style={{ color: t.colors.accent, fontSize: 16, marginTop: 2 }} />
      ) : (
        <CloseCircleOutlined style={{ color: t.colors.danger, fontSize: 16, marginTop: 2 }} />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div>
          <b style={{ fontSize: 13 }}>{title}</b>
          <Tag color={ok ? "green" : "orange"} style={{ marginLeft: 8 }}>
            {ok ? "就绪" : "未配置"}
          </Tag>
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 12, wordBreak: "break-all" }}>
          {detail}
        </Typography.Text>
      </div>
      {action && !ok && (
        <Button size="small" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}

/** 首次配置向导（M4-3）：检测 → Modal 凭证 → 本地 ComfyUI → 云端 Runner → 完成。
 * 全部步骤可跳过，复用设置页同款 PUT /config 热生效保存。 */
export function ConfigWizard({ open, onClose }: Props) {
  const [step, setStep] = useState(0);
  const { data: config, refetch: refetchConfig } = useConfig();
  const { data: health, refetch: refetchHealth } = useHealth();
  const saveConfig = useSaveConfig();
  const [modalForm] = Form.useForm();
  const [localForm] = Form.useForm();
  const [runnerForm] = Form.useForm();
  const [scanOpen, setScanOpen] = useState(false);
  const [checkingRoot, setCheckingRoot] = useState(false);
  const [saving, setSaving] = useState(false);
  const [warming, setWarming] = useState(false);
  const [lastError, setLastError] = useState<unknown>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const hasSecret = !!config?.modal.has_secret;
  const hasRoot = !!config?.local.comfy_root?.trim();
  const channelOk = !!health?.modal.online;

  useEffect(() => {
    if (!open || !config) return;
    setLastError(null);
    setStep(0);
    modalForm.setFieldsValue({ tokenId: config.modal.token_id, environment: config.modal.environment, tokenSecret: "" });
    localForm.setFieldsValue({ comfyRoot: config.local.comfy_root });
    runnerForm.setFieldsValue({ webUrl: config.modal.web_url ?? "", proxyTokenId: config.modal.proxy_token_id, proxyTokenSecret: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, config]);

  useEffect(() => {
    if (!open) return;
    const focusTimer = window.setTimeout(() => {
      contentRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(focusTimer);
  }, [open]);

  const finish = () => {
    try {
      localStorage.setItem(WIZARD_DONE_KEY, "1");
    } catch {
      /* 隐私模式等场景下不可用，仅影响下次不再自动弹出 */
    }
    onClose();
  };

  const saveModalCreds = async (values: { tokenId: string; tokenSecret?: string; environment?: string }) => {
    setSaving(true);
    setLastError(null);
    try {
      await saveConfig({
        modal: {
          token_id: values.tokenId.trim(),
          environment: values.environment?.trim() ?? "",
          ...(values.tokenSecret?.trim() ? { token_secret: values.tokenSecret.trim() } : {}),
        },
      });
      message.success("Modal 凭证已保存并热生效");
      refetchHealth();
      setStep(2);
    } catch (err) {
      setLastError(err);
    } finally {
      setSaving(false);
    }
  };

  const checkRoot = async () => {
    const path = String(localForm.getFieldValue("comfyRoot") ?? "").trim();
    if (!path) {
      message.warning("请先填写 ComfyUI 根目录");
      return;
    }
    setCheckingRoot(true);
    setLastError(null);
    try {
      const report = await validateComfyRoot(path);
      if (!report.exists) setLastError(`目录不存在：${path}`);
      else if (!report.valid) setLastError(`不是可用的 ComfyUI 目录：缺少 ${report.required_missing.join("、")}`);
      else message.success(`校验通过${report.version ? `（v${report.version}）` : ""}`);
    } catch (err) {
      setLastError(err);
    } finally {
      setCheckingRoot(false);
    }
  };

  const saveRoot = async (values: { comfyRoot: string }) => {
    setSaving(true);
    setLastError(null);
    try {
      await saveConfig({ local: { comfy_root: values.comfyRoot.trim() } });
      message.success("ComfyUI 根目录已保存并热生效");
      setStep(3);
    } catch (err) {
      setLastError(err);
    } finally {
      setSaving(false);
    }
  };

  const saveRunner = async (values: { webUrl?: string; proxyTokenId?: string; proxyTokenSecret?: string }) => {
    setSaving(true);
    setLastError(null);
    try {
      await saveConfig({
        modal: {
          ...(values.webUrl?.trim() ? { web_url: values.webUrl.trim() } : {}),
          ...(values.proxyTokenId?.trim() ? { proxy_token_id: values.proxyTokenId.trim() } : {}),
          ...(values.proxyTokenSecret?.trim() ? { proxy_token_secret: values.proxyTokenSecret.trim() } : {}),
        },
      });
      message.success("Runner 通道参数已保存，正在检测通道…");
      await refetchHealth();
      setStep(4);
    } catch (err) {
      setLastError(err);
    } finally {
      setSaving(false);
    }
  };

  const doWarmup = async () => {
    setWarming(true);
    setLastError(null);
    try {
      const r = await warmupModal();
      if (r.ok) message.success(`云端容器已就绪（耗时 ${r.seconds}s）`);
      else setLastError(r.error ?? "预热失败：未知错误");
      refetchHealth();
    } catch (err) {
      setLastError(err);
    } finally {
      setWarming(false);
    }
  };

  const localDetail = hasRoot
    ? `${config?.local.comfy_root}${health?.local.online ? `（在线，${health.local.version ?? "未知版本"}）` : "（目录已配置，服务未在线——不影响云端使用）"}`
    : "未指定 ComfyUI 根目录；纯云端使用也建议配置（工作流/节点同步依赖它）";
  const channelDetail = channelOk
    ? `通道就绪${health?.modal.web_url ? `：${health.modal.web_url}` : ""}`
    : (health?.modal.reason ?? "检测中…");

  return (
    <>
      <Modal
        title="ComfyBridge 首次配置向导"
        open={open}
        onCancel={finish}
        width={640}
        footer={
          <Space>
            {step > 0 && step < 4 && <Button onClick={() => setStep(step - 1)}>上一步</Button>}
            {step < 4 && (
              <Button onClick={() => setStep(step + 1)}>
                {step === 0 ? "开始配置" : "跳过此步"}
              </Button>
            )}
            {step === 4 ? (
              <Button type="primary" onClick={finish}>
                完成
              </Button>
            ) : null}
          </Space>
        }
        destroyOnHidden
        keyboard
        afterOpenChange={(visible) => {
          if (visible) contentRef.current?.focus();
        }}
      >
        <Steps
          size="small"
          current={step}
          onChange={(s) => setStep(s)}
          items={[
            { title: "检测" },
            { title: "Modal 凭证" },
            { title: "本地 ComfyUI" },
            { title: "云端 Runner" },
            { title: "完成" },
          ]}
          style={{ marginBottom: 16 }}
        />

        <div ref={contentRef} tabIndex={-1} style={{ outline: "none" }} data-testid="config-wizard-content">
        {!!lastError && (
          <div style={{ marginBottom: 12 }} data-testid="wizard-actionable-error">
            <SettingsErrorAlert error={lastError} compact />
          </div>
        )}
        {step === 0 && (
          <div>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              按需完成三项配置即可开始使用；全部可跳过，之后在「设置」页随时补齐。
            </Typography.Paragraph>
            <CheckRow
              ok={hasSecret}
              title="Modal 云端凭证"
              detail={
                hasSecret
                  ? `Token 已保存（secret 脱敏 ${config?.modal.secret_masked}）；明文可在「设置 → Modal」查看或更换`
                  : "云端 L40S 执行、模型/节点卷同步都依赖它"
              }
              action={{ label: "去配置", onClick: () => setStep(1) }}
            />
            <CheckRow
              ok={hasRoot}
              title="本地 ComfyUI 根目录"
              detail={localDetail}
              action={{ label: "去配置", onClick: () => setStep(2) }}
            />
            <CheckRow
              ok={channelOk}
              title="云端 Runner 通道"
              detail={channelDetail}
              action={{ label: "去配置", onClick: () => setStep(3) }}
            />
          </div>
        )}

        {step === 1 && (
          <Form form={modalForm} layout="vertical" onFinish={saveModalCreds}>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="Token 来自 modal.com/settings（API Tokens）或命令行 modal token new；保存到 ~/.comfybridge/config.toml，接口只回显脱敏值。"
            />
            <Form.Item label="Token ID（ak-…）" name="tokenId" rules={[{ required: true, message: "请填写 Token ID" }]}>
              <Input placeholder="ak-…" autoComplete="off" />
            </Form.Item>
            <Form.Item
              label="Token Secret（as-…）"
              name="tokenSecret"
              extra={hasSecret ? `已保存：${config?.modal.secret_masked}（留空 = 不修改）` : undefined}
              rules={hasSecret ? undefined : [{ required: true, message: "请填写 Token Secret" }]}
            >
              <Input.Password placeholder={hasSecret ? "留空不修改" : "as-…"} autoComplete="new-password" />
            </Form.Item>
            <Form.Item label="环境 environment（可选）" name="environment" extra="留空使用默认环境">
              <Input autoComplete="off" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存并下一步
            </Button>
          </Form>
        )}

        {step === 2 && (
          <Form form={localForm} layout="vertical" onFinish={saveRoot}>
            <Form.Item
              label="ComfyUI 根目录（含 main.py 的仓库根）"
              required
              extra="保存时后端会强制校验完整性；工作流与节点同步、本地路由都依赖该目录"
              style={{ marginBottom: 12 }}
            >
              <Space.Compact style={{ width: "100%" }}>
                <Form.Item
                  name="comfyRoot"
                  noStyle
                  rules={[{ required: true, message: "请填写 ComfyUI 根目录" }]}
                >
                  <Input placeholder="如 E:\AI\ComfyUI" />
                </Form.Item>
                <Button onClick={() => setScanOpen(true)}>扫描本机</Button>
                <Button loading={checkingRoot} onClick={checkRoot}>
                  校验
                </Button>
              </Space.Compact>
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>
              保存并下一步
            </Button>
          </Form>
        )}

        {step === 3 && (
          <div>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={
                <div style={{ fontSize: 12 }}>
                  <div>1. 在 comfy-bridge 目录执行：<code>modal deploy modal-runner/runner.py</code></div>
                  <div>2. 复制输出里的 web_server 端点（形如 https://xxx.modal.run）填到下方</div>
                  <div>3. 在 modal.com/settings/proxy-auth-tokens 创建代理鉴权 Token（wk-/ws-，与账户 token 不同族）</div>
                </div>
              }
            />
            <Form form={runnerForm} layout="vertical" onFinish={saveRunner}>
              <Form.Item
                label="Runner 端点 web_url"
                name="webUrl"
                extra={hasSecret ? undefined : "提示：先完成上一步的 Modal 凭证，部署才能使用当前账号"}
              >
                <Input placeholder="https://xxx.modal.run" autoComplete="off" />
              </Form.Item>
              <Form.Item label="代理鉴权 Token ID（wk-…）" name="proxyTokenId">
                <Input placeholder="wk-…" autoComplete="off" />
              </Form.Item>
              <Form.Item
                label="代理鉴权 Token Secret（ws-…）"
                name="proxyTokenSecret"
                extra={config?.modal.has_proxy_secret ? `已保存：${config.modal.proxy_secret_masked}（留空 = 不修改）` : undefined}
              >
                <Input.Password placeholder={config?.modal.has_proxy_secret ? "留空不修改" : "ws-…"} autoComplete="new-password" />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={saving}>
                保存并检测
              </Button>
            </Form>
          </div>
        )}

        {step === 4 && (
          <div>
            <CheckRow ok={hasSecret} title="Modal 云端凭证" detail={hasSecret ? "Token 已保存" : "仍未配置——可稍后到「设置 → Modal」补齐"} />
            <CheckRow ok={hasRoot} title="本地 ComfyUI 根目录" detail={localDetail} />
            <CheckRow ok={channelOk} title="云端 Runner 通道" detail={channelDetail} />
            {channelOk ? (
              <Alert
                type="success"
                showIcon
                style={{ marginTop: 12 }}
                message="配置完成！出图请求会按路由规则自动分配本地/云端执行。"
              />
            ) : (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 12 }}
                message="Runner 通道未就绪：纯本地模式也可用；需要云端时按上一步指引部署 runner.py。"
              />
            )}
            <Space style={{ marginTop: 12 }}>
              <Button icon={<FireOutlined />} loading={warming} disabled={!channelOk} onClick={doWarmup}>
                预热云端容器
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                空转约 $1.95/小时，闲置自动缩容到零
              </Typography.Text>
            </Space>
          </div>
        )}
        </div>
      </Modal>
      <ComfyScanModal
        open={scanOpen}
        onClose={() => setScanOpen(false)}
        onPick={(path) => {
          localForm.setFieldsValue({ comfyRoot: path });
          setScanOpen(false);
        }}
      />
    </>
  );
}
