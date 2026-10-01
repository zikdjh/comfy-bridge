import { useEffect, useState } from "react";
import {
  Alert, Button, Card, Col, Divider, Input, InputNumber, Row, Select, Space, Steps, Switch, Table, Tag,
  Typography, message,
} from "antd";
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined, SaveOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { BackendTag, type Backend } from "../../components/common/BackendTag";
import { EmptyGuide } from "../../components/common/EmptyGuide";
import { apiSend } from "../../api/client";
import { useLocalWorkflows } from "../../api/workflows";
import { useRoutingRules, useSaveRoutingRules, type RuleRow, type RuleKindInfo } from "../../api/routing";

// 固定规则（不参与 CRUD，决策链两端的安全与兜底步骤）
interface BuiltinRule {
  rule: string;
  condition: string;
  target: string;
}

const fixedRules: BuiltinRule[] = [
  { rule: "manual", condition: "提交方显式指定（x-bridge-backend 头或 bridge_backend 元数据）", target: "manual_backend" },
  { rule: "tag", condition: "工作流名 cloud: 前缀", target: "modal" },
  { rule: "cloud_model", condition: "引用云端专有模型（本地无副本）", target: "modal" },
];

// rules 为空时仍然生效的内置规则（历史行为；保存自定义链后被替换）
const legacyRules: BuiltinRule[] = [
  { rule: "gen_type", condition: "视频类输出节点 → video_backend（设置 → 护栏与预算）", target: "video_backend" },
  { rule: "size", condition: "图最大边 ≥ large_side_pixels（0 = 关闭）", target: "modal" },
];

const fixedColumns: ColumnsType<BuiltinRule> = [
  { title: "#", key: "i", width: 44, render: (_v, _r, i) => i + 1 },
  { title: "规则", dataIndex: "rule", width: 110, render: (r: string) => <Tag>{r}</Tag> },
  { title: "条件", dataIndex: "condition" },
  {
    title: "目标",
    dataIndex: "target",
    width: 130,
    render: (t: string) => (t === "local" || t === "modal" ? <BackendTag backend={t as Backend} /> : <Tag>{t}</Tag>),
  },
];

interface TraceStep {
  rule: string;
  matched: boolean;
  detail: string;
  target: string | null;
}

interface PreviewResp {
  facts: {
    gen_type: string;
    video_nodes: string[];
    image_nodes: string[];
    max_width: number | null;
    max_height: number | null;
    class_types: string[];
    model_refs: string[];
  };
  backend: string;
  final_backend: string;
  fallback: string | null;
  trace: TraceStep[];
}

