import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { CheckOutlined, CopyOutlined, PauseCircleOutlined, PlayCircleOutlined, PoweroffOutlined, ReloadOutlined, ThunderboltOutlined, UndoOutlined } from "@ant-design/icons";
import {
  restartLocalProc, saveLaunch, startLocalProc, stopLocalProc, stopModalContainers,
  useLaunch, useLocalProcState, useModalContainers,
  type LaunchView, type LocalProcState, type ProcResult,
} from "../../api/launch";
import { warmupModal } from "../../api/health";
import { LAUNCH_FLAGS, type LaunchFlagDef, type Target } from "./launchFlags";
import { useDarkMode, type BridgeTokens } from "../../hooks/useDarkMode";

/** 启用状态：true=无值开关；字符串=取值（required/optional 参数） */
export type Selection = Record<string, string | true>;

export interface PanelState {
  sel: Selection;
  custom: string;
}

function parseTokens(tokens: string[]): PanelState {
  const sel: Selection = {};
  const custom: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (!t) continue;
    const def = LAUNCH_FLAGS.find((f) => f.name === t);
    if (def) {
      const next = tokens[i + 1];
      if (def.valueMode !== "none" && next && !next.startsWith("--")) {
        sel[def.name] = next;
        i++;
      } else {
        sel[def.name] = true;
      }
      continue;
    }
    // 兼容 --flag=value 形式
    const eq = t.indexOf("=");
    const byEq = t.startsWith("--") && eq > 0 ? LAUNCH_FLAGS.find((f) => f.name === t.slice(0, eq)) : undefined;
    if (byEq) {
      sel[byEq.name] = t.slice(eq + 1);
      continue;
    }
    custom.push(t);
  }
  return { sel, custom: custom.join(" ") };
}

function buildTokens(state: PanelState): string[] {
  const out: string[] = [];
  for (const def of LAUNCH_FLAGS) {
    const v = state.sel[def.name];
    if (v === undefined) continue;
    if (def.valueMode === "none") {
      out.push(def.name);
    } else if (typeof v === "string" && v.trim()) {
      out.push(def.name, v.trim());
    } else if (def.valueMode === "optional") {
      out.push(def.name); // 可选值留空：只传开关本身
    }
    // required 但值为空：视为未配置，跳过
  }
  out.push(...state.custom.trim().split(/\s+/).filter(Boolean));
  return out;
}

interface TargetPanelProps {
  target: Target;
  launch: LaunchView;
  state: PanelState;
  onChange: (next: PanelState) => void;
  onReset: () => void;
}

