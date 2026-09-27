import { useState } from "react";
import { Alert, Button, Card, Col, Input, Row, Select, Space, Steps, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { BackendTag, type Backend } from "../../components/common/BackendTag";
import { EmptyGuide } from "../../components/common/EmptyGuide";
import { apiSend } from "../../api/client";
import { useLocalWorkflows } from "../../api/workflows";

// 与后端 routing/engine.py 内置规则链一一对应（M2 计划 DB 化 CRUD，此页先只读展示）
interface BuiltinRule {
  rule: string;
  condition: string;
  target: string;
}

const builtinRules: BuiltinRule[] = [
  { rule: "manual", condition: "提交方显式指定（x-bridge-backend 头或 bridge_backend 元数据）", target: "manual_backend" },
  { rule: "tag", condition: "工作流名 cloud: 前缀", target: "modal" },
  { rule: "cloud_model", condition: "引用云端专有模型（本地无副本）", target: "modal" },
  { rule: "gen_type", condition: "视频类输出节点 → video_backend（默认 modal）", target: "video_backend" },
  { rule: "size", condition: "图最大边 ≥ large_side_pixels（0 = 关闭）", target: "modal" },
  { rule: "default", condition: "以上均未命中 → default_backend 兜底", target: "default_backend" },
];

const ruleColumns: ColumnsType<BuiltinRule> = [
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
  const [workflow, setWorkflow] = useState<string | undefined>();
  const [graphJson, setGraphJson] = useState("");
  const [manual, setManual] = useState<string | undefined>();
  const [running, setRunning] = useState(false);
  const [preview, setPreview] = useState<PreviewResp | null>(null);

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

  return (
    <Row gutter={[12, 12]}>
      <Col span={13}>
        <Card
          size="small"
          title="规则链（代码内置，自上而下求值，首条命中生效）"
          extra={<Tag color="default">规则 CRUD 规划中</Tag>}
        >
          <Table<BuiltinRule> columns={ruleColumns} dataSource={builtinRules} pagination={false} size="small" />
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0, marginTop: 10 }}>
            决策后还会应用可用性回退：目标为云端但 Modal 通道不可用时，按 bridge.on_modal_unavailable（fallback_local / reject）处理，
            回退步骤会以「fallback」出现在干跑链路里。
          </Typography.Paragraph>
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
