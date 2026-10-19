import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import {
  Alert,
  AutoComplete,
  Breadcrumb,
  Button,
  Card,
  Input,
  Modal,
  Popconfirm,
  Popover,
  Progress,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  CloudDownloadOutlined,
  DeleteOutlined,
  FileOutlined,
  FolderAddOutlined,
  FolderOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useQueryClient } from "@tanstack/react-query";
import {
  cancelVolumeDownload,
  cancelVolumeMove,
  createVolume,
  createVolumeDownload,
  createVolumeMove,
  deleteVolume,
  deleteVolumePaths,
  forceVolumeUsageRefresh,
  mkdirVolumeFolder,
  previewVolumePaths,
  purgeVolumeTrash,
  restoreVolumeTrash,
  useDownloadJobs,
  useMoveJobs,
  useVolumeDirs,
  useVolumeList,
  useVolumeMeta,
  useVolumeUsage,
  useTrash,
} from "../../api/volume";
import type {
  DownloadJob,
  MoveJob,
  ReferenceCheck,
  TrashEntry,
  VolumeEntry,
  VolumeUsageItem,
} from "../../api/volume";
import { EmptyGuide } from "../../components/common/EmptyGuide";
import { bridgeTokens as t } from "../../theme";

/* 常用落卷目录（与 runner.py 的卷目录结构对应） */
const QUICK_DIRS = [
  "models/checkpoints",
  "models/loras",
  "models/vae",
  "models/text_encoders",
  "models/diffusion_models",
  "models/upscale_models",
  "models/controlnet",
  "models/embeddings",
  "custom_nodes",
  "input",
  "output",
];

/** 免费额度阈值（GiB）：≥512 标黄，≥1024 标红并提示超额 */
const WARN_GB = 512;
const OVER_GB = 1024;
const GiB = 1024 ** 3;

function humanBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

const fmtDuration = (ms: number) => {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
};

/* ---------------- 总用量状态卡 ---------------- */

const STATUS_TAG: Record<string, { color: string; text: string }> = {
  ok: { color: "green", text: "正常" },
  warn: { color: "gold", text: "接近免费额度" },
  over: { color: "red", text: "超出免费额度" },
};