function fmtDuration(ms?: number | null): string {
  if (!ms || ms < 0) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${s % 60}s`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

function showProcError(r: ProcResult, t: BridgeTokens) {
  Modal.error({
    title: r.error || "操作失败",
    width: 620,
    content: r.log_tail?.length ? (
      <pre style={{ maxHeight: 240, overflow: "auto", background: t.colors.console.background, color: t.colors.console.text, padding: 10, borderRadius: 8, fontSize: 12, marginTop: 12, whiteSpace: "pre-wrap" }}>
        {r.log_tail.join("\n")}
      </pre>
    ) : null,
  });
}

/** 命令展示块：复制图标内嵌块右上角（GitHub 风格），不再游离在代码块外 */
function CodeBlock({ text, dark }: { text: string; dark?: boolean }) {
  const { tokens: t } = useDarkMode();
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      message.error("复制失败");
    }
  };
  return (
    <div style={{ position: "relative" }}>
      <pre
        style={{
          background: dark ? t.colors.console.background : t.colors.surfaceWash,
          color: dark ? t.colors.console.text : undefined,
          padding: dark ? "10px 40px 10px 12px" : "8px 40px 8px 12px",
          borderRadius: 8,
          fontSize: 12,
          margin: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}
      >
        {text}
      </pre>
      <Tooltip title={copied ? "已复制" : "复制命令"}>
        <Button
          size="small"
          type="text"
          aria-label="复制命令"
          icon={copied ? <CheckOutlined style={{ color: t.colors.accent }} /> : <CopyOutlined />}
          onClick={onCopy}
          style={{ position: "absolute", top: 3, right: 3 }}
        />
      </Tooltip>
    </div>
  );
}

/** 本地进程控制条：桥接器直接启动 / 停止 / 重启本地 ComfyUI */
function LocalControl() {
  const { tokens: t } = useDarkMode();
  const { data: st, refetch } = useLocalProcState();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<"start" | "stop" | "restart" | null>(null);

  const refresh = () => {
    refetch();
    qc.invalidateQueries({ queryKey: ["launch"] });
    qc.invalidateQueries({ queryKey: ["health"] });
  };

  const report = (label: string, r: ProcResult) => {
    if (r.ok) message.success(label + (r.seconds != null ? `（${r.seconds}s）` : ""));
    else showProcError(r, t);
  };

  const doStart = async () => {
    setBusy("start");
    let done = false;
    try {
      const r = await startLocalProc(false);
      if (r.ok) {
        done = true;
        report("本地 ComfyUI 已就绪", r);
      } else if (r.conflict) {
        Modal.confirm({
          title: "端口被外部实例占用",
          content: `${r.error} 现在结束该进程并由桥接器接管启动？`,
          okText: "接管启动", okButtonProps: { danger: true }, cancelText: "取消",
          onOk: async () => {
            const rr = await startLocalProc(true);
            report("本地 ComfyUI 已就绪（接管完成）", rr);
            refresh();
          },
        });
      } else showProcError(r, t);
    } catch (err) {
      message.error(`启动失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
      if (done) refresh();
    }
  };

  const doStop = async (force: boolean) => {
    setBusy("stop");
    try {
      const r = await stopLocalProc(force);
      if (r.ok) message.success(r.stopped ? `本地 ComfyUI 已停止（PID ${r.pid}）` : r.detail || "本地 ComfyUI 未在运行");
      else showProcError(r, t);
    } catch (err) {
      message.error(`停止失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const onStop = () => {
    if (st?.running && !st.owned) {
      Modal.confirm({
        title: "结束外部托管实例？",
        content: `该实例由 ${st.managed_by ?? "外部进程"} 启动（PID ${st.pid}）。结束后 Desktop 会显示服务已停止；若其自动重启抢占端口，桥接状态会如实显示。建议先退出 Desktop 再操作。`,
        okText: "强制结束", okButtonProps: { danger: true }, cancelText: "取消",
        onOk: () => doStop(true),
      });
    } else doStop(false);
  };

  const doRestart = async (takeover: boolean) => {
    setBusy("restart");
    try {
      const r = await restartLocalProc(takeover);
      if (r.ok) report("本地 ComfyUI 已重启就绪", r);
      else showProcError(r, t);
    } catch (err) {
      message.error(`重启失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const onRestart = () => {
    if (st?.running && !st.owned) {
      Modal.confirm({
        title: "接管并重启？",
        content: `当前实例由 ${st.managed_by ?? "外部进程"} 托管（PID ${st.pid}）。桥接器将结束它并以「当前命令 + 表选追加参数」重新启动。建议先退出 Desktop，避免其自动重启抢占端口。`,
        okText: "结束并接管", okButtonProps: { danger: true }, cancelText: "取消",
        onOk: () => doRestart(true),
      });
    } else {
      Modal.confirm({
        title: "重启本地 ComfyUI？",
        content: "进行中的生成任务会中断；重启后表选追加参数生效。",
        okText: "重启", cancelText: "取消",
        onOk: () => doRestart(false),
      });
    }
  };

  const running = !!st?.running;
  const starting = !!st?.starting;
  const owned = !!st?.owned;
  return (
    <Space size={8} wrap style={{ marginBottom: 12 }}>
      {starting ? (
        <Tag color="orange" style={{ marginInlineEnd: 0 }}>启动中{st?.pid ? ` · PID ${st.pid}` : ""}</Tag>
      ) : running ? (
        <Tag color={owned ? "green" : "blue"} style={{ marginInlineEnd: 0 }}>
          {owned ? "运行中 · 桥接管管" : `运行中 · ${st?.managed_by ?? "外部托管"}`}
        </Tag>
      ) : (
        <Tag style={{ marginInlineEnd: 0 }}>未运行</Tag>
      )}
      {running && owned && st?.uptime_ms != null && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>已运行 {fmtDuration(st.uptime_ms)}</Typography.Text>
      )}
      <Button
        size="small" icon={<PlayCircleOutlined />}
        type={running || starting ? "default" : "primary"}
        disabled={running || starting} loading={busy === "start"}
        onClick={doStart}
      >启动</Button>
      <Button
        size="small" icon={<PoweroffOutlined />} danger={running && (owned || starting)}
        disabled={!running && !starting} loading={busy === "stop"}
        onClick={onStop}
      >停止</Button>
      <Button
        size="small" icon={<ReloadOutlined />}
        disabled={!running && !starting} loading={busy === "restart"}
        onClick={onRestart}
      >重启</Button>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        启动命令 = 当前命令 + 表选参数；启动日志在 ~/.comfybridge/local_comfyui.log
      </Typography.Text>
    </Space>
  );
}

/** 云端容器控制条：运行中容器数（M4-2 面板）/ 预热启动 / 停止容器 */
function ModalControl() {
  const { tokens: t } = useDarkMode();
  const [busy, setBusy] = useState<"warmup" | "stop" | null>(null);
  const { data: containers, refetch: refetchContainers, isFetching: containersFetching } = useModalContainers();

  const doWarmup = async () => {
    setBusy("warmup");
    try {
      const r = await warmupModal();
      if (r.ok) message.success(`云端 ComfyUI 已就绪（${r.seconds}s）`);
      else message.error(`预热失败：${r.error || "未知错误"}`);
    } catch (err) {
      message.error(`预热失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
      refetchContainers();
    }
  };

  const doStop = async () => {
    setBusy("stop");
    try {
      const r = await stopModalContainers();
      if (r.ok) {
        message.success(
          r.running > 0
            ? `已停止 ${r.stopped.length}/${r.running} 个容器，新参数下次冷启动生效`
            : "当前没有运行中的云端容器（新参数下次冷启动自动生效）",
        );
      } else {
        message.error(`停止容器失败：${(r.failed ?? []).join("；") || r.detail || "未知错误"}`);
      }
    } catch (err) {
      message.error(`停止容器失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
      refetchContainers();
    }
  };

  const count = containers?.count;
  const containerTip = containers?.ok
    ? (containers.containers.length
        ? containers.containers.map((c) => `${c.id.slice(0, 12)}…${c.started_at ? `（${c.started_at} 启动）` : ""}`).join("；")
        : (containers.detail ?? "当前没有运行中的云端容器"))
    : (containers?.detail ?? "容器数检测中…（modal container list，约 1~3s）");
  return (
    <Space size={8} wrap style={{ marginBottom: 12 }}>
      <Tag color="geekblue" style={{ marginInlineEnd: 0 }}>容器冷启动自动生效</Tag>
      <Tooltip title={containerTip}>
        <Tag color={count ? "processing" : "default"} style={{ marginInlineEnd: 0 }}>
          运行中容器 {count ?? "—"}
        </Tag>
      </Tooltip>
      <Button size="small" type="text" icon={<ReloadOutlined />} aria-label="刷新容器数" loading={containersFetching} onClick={() => void refetchContainers()} />
      <Button size="small" icon={<ThunderboltOutlined />} loading={busy === "warmup"} onClick={doWarmup}>
        预热启动
      </Button>
      <Popconfirm
        title="停止云端容器？"
        description="运行中的容器会被结束，进行中的任务中断；停止后新参数下次冷启动即生效。"
        okText="停止容器" okButtonProps={{ danger: true }} cancelText="取消"
        onConfirm={doStop}
      >
        <Button size="small" icon={<PauseCircleOutlined />} loading={busy === "stop"}>停止容器</Button>
      </Popconfirm>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        预热会拉起新容器（冷启动约 $0.02）
      </Typography.Text>
    </Space>
  );
}

function TargetPanel({ target, launch, state, onChange, onReset }: TargetPanelProps) {
  const { tokens: t } = useDarkMode();
  const { sel, custom } = state;

  const setOne = (name: string, value: string | true | undefined) => {
    const selNext = { ...sel };
    if (value === undefined) delete selNext[name];
    else selNext[name] = value;
    // 互斥组（官方 parser 的 exclusive group）：启用一个，自动关闭同组其他参数
    const def = LAUNCH_FLAGS.find((f) => f.name === name);
    if (def?.group && value !== undefined) {
      for (const f of LAUNCH_FLAGS) {
        if (f.group === def.group && f.name !== name) delete selNext[f.name];
      }
    }
    onChange({ sel: selNext, custom });
  };

  const tokens = buildTokens(state);
  const base = target === "local"
    ? launch.local.command
    : `python main.py ${launch.modal.base_args.join(" ")}`;
  const preview = `${base}${tokens.length ? " " + tokens.join(" ") : ""}`;

  const flags = useMemo(() => LAUNCH_FLAGS.filter((f) => f.targets.includes(target)), [target]);

  const columns: ColumnsType<LaunchFlagDef> = [
    {
      title: "启用 / 取值",
      key: "on",
      width: 190,
      render: (_, def) => {
        const v = sel[def.name];
        const on = v !== undefined;
        if (def.valueMode === "none") {
          return <Switch size="small" checked={on} onChange={(checked) => setOne(def.name, checked ? true : undefined)} />;
        }
        return (
          <Space size={6}>
            <Switch
              size="small"
              checked={on}
              onChange={(checked) => setOne(def.name, checked ? (def.options ? (def.options[0] ?? "") : "") : undefined)}
            />
            {def.options ? (
              <Select
                size="small"
                disabled={!on}
                style={{ width: 118 }}
                placeholder="选择"
                value={typeof v === "string" && v ? v : undefined}
                onChange={(val) => setOne(def.name, val)}
                options={def.options.map((o) => ({ value: o, label: o }))}
              />
            ) : (
              <Input
                size="small"
                disabled={!on}
                style={{ width: 118 }}
                placeholder={def.placeholder ?? "取值"}
                value={typeof v === "string" ? v : ""}
                onChange={(e) => setOne(def.name, e.target.value)}
              />
            )}
          </Space>
        );
      },
    },
    {
      title: "参数",
      dataIndex: "name",
      width: 225,
      render: (name: string) => (
        <Tooltip title={`${name}${sel[name] === true ? "（可选值留空，仅传开关）" : ""}`}>
          <Typography.Text code style={{ fontSize: 12 }}>{name}</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: "取值",
      key: "value",
      width: 80,
      render: (_, def) =>
        def.valueMode === "none" ? <Tag style={{ marginInlineEnd: 0 }}>开关</Tag> : <Tag color={def.valueMode === "required" ? "orange" : "default"} style={{ marginInlineEnd: 0 }}>{def.valueMode === "required" ? "必填" : "可选"}</Tag>,
    },
    {
      title: "说明（官方 help）",
      dataIndex: "desc",
      ellipsis: { showTitle: false },
      render: (d: string) => (
        <Tooltip title={d} placement="topLeft">
          <span style={{ color: t.colors.textMuted }}>{d}</span>
        </Tooltip>
      ),
    },
    { title: "分类", dataIndex: "category", width: 100 },
  ];

  return (
    <>
      {target === "local" ? (
        launch.local.control?.owned ? (
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 12 }}
            message={`本地实例由桥接器启动管理（PID ${launch.local.control.pid}）。追加参数点击上方「重启」即生效；停止后可随时「启动」。`}
          />
        ) : launch.local.detected ? (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={`本地实例由 ${launch.local.managed_by ?? "外部进程"} 托管（实测 PID ${launch.local.pid}）。桥接器可通过上方控制条启停/接管；接管前建议先退出 Desktop，避免其自动重启抢占端口。`}
          />
        ) : (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="未探测到 :8188 监听进程，下方为回退命令；可点击上方「启动」由桥接器拉起本地 ComfyUI。" />
        )
      ) : (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`保存后经 modal volume put 写入数据卷 ${launch.modal.volume}:${launch.modal.volume_file}，容器下次冷启动自动追加（闲置缩容后即生效）；正在运行的容器不受影响。GPU/内存/容器数等资源参数在 modal-runner/runner.py 的 @app.function 配置中，不在此表。`}
        />
      )}

      <div style={{ marginBottom: 10 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {target === "local" ? "当前启动命令（实测进程命令行）" : "默认命令（runner.py 固定基础参数）"}
        </Typography.Text>
        <CodeBlock text={base} />
      </div>
      <Table<LaunchFlagDef>
        columns={columns}
        dataSource={flags}
        rowKey="name"
        size="small"
        pagination={false}
        scroll={{ y: 360, x: true }}
      />

      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>自定义追加参数（空格分隔，追加在表选参数之后）</Typography.Text>
        <Input
          placeholder="--example-flag value"
          value={custom}
          onChange={(e) => onChange({ sel, custom: e.target.value })}
          style={{ marginTop: 4 }}
        />
      </div>

      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>完整命令预览</Typography.Text>
        <CodeBlock text={preview} dark />
      </div>

      <Button size="small" icon={<UndoOutlined />} style={{ marginTop: 10 }} onClick={onReset}>
        撤销本栏修改
      </Button>
    </>
  );
}

export function LaunchArgsPanel() {
  const { tokens: t } = useDarkMode();
  const { data: launch, isLoading, refetch } = useLaunch();
  const [saving, setSaving] = useState(false);
  const [localState, setLocalState] = useState<PanelState>({ sel: {}, custom: "" });
  const [modalState, setModalState] = useState<PanelState>({ sel: {}, custom: "" });
  const inited = useRef(false);

  // 首次拿到已保存参数时解析进选择表；之后的 refetch 不再覆盖用户编辑
  useEffect(() => {
    if (!launch || inited.current) return;
    inited.current = true;
    setLocalState(parseTokens(launch.local.extra_args));
    setModalState(parseTokens(launch.modal.extra_args));
  }, [launch]);

  const resetOne = (target: Target) => {
    if (!launch) return;
    const parsed = parseTokens(target === "local" ? launch.local.extra_args : launch.modal.extra_args);
    (target === "local" ? setLocalState : setModalState)(parsed);
  };

  const save = async () => {
    if (!launch) return;
    setSaving(true);
    try {
      const r = await saveLaunch(
        buildTokens(localState),
        buildTokens(modalState),
      );
      if (r.volume_push.ok) {
        message.success("已保存；云端参数已写入数据卷，容器下次冷启动生效");
      } else {
        message.warning(`已保存到配置文件，但下发数据卷失败：${r.volume_push.detail}（可稍后重试保存）`);
      }
      refetch();
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="参数目录整理自 ComfyUI 官方文档（docs.comfy.org → Startup flags）与 v0.37.4 源码 comfy/cli_args.py；默认命令为两端当前生效的启动命令。互斥参数（注意力/缓存/显存模式等组）启用其一将自动关闭同组其他项。"
      />
      {/* 参数表很宽，两栏固定上下堆叠（不随宽度并排/换行翻转） */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12, alignItems: "stretch" }}>
        <Card
          size="small"
          title={
            <Space size={8}>
              本地 ComfyUI
              {launch?.local.detected && <Tag color="green" style={{ marginInlineEnd: 0 }}>已检测 PID {launch.local.pid}</Tag>}
            </Space>
          }
          style={{ flex: "1 1 620px", minWidth: 0 }}
        >
          {launch && <LocalControl />}
          {launch && (
            <TargetPanel target="local" launch={launch} state={localState} onChange={setLocalState} onReset={() => resetOne("local")} />
          )}
        </Card>
        <Card
          size="small"
          title={
            <Space size={8}>
              Modal 云端 ComfyUI
              {launch?.modal.app_state === "deployed" && <Tag color="geekblue" style={{ marginInlineEnd: 0 }}>已部署</Tag>}
            </Space>
          }
          style={{ flex: "1 1 620px", minWidth: 0 }}
        >
          {launch && <ModalControl />}
          {launch && (
            <TargetPanel target="modal" launch={launch} state={modalState} onChange={setModalState} onReset={() => resetOne("modal")} />
          )}
        </Card>
      </div>
      <Space>
        <Button type="primary" loading={saving} disabled={!launch || isLoading} onClick={save}>
          保存全部（本地 + 云端）
        </Button>
        <span style={{ color: t.colors.textSecondary, fontSize: 12 }}>
          保存到 ~/.comfybridge/config.toml；云端参数同时下发数据卷 {launch?.modal.volume_file ?? "/config/launch_args.json"}
        </span>
      </Space>
    </Space>
  );
}
