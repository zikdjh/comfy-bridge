import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  DeleteOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import SparkMD5 from "spark-md5";
import { useQueryClient } from "@tanstack/react-query";
import {
  completeUpload,
  deleteUpload,
  initUpload,
  putUploadChunk,
  useUploadSessions,
  type UploadSession,
} from "../../api/modalVolume";
import { EmptyGuide } from "../common/EmptyGuide";

const CHUNK_SIZE = 8 * 1024 * 1024;

function md5Chunk(data: ArrayBuffer): string {
  return SparkMD5.ArrayBuffer.hash(data);
}

async function fileMd5(file: File): Promise<string> {
  const spark = new SparkMD5.ArrayBuffer();
  const chunks = Math.ceil(file.size / CHUNK_SIZE);
  for (let index = 0; index < chunks; index += 1) {
    const start = index * CHUNK_SIZE;
    const blob = file.slice(start, Math.min(file.size, start + CHUNK_SIZE));
    spark.append(await blob.arrayBuffer());
  }
  return spark.end();
}

const STATUS: Record<
  UploadSession["status"],
  { color: string; text: string }
> = {
  receiving: { color: "processing", text: "上传中" },
  verifying: { color: "processing", text: "校验中" },
  pushing: { color: "processing", text: "落卷中" },
  done: { color: "success", text: "已完成" },
  error: { color: "error", text: "失败" },
};