function UsageCard() {
  const queryClient = useQueryClient();
  const { data: meta } = useVolumeMeta();
  const { data, isFetching } = useVolumeUsage();
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<VolumeUsageItem | null>(null);
  const [deleteConfirmName, setDeleteConfirmName] = useState("");
  const [deleting, setDeleting] = useState(false);

  const refresh = async () => {
    try {
      await forceVolumeUsageRefresh(); // 绕过缓存触发后台重算
    } catch {
      // 失败时仍然刷新一次普通查询
    }
    queryClient.invalidateQueries({ queryKey: ["volume", "usage"] });
  };

  const submitCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      await createVolume(name);
      message.success(`数据卷 ${name} 已创建`);
      setCreateOpen(false);
      setNewName("");
      refresh();
    } catch (exc) {
      message.error(`创建失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setCreating(false);
    }
  };

  const submitDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteVolume(deleteTarget.name);
      message.success(`数据卷 ${deleteTarget.name} 已删除`);
      setDeleteTarget(null);
      setDeleteConfirmName("");
      refresh();
    } catch (exc) {
      message.error(`删除失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setDeleting(false);
    }
  };

  const total = data?.total_bytes ?? 0;
  const status = data?.status ?? "ok";
  const tag = STATUS_TAG[status];
  const percent = Math.min(100, (total / (OVER_GB * GiB)) * 100);

  const volumeColumns: ColumnsType<VolumeUsageItem> = [
    {
      title: "卷名",
      dataIndex: "name",
      render: (v: string) => (
        <Space size={6}>
          <Typography.Text code>{v}</Typography.Text>
          {v === meta?.volume && <Tag color="blue">主卷</Tag>}
        </Space>
      ),
    },
    { title: "创建时间", dataIndex: "created_at", width: 220 },
    { title: "用量", dataIndex: "total_bytes", width: 120, render: (v: number) => humanBytes(v) },
    { title: "文件数", dataIndex: "file_count", width: 100 },
    {
      title: "操作",
      key: "op",
      width: 100,
      render: (_v, r) =>
        r.name === meta?.volume ? (
          <Typography.Text type="secondary">主卷不可删除</Typography.Text>
        ) : (
          <Button size="small" type="link" danger onClick={() => setDeleteTarget(r)}>
            删除
          </Button>
        ),
    },
  ];

  return (
    <Card
      size="small"
      title="数据卷总览 —— 当前 Modal 凭证下所有卷"
      extra={
        <Space>
          <Button size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新增数据卷
          </Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => refresh()} loading={isFetching}>
            重新统计
          </Button>
        </Space>
      }
    >
      {data?.error && <Alert type="error" showIcon message="用量统计失败" description={data.error} style={{ marginBottom: 12 }} />}
      {status === "warn" && (
        <Alert
          type="warning"
          showIcon
          message={`总用量 ${humanBytes(total)} 已达 ${WARN_GB} GB 免费额度阈值`}
          description="继续增长可能产生存储费用，建议清理不用的卷与大文件。"
          style={{ marginBottom: 12 }}
        />
      )}
      {status === "over" && (
        <Alert
          type="error"
          showIcon
          message={`总用量 ${humanBytes(total)} 已超出免费额度（${OVER_GB} GB）`}
          description="当前将按 Modal 存储计费，请尽快清理不用的数据卷或大文件。"
          style={{ marginBottom: 12 }}
        />
      )}
      <Space size={40} wrap align="center" style={{ width: "100%" }}>
        <Statistic title="所有卷总用量" value={humanBytes(total)} />
        <Statistic title="文件总数" value={data?.file_count ?? 0} />
        <Statistic title="卷数量" value={data?.volumes.length ?? 0} />
        <div style={{ minWidth: 280 }}>
          <Space size={8} style={{ marginBottom: 4 }}>
            <span style={{ fontSize: 12, color: t.colors.textMuted }}>额度水位（{OVER_GB} GB）</span>
            <Tag color={tag.color}>{tag.text}</Tag>
          </Space>
          <Progress
            percent={percent}
            showInfo={false}
            strokeColor={status === "over" ? t.colors.danger : status === "warn" ? t.colors.warningDeep : undefined}
            size={["100%", 8]}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {data?.error ? (
              <Alert
                type="warning"
                showIcon
                message="数据卷用量暂未更新"
                description={data.error}
              />
            ) : data?.computing ? (
              <Space size={6}>
                <Spin size="small" />
                正在遍历所有卷统计用量…
              </Space>
            ) : (
              `阈值：${WARN_GB} GB 标黄 / ${OVER_GB} GB 标红${data?.computed_ms ? ` · 上次统计 ${new Date(data.computed_ms).toLocaleTimeString()}` : ""}`
            )}
          </Typography.Text>
        </div>
      </Space>
      {data && data.volumes.length > 0 && (
        <Table
          style={{ marginTop: 16 }}
          columns={volumeColumns}
          dataSource={data.volumes}
          rowKey="name"
          size="small"
          pagination={false}
        />
      )}

      <Modal
        title="新增数据卷"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={submitCreate}
        confirmLoading={creating}
        okText="创建"
      >
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <Input
            placeholder="卷名（如 my-extra-data）"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onPressEnter={submitCreate}
          />
          <Typography.Text type="secondary">
            3~32 位小写字母/数字/连字符，字母开头。空卷不占存储费用，写入数据后按用量计费。
            新卷不会自动挂载到云端 ComfyUI（runner 只挂载主卷）。
          </Typography.Text>
        </Space>
      </Modal>

      <Modal
        title="删除数据卷"
        open={!!deleteTarget}
        onCancel={() => {
          setDeleteTarget(null);
          setDeleteConfirmName("");
        }}
        onOk={submitDelete}
        okButtonProps={{
          danger: true,
          disabled: deleteConfirmName !== deleteTarget?.name || (deleteTarget?.file_count ?? 0) > 0,
        }}
        confirmLoading={deleting}
        okText="确认删除"
      >
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          {deleteTarget && (
            <Alert
              type="error"
              showIcon
              message={`将删除数据卷 ${deleteTarget.name}`}
              description={
                (deleteTarget.file_count ?? 0) > 0
                  ? `该卷包含 ${deleteTarget.file_count} 个文件（${humanBytes(deleteTarget.total_bytes)}），不允许删除——请先在目录浏览中清空卷内文件，再回到此处删除空卷。`
                  : "该卷当前为空，确认后将从 Modal 平台删除，不可恢复。"
              }
            />
          )}
          <Input
            placeholder={`输入卷名 ${deleteTarget?.name ?? ""} 以确认`}
            value={deleteConfirmName}
            onChange={(e) => setDeleteConfirmName(e.target.value)}
          />
        </Space>
      </Modal>
    </Card>
  );
}

/* ---------------- 云端直下（任意数据卷） ---------------- */