export default function RoutingPage() {
  const { data: wf } = useLocalWorkflows();
  const { data: rulesResp } = useRoutingRules();
  const saveRules = useSaveRoutingRules();

  const [rows, setRows] = useState<RuleRow[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [workflow, setWorkflow] = useState<string | undefined>();
  const [graphJson, setGraphJson] = useState("");
  const [manual, setManual] = useState<string | undefined>();
  const [running, setRunning] = useState(false);
  const [preview, setPreview] = useState<PreviewResp | null>(null);

  // 服务端数据到达/刷新后同步本地编辑副本（params 浅拷贝，避免直接改缓存）
  useEffect(() => {
    if (rulesResp) setRows(rulesResp.rules.map((r) => ({ ...r, params: { ...r.params } })));
  }, [rulesResp]);

  const kinds: Record<string, RuleKindInfo> = rulesResp?.kinds ?? {};
  const suggest = rulesResp?.suggest ?? {};
  const customRules = rows ?? [];

  const updateRow = (idx: number, patch: Partial<RuleRow>) =>
    setRows((rs) => (rs ?? []).map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  const updateParam = (idx: number, key: string, val: unknown) =>
    setRows((rs) => (rs ?? []).map((r, i) => (i === idx ? { ...r, params: { ...r.params, [key]: val } } : r)));

  const addRow = () =>
    setRows((rs) => [
      ...(rs ?? []),
      { id: "", name: "", kind: "workflow_tag", params: { prefix: "" }, target: "modal", enabled: true },
    ]);

  const seedFromSettings = () =>
    setRows((rs) => [
      ...(rs ?? []),
      ...Object.values(suggest).map((s) => ({
        id: "", name: s.name, kind: s.kind, params: { ...s.params }, target: s.target, enabled: true,
      })),
    ]);

  const move = (idx: number, delta: -1 | 1) =>
    setRows((rs) => {
      const next = [...(rs ?? [])];
      const j = idx + delta;
      if (j < 0 || j >= next.length) return next;
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });

  const save = async () => {
    setSaving(true);
    try {
      await saveRules(customRules);
      message.success("规则链已保存并热生效（空列表 = 沿用内置视频/大分辨率规则）");
    } catch (err) {
      message.error(`保存失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setSaving(false);
    }
  };

  const dryRun = async () => {
    let prompt: unknown;
    if (graphJson.trim()) {
      try {
        prompt = JSON.parse(graphJson);
      } catch (err) {
        message.error(`工作流 JSON 解析失败：${err instanceof Error ? err.message : err}`);
        return;
      }
      if (!prompt || typeof prompt !== "object" || Array.isArray(prompt)) {
        message.error("工作流 JSON 需要是 API 格式的图对象（键为节点 id）");
        return;
      }
    }
    if (!workflow && !prompt) {
      message.warning("请先选择工作流或粘贴 API 格式工作流 JSON");
      return;
    }
    setRunning(true);
    try {
      const body: Record<string, unknown> = {};
      if (workflow) body.workflow_name = workflow;
      if (prompt) body.prompt = prompt;
      if (manual) body.manual_backend = manual;
      const res = await apiSend<PreviewResp>("/api/v1/routing/preview", "POST", body);
      setPreview(res);
    } catch (err) {
      message.error(`干跑失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setRunning(false);
    }
  };

  const matchedIdx = preview?.trace.findIndex((t) => t.matched) ?? -1;
  const finalBackend = preview?.final_backend ?? "";
  const rejected = preview != null && !finalBackend;

  const paramCell = (idx: number, row: RuleRow) => {
    const info = kinds[row.kind];
    if (!info) return <Tag color="warning">未知类型</Tag>;
    if (row.kind === "gen_type") {
      return (
        <Select
          size="small"
          style={{ width: "100%" }}
          value={String(row.params.gen_type ?? "video")}
          onChange={(v) => updateParam(idx, "gen_type", v)}
          options={[
            { value: "video", label: "视频类" },
            { value: "image", label: "图片类" },
          ]}
        />
      );
    }
    if (row.kind === "min_pixels") {
      return (
        <InputNumber
          size="small"
          style={{ width: "100%" }}
          min={1}
          precision={0}
          placeholder="像素阈值"
          value={Number(row.params.pixels ?? undefined) || undefined}
          onChange={(v) => updateParam(idx, "pixels", v ?? 0)}
        />
      );
    }
    return (
      <Input
        size="small"
        placeholder={info.param_label}
        value={String(row.params[info.param_key] ?? "")}
        onChange={(e) => updateParam(idx, info.param_key, e.target.value)}
      />
    );
  };

  const ruleColumns: ColumnsType<RuleRow> = [
    {
      title: "启用",
      key: "enabled",
      width: 62,
      render: (_v, r, i) => <Switch size="small" checked={r.enabled} onChange={(v) => updateRow(i, { enabled: v })} />,
    },
    {
      title: "名称",
      key: "name",
      width: 130,
      render: (_v, r, i) => (
        <Input
          size="small"
          placeholder="如：视频上云"
          value={r.name}
          onChange={(e) => updateRow(i, { name: e.target.value })}
        />
      ),
    },
    {
      title: "类型",
      key: "kind",
      width: 130,
      render: (_v, r, i) => (
        <Select
          size="small"
          style={{ width: "100%" }}
          value={r.kind in kinds ? r.kind : undefined}
          placeholder="类型"
          onChange={(v) => {
            const info = kinds[v];
            updateRow(i, { kind: v, params: info ? { [info.param_key]: v === "min_pixels" ? 1024 : "" } : {} });
          }}
          options={Object.entries(kinds).map(([k, info]) => ({ value: k, label: info.label }))}
        />
      ),
    },
    { title: "条件参数", key: "params", render: (_v, r, i) => paramCell(i, r) },
    {
      title: "目标",
      key: "target",
      width: 110,
      render: (_v, r, i) => (
        <Select
          size="small"
          style={{ width: "100%" }}
          value={r.target || undefined}
          placeholder="目标"
          onChange={(v) => updateRow(i, { target: v })}
          options={[
            { value: "local", label: "本地" },
            { value: "modal", label: "云端" },
          ]}
        />
      ),
    },
    {
      title: "操作",
      key: "op",
      width: 108,
      render: (_v, r, i) => (
        <Space size={0}>
          <Button type="text" size="small" icon={<ArrowUpOutlined />} disabled={i === 0} onClick={() => move(i, -1)} />
          <Button
            type="text"
            size="small"
            icon={<ArrowDownOutlined />}
            disabled={i === customRules.length - 1}
            onClick={() => move(i, 1)}
          />
          <Button
            type="text"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => setRows((rs) => (rs ?? []).filter((_, j) => j !== i))}
          />
        </Space>
      ),
    },
  ];

  return (
    <Row gutter={[12, 12]}>
      <Col span={13}>
        <Card size="small" title="路由规则链（自上而下求值，首条命中生效）">
          <Space direction="vertical" size={10} style={{ width: "100%" }}>
            <Table<BuiltinRule> columns={fixedColumns} dataSource={fixedRules} pagination={false} size="small" />
            <Divider plain style={{ margin: "2px 0", fontSize: 12 }}>
              自定义规则{customRules.length ? `（${customRules.length} 条）` : "（未配置）"}
            </Divider>
            {customRules.length ? (
              <Table<RuleRow> columns={ruleColumns} dataSource={customRules} pagination={false} size="small" rowKey={(_r, i) => String(i)} />
            ) : (
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
                未配置自定义规则：内置「视频 → video_backend」「大分辨率 → modal」两条继续生效（历史行为）。
                保存任意自定义链后，这两条内置规则将被替换。
              </Typography.Paragraph>
            )}
            <Space>
              <Button icon={<PlusOutlined />} onClick={addRow}>添加规则</Button>
              {customRules.length === 0 && Object.keys(suggest).length > 0 && (
                <Button onClick={seedFromSettings}>按当前内置设置生成</Button>
              )}
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>
                保存规则链
              </Button>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              规则链保存在 ~/.comfybridge/config.toml 的 [bridge].rules（热生效）。决策后还会应用可用性回退：
              目标为云端但 Modal 通道不可用时，按 bridge.on_modal_unavailable（fallback_local / reject）处理，
              回退步骤会以「fallback」出现在干跑链路里。
            </Typography.Text>
          </Space>
        </Card>
      </Col>
      <Col span={11}>
        <Card size="small" title="干跑预览器">
          <Space direction="vertical" size={10} style={{ width: "100%" }}>
            <Select
              style={{ width: "100%" }}
              showSearch
              optionFilterProp="label"
              placeholder="选择本地工作流"
              value={workflow}
              onChange={setWorkflow}
              allowClear
              options={(wf?.workflows ?? []).map((w) => ({ value: w.name, label: w.name }))}
              notFoundContent={wf?.dir_exists ? "本地工作流目录为空" : "本地工作流目录不可用"}
            />
            <Input.TextArea
              rows={3}
              placeholder="或粘贴 API 格式工作流 JSON（键为节点 id）；粘贴后以 JSON 为准"
              value={graphJson}
              onChange={(e) => setGraphJson(e.target.value)}
            />
            <Select
              style={{ width: "100%" }}
              placeholder="模拟手动指定后端（可选）"
              value={manual}
              onChange={setManual}
              allowClear
              options={[
                { value: "local", label: "local（强制本地）" },
                { value: "modal", label: "modal（强制云端）" },
              ]}
            />
            <Button type="primary" block loading={running} onClick={dryRun}>
              干跑
            </Button>
            {preview ? (
              <>
                <Steps
                  size="small"
                  direction="vertical"
                  current={matchedIdx === -1 ? preview.trace.length - 1 : matchedIdx}
                  items={preview.trace.map((t) => ({
                    title: t.rule,
                    description: t.detail,
                    status: t.matched ? "finish" : "wait",
                  }))}
                />
                {rejected ? (
                  <Alert type="error" showIcon message="最终决策：拒绝执行" description={preview.fallback ?? undefined} />
                ) : (
                  <Alert
                    type={finalBackend === "modal" ? "info" : "success"}
                    showIcon
                    message={
                      preview.fallback
                        ? `最终决策：${finalBackend === "modal" ? "云端" : "本地"}（已回退）`
                        : `最终决策：${finalBackend === "modal" ? "云端" : "本地"}`
                    }
                    description={preview.fallback ?? undefined}
                  />
                )}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  事实：类型 {preview.facts.gen_type} · 最大边 {preview.facts.max_width ?? "?"}×{preview.facts.max_height ?? "?"} ·
                  模型引用 {preview.facts.model_refs.length ? preview.facts.model_refs.join("、") : "无"}
                  （干跑不评估模型在本地/云端的实际归属，实际提交时由桥接器按索引判定）
                </Typography.Text>
              </>
            ) : (
              <EmptyGuide description="选择工作流（或粘贴 JSON）后点击「干跑」，查看逐条规则的求值过程" />
            )}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              调用 POST /api/v1/routing/preview（不执行，仅返回分类事实与决策链路）
            </Typography.Text>
          </Space>
        </Card>
      </Col>
    </Row>
  );
}
