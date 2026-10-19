import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  cancelComfyScan,
  fetchComfyScan,
  startComfyScan,
  type ComfyFindDto,
  type ComfyScanStateDto,
} from "../../api/config";

const columns: ColumnsType<ComfyFindDto> = [
  {
    title: "路径",
    dataIndex: "path",
    render: (p: string) => <Typography.Text code style={{ fontSize: 12 }}>{p}</Typography.Text>,
  },
  { title: "版本", dataIndex: "version", width: 90, render: (v?: string | null) => v ?? "—" },
  {
    title: "环境",
    dataIndex: "has_venv",
    width: 100,
    render: (has: boolean) =>
      has ? <Tag color="green">内置 venv</Tag> : <Tag>外部 Python</Tag>,
  },
  {
    title: "完整性",
    dataIndex: "complete",
    width: 96,
    render: (complete: boolean, row: ComfyFindDto) =>
      complete ? (
        <Tag color="green">完整</Tag>
      ) : (
        <Tooltip title={`缺少：${row.optional_missing.join("、")}`}>
          <Tag color="orange">缺 {row.optional_missing.length} 项</Tag>
        </Tooltip>
      ),
  },
];

/** 扫描本机 ComfyUI（排除 C 盘）弹窗：启动/轮询/取消 + 结果单选回填根目录 */
export function ComfyScanModal({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (path: string) => void;
}) {
  const [state, setState] = useState<ComfyScanStateDto | null>(null);
  const [selected, setSelected] = useState<string>();
  const [busy, setBusy] = useState(false);
  // action（开始/取消）后自增，触发下方 effect 重新拉取并接上轮询
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const s = await fetchComfyScan();
        if (!alive) return;
        setState(s);
        if (s.status === "running") timer = window.setTimeout(poll, 1500);
      } catch {
        if (alive) timer = window.setTimeout(poll, 3000); // 轮询失败静默重试
      }
    };
    poll();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [open, tick]);

  const run = async (fn: () => Promise<unknown>, reset?: boolean) => {
    setBusy(true);
    if (reset) setSelected(undefined);
    try {
      await fn();
      setTick((t) => t + 1);
    } catch (err) {
      message.error(`操作失败：${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(false);
    }
  };

  const running = state?.status === "running";
  const scope = state?.drives?.length ? state.drives.join("、") : "本机固定/可移动磁盘";

  return (
    <Modal
      title="扫描本机 ComfyUI"
      open={open}
      onCancel={onClose}
      width={720}
      footer={
        <Space>
          {running ? (
            <Button danger disabled={busy} onClick={() => run(cancelComfyScan)}>
              取消扫描
            </Button>
          ) : (
            <Button
              icon={<SearchOutlined />}
              loading={busy}
              onClick={() => run(startComfyScan, true)}
            >
              {state?.status === "done" || state?.status === "error" ? "重新扫描" : "开始扫描"}
            </Button>
          )}
          <Button type="primary" disabled={!selected || running} onClick={() => selected && onPick(selected)}>
            采用所选目录
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          扫描范围：{scope}（已排除 C 盘）；识别特征：目录含 main.py + comfy/ + comfy_extras/。
        </Typography.Text>

        {running && (
          <Alert
            type="info"
            icon={<Spin size="small" />}
            message={`正在扫描 ${state?.current_drive ?? "…"}（已枚举 ${state?.scanned_dirs ?? 0} 个目录）`}
            description="大容量磁盘可能需要一两分钟，可随时取消。"
          />
        )}
        {state?.status === "error" && (
          <Alert type="error" showIcon message="扫描失败" description={state.error} />
        )}
        {state?.status === "done" && state.cancelled && (
          <Alert type="warning" showIcon message="扫描已取消" description="可重新扫描或手动填写路径。" />
        )}
        {state?.status === "done" && !state.cancelled && state.truncated && (
          <Alert
            type="warning"
            showIcon
            message="扫描提前结束"
            description="达到时限/目录上限，结果可能不全；可重新扫描或手动填写路径。"
          />
        )}
        {state?.status === "done" && !state.cancelled && !state.truncated && state.found.length === 0 && (
          <Alert type="info" showIcon message="未找到 ComfyUI 安装" description="可尝试重新扫描，或直接在输入框手动填写路径。" />
        )}

        {(state?.found.length ?? 0) > 0 && (
          <Table<ComfyFindDto>
            size="small"
            columns={columns}
            dataSource={state?.found}
            rowKey="path"
            pagination={false}
            scroll={{ x: true }}
            rowSelection={{
              type: "radio",
              selectedRowKeys: selected ? [selected] : [],
              onChange: (keys) => setSelected(keys[0] as string),
            }}
            onRow={(row) => ({ onClick: () => setSelected(row.path) })}
          />
        )}
      </Space>
    </Modal>
  );
}