function DownloadCard({
  currentPath,
  browserVolume,
  volumeNames,
  defaultVolume,
}: {
  currentPath: string;
  browserVolume: string;
  volumeNames: string[];
  defaultVolume: string;
}) {
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [volume, setVolume] = useState(defaultVolume);
  // 主卷名后到时跟随（DownloadCard 的初始值同样早于 meta）
  useEffect(() => {
    if (defaultVolume) setVolume(defaultVolume);
  }, [defaultVolume]);
  const [dest, setDest] = useState("models/checkpoints");
  const [filename, setFilename] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const dirsQuery = useVolumeDirs(volume, true);

  // 目录候选 = 所选数据卷内已有目录（实时清单）+（主卷时）常用结构目录 + 当前浏览目录
  const dirOptions = useMemo(() => {
    const all = [
      ...(dirsQuery.data?.dirs ?? []),
      ...(volume === defaultVolume ? QUICK_DIRS : []),
      ...(currentPath && browserVolume === volume ? [currentPath] : []),
    ];
    return [...new Set(all)].map((v) => ({ value: v }));
  }, [dirsQuery.data, volume, defaultVolume, currentPath, browserVolume]);

  const volumeOptions = useMemo(
    () =>
      [...new Set([defaultVolume, ...volumeNames])].map((v) => ({
        value: v,
        label: (
          <Space size={6}>
            {v}
            {v === defaultVolume && <Tag color="blue">主卷</Tag>}
          </Space>
        ),
      })),
    [volumeNames, defaultVolume],
  );

  const submit = async () => {
    if (!url.trim()) {
      message.warning("请输入下载链接");
      return;
    }
    if (!dest.trim()) {
      message.warning("请选择目标目录");
      return;
    }
    setSubmitting(true);
    try {
      await createVolumeDownload(url.trim(), dest.trim(), volume, filename.trim() || undefined);
      message.success(`已创建下载任务（云端容器后台写入 ${volume}，可在下方查看进度）`);
      setUrl("");
      setFilename("");
      queryClient.invalidateQueries({ queryKey: ["volume", "downloads"] });
    } catch (exc) {
      message.error(`创建失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card size="small" title="云端直下 —— 输入文件下载链接，选择数据卷与目录，云端容器直接落卷">
      <Space size={8} wrap style={{ width: "100%" }}>
        <Input
          style={{ width: 340 }}
          placeholder="下载链接（http/https 直链或 HuggingFace 仓库）"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onPressEnter={submit}
        />
        <Select
          style={{ minWidth: 180 }}
          placeholder="目标数据卷"
          value={volume}
          onChange={setVolume}
          options={volumeOptions}
        />
        <AutoComplete
          style={{ width: 260 }}
          placeholder="目标目录（所选卷内选择或输入）"
          value={dest}
          onChange={setDest}
          options={dirOptions}
          notFoundContent={dirsQuery.isLoading ? <Spin size="small" /> : "输入新目录，云端自动创建"}
        />
        <Input
          style={{ width: 160 }}
          placeholder="保存文件名（可选）"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
          onPressEnter={submit}
        />
        <Button type="primary" icon={<CloudDownloadOutlined />} loading={submitting} onClick={submit}>
          下载到数据卷
        </Button>
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 10 }}>
        在云端 CPU 容器下载后写入所选数据卷并提交，不占用本地带宽；大文件（模型权重）耗时较长，可随时取消。
        目标目录候选实时读取所选卷内的目录清单（主卷另附常用结构目录），也可直接输入新目录由云端自动创建。
        直链下载用「保存文件名」指定落卷名；HuggingFace 仓库名则拉取整个仓库（可配合文件名通配符）。
        非主卷经临时应用挂载写入；云端 ComfyUI（runner）只读主卷，其他卷仅作数据存储。
      </Typography.Paragraph>
    </Card>
  );
}

/* ---------------- 目录浏览（任意卷） ---------------- */

function VolumeBrowser({
  volume,
  setVolume,
  path,
  setPath,
  volumeNames,
}: {
  volume: string;
  setVolume: (v: string) => void;
  path: string;
  setPath: (p: string) => void;
  volumeNames: string[];
}) {
  const { data: meta } = useVolumeMeta();
  const { data, isLoading, refetch, isFetching } = useVolumeList(path, volume);
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState(false);
  const [deletePreview, setDeletePreview] = useState<{
    name: string;
    path: string;
    reference?: ReferenceCheck;
  } | null>(null);
  const entries = data?.entries ?? [];
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);

  // 新建文件夹
  const [mkdirOpen, setMkdirOpen] = useState(false);
  const [mkdirName, setMkdirName] = useState("");
  const [mkdirBusy, setMkdirBusy] = useState(false);
  // 移动到
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveDest, setMoveDest] = useState("");
  const [moveBusy, setMoveBusy] = useState(false);
  const dirsQuery = useVolumeDirs(volume, moveOpen);

  useEffect(() => setSelectedKeys([]), [volume, path]);

  const crumbs = useMemo(() => path.split("/").filter(Boolean), [path]);

  const fullPaths = selectedKeys
    .map((k) => entries.find((e) => e.name === k))
    .filter((e): e is VolumeEntry => !!e)
    .map((e) => (path ? `${path}/${e.name}` : e.name));

  const submitMkdir = async () => {
    const name = mkdirName.trim();
    if (!name) {
      message.warning("请输入文件夹名称");
      return;
    }
    if (name.includes("/") || name.includes("\\") || name.startsWith(".")) {
      message.warning("文件夹名不能包含 / 或 \\ ，也不能以 . 开头");
      return;
    }
    setMkdirBusy(true);
    try {
      await mkdirVolumeFolder(path ? `${path}/${name}` : name, volume);
      message.success(`文件夹 ${name} 已创建（云端清单刷新可能有约 1 分钟延迟）`);
      setMkdirOpen(false);
      setMkdirName("");
      queryClient.invalidateQueries({ queryKey: ["volume", "list"] });
    } catch (exc) {
      message.error(`创建失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setMkdirBusy(false);
    }
  };

  const submitMove = async () => {
    if (!fullPaths.length) return;
    setMoveBusy(true);
    try {
      const res = await createVolumeMove(fullPaths, moveDest, volume);
      message.success(
        `已创建移动任务（${res.job.entries.length} 个条目 → ${res.job.dest_dir || "卷根目录"}，云端服务端搬移中）`,
      );
      setMoveOpen(false);
      setSelectedKeys([]);
      queryClient.invalidateQueries({ queryKey: ["volume", "moves"] });
      queryClient.invalidateQueries({ queryKey: ["volume", "usage"] });
    } catch (exc) {
      message.error(`创建失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setMoveBusy(false);
    }
  };

  // 目标目录候选：全卷目录 - 选中目录及其子目录 - 当前目录（移进原位置无意义）
  const destOptions = useMemo(() => {
    const selectedDirs = entries
      .filter((e) => e.type === "dir" && selectedKeys.includes(e.name))
      .map((e) => (path ? `${path}/${e.name}` : e.name));
    const blocked = [...selectedDirs, path];
    const dirs = (dirsQuery.data?.dirs ?? []).filter(
      (d) => !blocked.some((b) => b && (d === b || d.startsWith(`${b}/`))),
    );
    return [{ value: "", label: "（卷根目录）" }, ...dirs.map((d) => ({ value: d }))];
  }, [entries, selectedKeys, path, dirsQuery.data]);

  const remove = async (name: string, allowReferenced = false) => {
    setDeleting(true);
    try {
      const res = await deleteVolumePaths(
        [path ? `${path}/${name}` : name],
        volume,
        allowReferenced,
      );
      queryClient.invalidateQueries({ queryKey: ["volume", "list"] });
      queryClient.invalidateQueries({ queryKey: ["volume", "usage"] });
      queryClient.invalidateQueries({ queryKey: ["volume", "trash", volume] });
      if (res.ok) {
        message.success(`已删除 ${name}（已移入回收站）`);
        setDeletePreview(null);
      } else {
        const failed = res.results.find((r) => !r.ok);
        if (failed?.reference?.referenced) {
          setDeletePreview({
            name,
            path: failed.path,
            reference: failed.reference,
          });
        } else {
          message.error(failed?.error ?? "删除失败");
        }
      }
    } catch (exc) {
      message.error(`删除失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setDeleting(false);
    }
  };

  const openDelete = async (entry: VolumeEntry) => {
    const rel = path ? `${path}/${entry.name}` : entry.name;
    if (entry.type === "dir" || !rel.startsWith("models/")) {
      setDeletePreview({ name: entry.name, path: rel });
      return;
    }
    setDeleting(true);
    try {
      const res = await previewVolumePaths([rel], volume);
      const row = res.results[0];
      if (!row?.ok) {
        message.error(row?.error ?? "删除预检失败");
        return;
      }
      setDeletePreview({ name: entry.name, path: rel, reference: row.reference });
    } catch (exc) {
      message.error(`删除预检失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setDeleting(false);
    }
  };

  const columns: ColumnsType<VolumeEntry> = [
    {
      title: "名称",
      dataIndex: "name",
      render: (v: string, r) =>
        r.type === "dir" ? (
          <Button
            type="link"
            size="small"
            icon={<FolderOutlined />}
            style={{ padding: 0 }}
            onClick={() => setPath(path ? `${path}/${v}` : v)}
          >
            {v}
          </Button>
        ) : (
          <Space size={6}>
            <FileOutlined />
            <Typography.Text code>{v}</Typography.Text>
          </Space>
        ),
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 90,
      render: (v: string) => (v === "dir" ? <Tag>目录</Tag> : <Tag>文件</Tag>),
    },
    { title: "大小", dataIndex: "size", width: 110 },
    { title: "修改时间", dataIndex: "mtime", width: 200 },
    {
      title: "操作",
      key: "op",
      width: 100,
      render: (_v, r) => (
        <Button size="small" type="link" danger disabled={deleting} onClick={() => openDelete(r)}>
          删除
        </Button>
      ),
    },
  ];

  return (
    <Card
      size="small"
      title="目录浏览"
      extra={
        <Space>
          <Select
            size="small"
            style={{ minWidth: 170 }}
            value={volume}
            onChange={(v) => {
              setVolume(v);
              setPath("");
            }}
            options={[...new Set([meta?.volume ?? "comfyui-data", ...volumeNames])].map((v) => ({
              value: v,
              label: (
                <Space size={6}>
                  {v}
                  {v === meta?.volume && <Tag color="blue">主卷</Tag>}
                </Space>
              ),
            }))}
          />
          <Button size="small" icon={<FolderAddOutlined />} onClick={() => setMkdirOpen(true)}>
            新建文件夹
          </Button>
          <Button
            size="small"
            icon={<SendOutlined />}
            disabled={selectedKeys.length === 0}
            onClick={() => {
              setMoveDest("");
              setMoveOpen(true);
            }}
          >
            移动到{selectedKeys.length ? `（${selectedKeys.length}）` : ""}
          </Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
            刷新
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Breadcrumb
          items={[
            { title: <a onClick={() => setPath("")}>{volume}</a> },
            ...crumbs.map((seg, i) => ({
              title: <a onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>{seg}</a>,
            })),
          ]}
        />
        {meta && !meta.credentials_ok && (
          <Alert type="warning" showIcon message="未配置 Modal 凭证（设置 → Modal），无法浏览云端数据卷" />
        )}
        {isLoading && !data ? (
          <Spin style={{ display: "block", margin: "32px auto" }} />
        ) : data?.error ? (
          <Alert type="error" showIcon message="数据卷读取失败" description={data.error} />
        ) : (data?.entries.length ?? 0) === 0 ? (
          <EmptyGuide description="该目录为空（新目录需要几分钟后才会出现在云端清单中）" />
        ) : (
          <Table
            columns={columns}
            dataSource={entries}
            rowKey={(e) => e.name}
            rowSelection={{ selectedRowKeys: selectedKeys, onChange: setSelectedKeys }}
            size="small"
            pagination={false}
          />
        )}
      </Space>

      <Modal
        title={`删除 ${deletePreview?.name ?? ""}`}
        open={!!deletePreview}
        onCancel={() => setDeletePreview(null)}
        onOk={() =>
          deletePreview &&
          remove(deletePreview.name, Boolean(deletePreview.reference?.referenced))
        }
        confirmLoading={deleting}
        okText={deletePreview?.reference?.referenced ? "仍要软删除" : "移入回收站"}
        okButtonProps={{ danger: true }}
      >
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          {deletePreview?.reference?.referenced ? (
            <>
              <Alert
                type="warning"
                showIcon
                message="该模型仍被引用"
                description={deletePreview.reference.warning}
              />
              <Table
                size="small"
                pagination={false}
                rowKey={(r) => `${r.source}-${r.ref}-${r.detail}`}
                dataSource={deletePreview.reference.refs}
                columns={[
                  {
                    title: "来源",
                    dataIndex: "source",
                    width: 90,
                    render: (v: string) =>
                      v === "request" ? "请求日志" : v === "graph" ? "图快照" : "工作流",
                  },
                  { title: "引用", dataIndex: "ref" },
                  { title: "位置", dataIndex: "detail", width: 180 },
                ]}
              />
            </>
          ) : deletePreview?.path.startsWith("models/") ? (
            <Alert
              type="info"
              showIcon
              message="未发现近期引用"
              description="该模型文件将移入 .trash，默认保留 7 天，可在下方回收站恢复。"
            />
          ) : (
            <Alert
              type="info"
              showIcon
              message="移入回收站"
              description="该条目将移入 .trash，默认保留 7 天，可在下方回收站恢复；目录含文件（递归）时会被后端拒绝，只有空目录才会删除。"
            />
          )}
          <Typography.Text code>{deletePreview?.path}</Typography.Text>
        </Space>
      </Modal>

      <Modal
        title="新建文件夹"
        open={mkdirOpen}
        onCancel={() => setMkdirOpen(false)}
        onOk={submitMkdir}
        confirmLoading={mkdirBusy}
        okText="创建"
      >
        <Space direction="vertical" style={{ width: "100%" }} size={8}>
          <Input
            placeholder="文件夹名称（支持中文）"
            value={mkdirName}
            onChange={(e) => setMkdirName(e.target.value)}
            onPressEnter={submitMkdir}
          />
          <Typography.Text type="secondary">
            在数据卷 {volume} 的 {path || "根目录"} 下创建。名称不能包含 / 或 \ ，不能以 . 开头。
            创建后云端清单刷新可能有约 1 分钟延迟。
          </Typography.Text>
        </Space>
      </Modal>

      <Modal
        title={`移动 ${fullPaths.length} 个条目`}
        open={moveOpen}
        onCancel={() => setMoveOpen(false)}
        onOk={submitMove}
        confirmLoading={moveBusy}
        okText="开始移动"
      >
        <Space direction="vertical" style={{ width: "100%" }} size={10}>
          <Space size={6} wrap>
            {entries
              .filter((e) => selectedKeys.includes(e.name))
              .map((e) => (
                <Tag key={e.name} icon={e.type === "dir" ? <FolderOutlined /> : <FileOutlined />}>
                  {e.name}
                </Tag>
              ))}
          </Space>
          <Select
            style={{ width: "100%" }}
            placeholder="选择目标目录"
            value={moveDest}
            onChange={setMoveDest}
            options={destOptions}
            showSearch
            optionFilterProp="value"
            loading={dirsQuery.isLoading}
          />
          {dirsQuery.data?.error && (
            <Alert type="warning" showIcon message="目录清单读取失败" description={dirsQuery.data.error} />
          )}
          <Typography.Text type="secondary">
            移动经云端容器服务端完成（复制到目标后删除源），不占用本地带宽；
            含大量文件的目录可能耗时数分钟，可在下方「移动任务」查看进度。
            不能移动到条目自身或其子目录内；目标已存在同名条目会被拒绝。
          </Typography.Text>
        </Space>
      </Modal>
    </Card>
  );
}

/* ---------------- 下载任务 ---------------- */

function DownloadJobsCard() {
  const queryClient = useQueryClient();
  const { data: meta } = useVolumeMeta();
  const { data } = useDownloadJobs();
  const jobs = data?.jobs ?? [];
  const cancel = async (id: string) => {
    try {
      await cancelVolumeDownload(id);
      message.success("已请求取消（云端任务随 CLI 终止，尽力而为）");
      queryClient.invalidateQueries({ queryKey: ["volume", "downloads"] });
    } catch (exc) {
      message.error(`取消失败：${exc instanceof Error ? exc.message : String(exc)}`);
    }
  };

  const columns: ColumnsType<DownloadJob> = [
    {
      title: "文件",
      dataIndex: "name",
      render: (v: string, r) => (
        <Tooltip title={r.url}>
          <Typography.Text code ellipsis style={{ maxWidth: 220 }}>
            {v}
          </Typography.Text>
        </Tooltip>
      ),
    },
    { title: "目标目录", dataIndex: "dest_dir", width: 230, render: (_v: string, r) => (
      <Space size={6}>
        <Tag color={r.volume === meta?.volume ? "blue" : "default"}>{r.volume}</Tag>
        <Typography.Text code>{r.dest_dir}</Typography.Text>
      </Space>
    ) },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (v: DownloadJob["status"], r) =>
        v === "running" ? (
          <Tag color="processing">下载中</Tag>
        ) : v === "done" ? (
          <Tooltip title={r.result_files.length ? `落卷文件：${r.result_files.join("、")}` : undefined}>
            <Tag color="success">完成</Tag>
          </Tooltip>
        ) : (
          <Tooltip title={r.error ?? undefined}>
            <Tag color="error">失败{r.error === "已被取消" ? "（已取消）" : ""}</Tag>
          </Tooltip>
        ),
    },
    {
      title: "耗时",
      key: "elapsed",
      width: 100,
      render: (_v, r) => fmtDuration((r.finished_ms ?? Date.now()) - r.created_ms),
    },
    {
      title: "详情",
      key: "log",
      width: 80,
      render: (_v, r) =>
        r.log_tail.length ? (
          <Popover
            title="下载输出（最近）"
            content={
              <pre style={{ maxWidth: 520, maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap", margin: 0 }}>
                {r.log_tail.join("\n")}
              </pre>
            }
          >
            <Button size="small" type="link">
              日志
            </Button>
          </Popover>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: "操作",
      key: "op",
      width: 90,
      render: (_v, r) =>
        r.status === "running" ? (
          <Popconfirm title="取消该下载任务？" onConfirm={() => cancel(r.id)}>
            <Button size="small" type="link" danger>
              取消
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <Card size="small" title={`下载任务（${jobs.filter((j) => j.status === "running").length} 个进行中）`}>
      {jobs.length === 0 ? (
        <EmptyGuide description="暂无下载任务 —— 在上方输入链接开始云端直下" />
      ) : (
        <Table
          columns={columns}
          dataSource={jobs}
          rowKey="id"
          size="small"
          pagination={false}
        />
      )}
    </Card>
  );
}

/* ---------------- 回收站 ---------------- */

function TrashCard({ volume }: { volume: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, refetch, isFetching } = useTrash(volume);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const entries = data?.entries ?? [];

  const invalidateVolume = () => {
    queryClient.invalidateQueries({ queryKey: ["volume", "trash", volume] });
    queryClient.invalidateQueries({ queryKey: ["volume", "list"] });
    queryClient.invalidateQueries({ queryKey: ["volume", "usage"] });
  };

  const restore = async (entry: TrashEntry) => {
    setBusyPath(entry.path);
    try {
      const res = await restoreVolumeTrash(entry.path, volume);
      message.success(`已恢复到 ${res.path}`);
      invalidateVolume();
    } catch (exc) {
      message.error(`恢复失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setBusyPath(null);
    }
  };

  const purge = async (entry: TrashEntry) => {
    setBusyPath(entry.path);
    try {
      const res = await purgeVolumeTrash([entry.path], volume);
      const failed = res.results.find((r) => !r.ok);
      if (!res.ok) throw new Error(failed?.error ?? "永久清理失败");
      message.success(`已永久清理 ${entry.name}`);
      invalidateVolume();
    } catch (exc) {
      message.error(`永久清理失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setBusyPath(null);
    }
  };

  const columns: ColumnsType<TrashEntry> = [
    {
      title: "条目",
      dataIndex: "name",
      render: (v: string, r) => (
        <Space size={6}>
          {r.type === "dir" ? <FolderOutlined /> : <FileOutlined />}
          <Typography.Text code>{v}</Typography.Text>
        </Space>
      ),
    },
    { title: "原路径", dataIndex: "path", ellipsis: true },
    {
      title: "类型",
      dataIndex: "type",
      width: 90,
      render: (v: string) => (v === "dir" ? <Tag>目录</Tag> : <Tag>文件</Tag>),
    },
    { title: "大小", dataIndex: "size", width: 110 },
    { title: "删除时间", dataIndex: "mtime", width: 200 },
    {
      title: "操作",
      key: "op",
      width: 150,
      render: (_v, r) => (
        <Space size={4}>
          <Button
            size="small"
            type="link"
            icon={<UndoOutlined />}
            disabled={busyPath !== null}
            loading={busyPath === r.path}
            onClick={() => restore(r)}
          >
            恢复
          </Button>
          <Popconfirm
            title={`永久清理 ${r.name}？`}
            description="永久清理后无法恢复，请确认该模型不再需要。"
            okButtonProps={{ danger: true }}
            onConfirm={() => purge(r)}
          >
            <Button
              size="small"
              type="link"
              danger
              icon={<DeleteOutlined />}
              disabled={busyPath !== null}
            >
              清理
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      size="small"
      title={`回收站（${entries.length}）`}
      extra={
        <Button size="small" icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
          刷新
        </Button>
      }
    >
      {data?.error && (
        <Alert type="error" showIcon message="回收站读取失败" description={data.error} style={{ marginBottom: 12 }} />
      )}
      {isLoading && !data ? (
        <Spin style={{ display: "block", margin: "24px auto" }} />
      ) : entries.length === 0 ? (
        <EmptyGuide description="回收站为空 —— 删除模型文件后可在保留期内从这里恢复" />
      ) : (
        <Table
          columns={columns}
          dataSource={entries}
          rowKey="path"
          size="small"
          pagination={false}
        />
      )}
    </Card>
  );
}

/* ---------------- 移动任务 ---------------- */

function MoveJobsCard() {
  const queryClient = useQueryClient();
  const { data: meta } = useVolumeMeta();
  const { data } = useMoveJobs();
  const jobs = data?.jobs ?? [];
  const cancel = async (id: string) => {
    try {
      await cancelVolumeMove(id);
      message.success("已请求取消（当前条目可能半复制：源保留，目标有残留）");
      queryClient.invalidateQueries({ queryKey: ["volume", "moves"] });
    } catch (exc) {
      message.error(`取消失败：${exc instanceof Error ? exc.message : String(exc)}`);
    }
  };

  const columns: ColumnsType<MoveJob> = [
    {
      title: "条目",
      key: "entries",
      render: (_v, r) => (
        <Space size={4} wrap>
          {r.entries.slice(0, 2).map((e) => (
            <Tag key={e.src} icon={e.kind === "dir" ? <FolderOutlined /> : <FileOutlined />}>
              {e.src}
            </Tag>
          ))}
          {r.entries.length > 2 && <Tag>+{r.entries.length - 2}</Tag>}
        </Space>
      ),
    },
    {
      title: "目标",
      key: "dest",
      width: 240,
      render: (_v, r) => (
        <Space size={6}>
          <Tag color={r.volume === meta?.volume ? "blue" : "default"}>{r.volume}</Tag>
          <Typography.Text code>{r.dest_dir || "（卷根目录）"}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "进度",
      key: "progress",
      width: 160,
      render: (_v, r) => {
        if (r.status === "done") return <Tag color="success">已完成</Tag>;
        if (r.status === "error") {
          return (
            <Tooltip title={r.error ?? undefined}>
              <Tag color="error">失败{r.error === "已被取消（当前条目可能半复制：源保留，目标有残留）" ? "（已取消）" : ""}</Tag>
            </Tooltip>
          );
        }
        const total = r.entries.reduce((s, e) => s + (e.files_total || 0), 0);
        const done = r.entries.reduce((s, e) => s + (e.files_done || 0), 0);
        return total ? (
          <Space size={6}>
            <Progress percent={Math.round((done / total) * 100)} size="small" style={{ width: 70 }} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {done}/{total} 文件
            </Typography.Text>
          </Space>
        ) : (
          <Tag color="processing">启动容器…</Tag>
        );
      },
    },
    {
      title: "耗时",
      key: "elapsed",
      width: 100,
      render: (_v, r) => fmtDuration((r.finished_ms ?? Date.now()) - r.created_ms),
    },
    {
      title: "详情",
      key: "log",
      width: 80,
      render: (_v, r) =>
        r.log_tail.length ? (
          <Popover
            title="搬移输出（最近）"
            content={
              <pre style={{ maxWidth: 520, maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap", margin: 0 }}>
                {r.log_tail.join("\n")}
              </pre>
            }
          >
            <Button size="small" type="link">
              日志
            </Button>
          </Popover>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: "操作",
      key: "op",
      width: 90,
      render: (_v, r) =>
        r.status === "running" ? (
          <Popconfirm title="取消该移动任务？" onConfirm={() => cancel(r.id)}>
            <Button size="small" type="link" danger>
              取消
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <Card size="small" title={`移动任务（${jobs.filter((j) => j.status === "running").length} 个进行中）`}>
      {jobs.length === 0 ? (
        <EmptyGuide description="暂无移动任务 —— 在目录浏览中勾选条目后点「移动到」" />
      ) : (
        <Table columns={columns} dataSource={jobs} rowKey="id" size="small" pagination={false} />
      )}
    </Card>
  );
}

export default function VolumePage() {
  const { data: meta } = useVolumeMeta();
  const { data: usage } = useVolumeUsage();
  const mainVolume = meta?.volume ?? "comfyui-data";
  const [volume, setVolume] = useState(mainVolume);
  // meta 异步加载完成后把卷选择器同步到主卷（初始默认值在 meta 到达前就已固定）
  useEffect(() => {
    if (meta?.volume) setVolume(meta.volume);
  }, [meta?.volume]);
  const [path, setPath] = useState("");
  const volumeNames = usage?.volumes.map((v) => v.name) ?? [];
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <UsageCard />
      <DownloadCard
        currentPath={path}
        browserVolume={volume}
        volumeNames={volumeNames}
        defaultVolume={mainVolume}
      />
      <VolumeBrowser
        volume={volume}
        setVolume={setVolume}
        path={path}
        setPath={setPath}
        volumeNames={volumeNames}
      />
      <TrashCard volume={volume} />
      <MoveJobsCard />
      <DownloadJobsCard />
    </Space>
  );
}