function UploadJobTable({
  sessions,
  activeId,
  onPause,
  onResume,
  onRemove,
}: {
  sessions: UploadSession[];
  activeId: string | null;
  onPause: () => void;
  onResume: (session: UploadSession) => void;
  onRemove: (id: string) => void;
}) {
  const columns: ColumnsType<UploadSession> = [
    {
      title: "文件",
      dataIndex: "filename",
      render: (name: string, row) => (
        <Tooltip title={`${row.volume}:/${row.remote_path}`}>
          <Typography.Text code ellipsis style={{ maxWidth: 260 }}>
            {name}
          </Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: "目标",
      dataIndex: "remote_path",
      width: 260,
      render: (_path: string, row) => (
        <Space size={6}>
          <Tag color="blue">{row.volume}</Tag>
          <Typography.Text code ellipsis style={{ maxWidth: 190 }}>
            {row.remote_dir || "（卷根目录）"}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "进度",
      key: "progress",
      width: 190,
      render: (_value, row) => {
        const percent = row.size
          ? Math.min(100, Math.round((row.uploaded_bytes / row.size) * 100))
          : 0;
        return (
          <Space size={8}>
            <Progress
              percent={percent}
              size="small"
              style={{ width: 90 }}
              status={row.status === "error" ? "exception" : undefined}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.uploaded_chunks.length}/{row.total_chunks}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (_value, row) => {
        const status = STATUS[row.status];
        return (
          <Tooltip title={row.error || undefined}>
            <Tag color={status.color}>{status.text}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: "校验",
      key: "md5",
      width: 150,
      render: (_value, row) =>
        row.status === "done" ? (
          <Tag color={row.actual_md5 === row.md5 ? "success" : "warning"}>
            {row.actual_md5 === row.md5 ? "MD5 一致" : "待确认"}
          </Tag>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.md5.slice(0, 8)}…
          </Typography.Text>
        ),
    },
    {
      title: "操作",
      key: "action",
      width: 140,
      render: (_value, row) => (
        <Space size={2}>
          {row.status === "receiving" && (row.missing_count > 0 || row.uploaded_bytes < row.size) && (
            activeId === row.id ? (
              <Button
                size="small"
                type="link"
                icon={<PauseCircleOutlined />}
                onClick={onPause}
              >
                暂停
              </Button>
            ) : (
              <Button
                size="small"
                type="link"
                icon={<PlayCircleOutlined />}
                onClick={() => onResume(row)}
              >
                继续
              </Button>
            )
          )}
          {row.status === "error" && row.error_stage !== "verify" && (
            <Button
              size="small"
              type="link"
              icon={<ReloadOutlined />}
              onClick={() => onResume(row)}
            >
              重试
            </Button>
          )}
          {!(activeId === row.id) && (
            <Button
              size="small"
              type="link"
              danger
              icon={<DeleteOutlined />}
              onClick={() => onRemove(row.id)}
            >
              删除
            </Button>
          )}
        </Space>
      ),
    },
  ];

  if (sessions.length === 0) {
    return <EmptyGuide description="暂无分块上传任务" />;
  }
  return (
    <Table
      columns={columns}
      dataSource={sessions}
      rowKey="id"
      size="small"
      pagination={{ pageSize: 8, hideOnSinglePage: true }}
    />
  );
}

export function ModelUploadCard({ categories }: { categories: string[] }) {
  const queryClient = useQueryClient();
  const { data } = useUploadSessions();
  const sessions = useMemo(() => data?.uploads ?? [], [data]);
  const [category, setCategory] = useState(categories[0] ?? "checkpoints");
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const pausedRef = useRef(false);
  const fileRef = useRef<File | null>(null);

  useEffect(() => {
    if (categories.length && !categories.includes(category)) {
      setCategory(categories[0]);
    }
  }, [categories, category]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["uploads"] });

  const uploadChunks = async (session: UploadSession, selected: File): Promise<boolean> => {
    const received = new Set(session.uploaded_chunks);
    for (let index = 0; index < session.total_chunks; index += 1) {
      if (pausedRef.current) return false;
      if (received.has(index)) continue;
      const start = index * session.chunk_size;
      const blob = selected.slice(
        start,
        Math.min(selected.size, start + session.chunk_size),
      );
      const buffer = await blob.arrayBuffer();
      await putUploadChunk(session.id, index, blob, md5Chunk(buffer));
      received.add(index);
      await refresh();
    }
    await completeUpload(session.id);
    await refresh();
    return true;
  };

  const start = async (selected: File, existing?: UploadSession) => {
    if (!selected.size) {
      message.warning("不能上传空文件");
      return;
    }
    setBusy(true);
    pausedRef.current = false;
    try {
      const session =
        existing ??
        (
          await initUpload({
            filename: selected.name,
            size: selected.size,
            md5: await fileMd5(selected),
            category,
          })
        ).upload;
      setActiveId(session.id);
      const completed = await uploadChunks(session, selected);
      if (completed) {
        message.success(`「${selected.name}」分块已全部上传，服务端正在校验并落卷`);
        setFile(null);
        setFileInputKey((value) => value + 1);
      }
    } catch (error) {
      message.error(`上传失败：${error instanceof Error ? error.message : String(error)}`);
      await refresh();
    } finally {
      setActiveId(null);
      setBusy(false);
    }
  };

  const resume = (session: UploadSession) => {
    const selected = fileRef.current;
    if (!selected || selected.name !== session.filename || selected.size !== session.size) {
      message.warning("请重新选择同一个文件后再继续上传");
      return;
    }
    start(selected, session);
  };

  const remove = async (id: string) => {
    try {
      await deleteUpload(id);
      message.success("上传会话已删除");
      await refresh();
    } catch (error) {
      message.error(`删除失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  return (
    <Card size="small" title="分块上传（断点续传 + MD5 校验）">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Space size={8} wrap>
          <Select
            style={{ width: 200 }}
            value={category}
            onChange={setCategory}
            options={categories.map((value) => ({ value, label: value }))}
          />
          <input
            key={fileInputKey}
            type="file"
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              setFile(selected);
              fileRef.current = selected;
            }}
          />
          <Button
            type="primary"
            icon={<UploadOutlined />}
            disabled={!file || busy || !categories.length}
            loading={busy}
            onClick={() => file && start(file)}
          >
            上传到云端
          </Button>
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          分块大小 8 MiB，上传中断或桥接器重启后可从已完成分块继续；服务端合并后计算
          整文件 MD5，校验一致才会写入数据卷。目标路径为 models/&lt;分类&gt;/&lt;文件名&gt;。
        </Typography.Text>
        {!categories.length && (
          <Alert type="warning" showIcon message="没有可用的模型分类，请先完成本地模型目录配置" />
        )}
        <UploadJobTable
          sessions={sessions}
          activeId={activeId}
          onPause={() => {
            pausedRef.current = true;
            message.info("将在当前分块完成后暂停；再次点击继续即可补齐剩余分块");
          }}
          onResume={resume}
          onRemove={remove}
        />
      </Space>
    </Card>
  );
}
