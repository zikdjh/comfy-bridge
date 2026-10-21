import { Alert, Button, Space, Typography } from "antd";
import { ApiError } from "../../api/client";
import { openSettings } from "../../utils/appNavigation";

export interface ErrorGuidance {
  title: string;
  reason: string;
  action: string;
  settingsTab?: string;
}

function rawMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const value = error as { message?: unknown; detail?: unknown };
    if (typeof value.message === "string") return value.message;
    if (typeof value.detail === "string") return value.detail;
  }
  return String(error ?? "未知错误");
}

/** 高频失败场景 → 用户下一步动作。原始错误只作详情，不作为主提示。 */
export function explainError(error: unknown): ErrorGuidance {
  const message = rawMessage(error);
  const lower = message.toLowerCase();
  const status = error instanceof ApiError ? error.status : null;

  if (status === 401 || /not_logged_in|token not found|unauthori[sz]ed|未登录|认证失败/.test(lower)) {
    return {
      title: "Modal 凭证不可用",
      reason: "当前凭证无效或已失效，云端请求没有发出。",
      action: "到「设置 → Modal」重新填写 Token ID 和 Token Secret，保存后再重试。",
      settingsTab: "modal",
    };
  }
  if (/not_installed|sdk 不可用|modal python sdk|no module named ['\"]?modal/.test(lower)) {
    return {
      title: "Modal 运行组件缺失",
      reason: "当前运行环境缺少 Modal Python SDK。",
      action: "请使用完整整合包，或安装 modal>=1.5 后重启应用。",
      settingsTab: "modal",
    };
  }
  if (/proxy|http_proxy|https_proxy|tunnel|代理/.test(lower)) {
    return {
      title: "代理连接失败",
      reason: "云端请求没有通过当前代理建立连接。",
      action: "确认代理地址可用；不需要代理时在「设置 → 护栏与预算」关闭「使用系统代理」再重试。",
      settingsTab: "budget",
    };
  }
  if (/rate.?limit|429|限流|too many requests/.test(lower)) {
    return {
      title: "Modal 平台正在限流",
      reason: "请求频率过高，或控制面正在退避重试。",
      action: "等待 30–60 秒后重试；不要连续点击，以免拉长限流时间。",
    };
  }
  if (status === 409 || /already exists|in progress|conflict|进行中|冲突|占用/.test(lower)) {
    return {
      title: "已有任务或资源正在使用",
      reason: "当前操作与另一个进行中的任务发生了冲突。",
      action: "先到顶部「任务」抽屉确认进度；结束或取消后再重试。",
    };
  }
  if (/eacces|eperm|permission|access denied|权限|拒绝访问|winerror 5/.test(lower)) {
    return {
      title: "文件或目录没有写入权限",
      reason: "目标路径被占用，或当前用户无权修改。",
      action: "关闭占用该文件的程序，或改到当前用户可写的目录后重试。",
      settingsTab: "local",
    };
  }
  if (/\bport\b|eaddrinuse|地址已被使用|端口/.test(lower)) {
    return {
      title: "本机端口冲突",
      reason: "另一个程序正在占用 ComfyBridge 或 ComfyUI 需要的端口。",
      action: "关闭占用端口的程序；桌面模式重启后会自动尝试备用端口。",
      settingsTab: "bridge",
    };
  }
  if (/timeout|timed out|超时|etimedout/.test(lower)) {
    return {
      title: "连接超时",
      reason: "目标服务在规定时间内没有响应。",
      action: "确认网络与代理可用，然后重试；Modal 冷启动期间请先看「任务」进度。",
    };
  }
  if (/enotfound|econnrefused|network|failed to fetch|网络|连接失败|unreachable/.test(lower)) {
    return {
      title: "网络连接失败",
      reason: "ComfyBridge 无法连接到目标服务。",
      action: "检查网络和代理设置；本地服务还需确认 ComfyUI 已在运行。",
      settingsTab: "budget",
    };
  }
  if (/enoent|not found|不存在|missing|no such file/.test(lower)) {
    return {
      title: "文件或目录不存在",
      reason: "配置中的路径可能已经移动、删除或改名。",
      action: "到「设置 → 本地」重新扫描并校验 ComfyUI 根目录。",
      settingsTab: "local",
    };
  }
  if (/dependenc|requirement|约束|依赖/.test(lower)) {
    return {
      title: "节点依赖存在冲突",
      reason: "当前节点包的 Python 依赖无法同时满足，云端镜像不具备可重复构建条件。",
      action: "按下方约束调整或停用冲突节点，再重新执行依赖预检与镜像重建。",
    };
  }
  if (/\bspace\b|disk full|enospc|磁盘|空间不足/.test(lower)) {
    return {
      title: "磁盘空间不足",
      reason: "操作无法继续写入。",
      action: "清理输出目录或系统临时目录后重试。",
      settingsTab: "local",
    };
  }
  if (status != null && status >= 500) {
    return {
      title: "服务暂时不可用",
      reason: "桥接器或云端返回了服务端错误。",
      action: "稍后重试；连续失败时到「系统状态」查看最近错误并导出诊断信息。",
      settingsTab: "status",
    };
  }
  return {
    title: "操作没有完成",
    reason: "发生了未归类的错误。",
    action: "查看下方详情；需要排查时到「系统状态」检查最近错误。",
  };
}

export function ActionableError({
  error,
  onOpenSettings,
  compact = false,
}: {
  error: unknown;
  onOpenSettings?: (tab: string) => void;
  compact?: boolean;
}) {
  const guidance = explainError(error);
  const detail = rawMessage(error);
  return (
    <Alert
      type="error"
      showIcon
      message={guidance.title}
      description={
        <div style={{ fontSize: 12 }}>
          <div>{guidance.reason}</div>
          <div style={{ marginTop: 4 }}>{guidance.action}</div>
          {!compact && (
            <Typography.Paragraph type="secondary" copyable={{ text: detail }} style={{ margin: "6px 0 0", fontSize: 11 }}>
              详情：{detail}
            </Typography.Paragraph>
          )}
          {guidance.settingsTab && onOpenSettings && (
            <Button
              size="small"
              style={{ marginTop: 8 }}
              onClick={() => onOpenSettings(guidance.settingsTab!)}
            >
              前往设置
            </Button>
          )}
        </div>
      }
    />
  );
}

export function SettingsErrorAlert({ error, compact = false }: { error: unknown; compact?: boolean }) {
  return <ActionableError error={error} onOpenSettings={openSettings} compact={compact} />;
}

export function InlineError({ error }: { error: unknown }) {
  const guidance = explainError(error);
  const detail = rawMessage(error);
  return (
    <Space direction="vertical" size={2} style={{ display: "flex" }}>
      <Typography.Text type="danger" style={{ fontSize: 12 }}>
        {guidance.title}：{guidance.action}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        {detail}
      </Typography.Text>
    </Space>
  );
}

/** Modal 内的详细错误：上方给下一步动作，下方保留原始失败清单。 */
export function ErrorDetails({ error, detail }: { error: unknown; detail?: string }) {
  return (
    <div>
      <SettingsErrorAlert error={error} compact />
      {detail && (
        <pre
          style={{
            maxHeight: 240,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            fontSize: 12,
            margin: "12px 0 0",
          }}
        >
          {detail}
        </pre>
      )}
    </div>
  );
}
