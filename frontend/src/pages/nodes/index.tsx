import { useMemo, useState } from "react";
import type { Key } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, CloudUploadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useQueryClient } from "@tanstack/react-query";
import {
  removeCloudNodes,
  syncCloudNodes,
  useLocalNodes,
  useNodeDiff,
} from "../../api/nodes";
import type { NodeDiffRow } from "../../api/nodes";
import { stopModalContainers } from "../../api/launch";
import { warmupModal } from "../../api/health";
import { EmptyGuide } from "../../components/common/EmptyGuide";

/* ---------------- 本地清单（真实数据） ---------------- */

const PAGE_SIZE_OPTIONS = [20, 50, 100, 200];

function ClassTypeTable({ classTypes }: { classTypes: string[] }) {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(20);
  const applyKeyword = (v: string) => {
    setKeyword(v);
    setPage(1);
  };
  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return kw ? classTypes.filter((c) => c.toLowerCase().includes(kw)) : classTypes;
  }, [classTypes, keyword]);

  return (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      <Input.Search
        allowClear
        placeholder="搜索 class_type（如 KSampler、SaveImage…）"
        onSearch={applyKeyword}
        onChange={(e) => applyKeyword(e.target.value)}
        style={{ maxWidth: 360 }}
      />
      <Table
        dataSource={filtered}
        rowKey={(c) => c}
        size="small"
        scroll={{ y: 320 }}
        pagination={{
          current: page,
          pageSize: size,
          onChange: (p, s) => {
            setPage(p);
            setSize(s);
          },
          showSizeChanger: true,
          pageSizeOptions: PAGE_SIZE_OPTIONS,
          showTotal: (t) => `共 ${t} 项`,
        }}
        columns={[
          {
            title: "class_type",
            dataIndex: "",
            render: (c: string) => <Typography.Text code>{c}</Typography.Text>,
          },
        ]}
      />
    </Space>
  );
}

function LocalInventory() {
  const { data, isLoading, refetch, isFetching } = useLocalNodes();

  if (isLoading) return <Spin style={{ display: "block", margin: "48px auto" }} />;

  if (!data?.online) {
    return (
      <Alert
        type="error"
        showIcon
        message="本地 ComfyUI 未连接"
        description={`${data?.error ?? "无法访问"} —— 请启动本地服务（默认 http://127.0.0.1:8188）后重试。`}
      />
    );
  }

  const cn = data.custom_nodes;
  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="success"
        showIcon
        message={`本地 ComfyUI 已连接：v${data.version ?? "?"} @ ${data.comfy_url}`}
        action={
          <Button size="small" onClick={() => refetch()} loading={isFetching}>
            重新扫描
          </Button>
        }
      />
      <Space size={40} wrap>
        <Statistic title="节点 class_type 总数" value={data.total_class_types} />
        <Statistic title="自定义节点包" value={cn.packages.length} />
        <Statistic title="单文件节点" value={cn.single_files.length} />
      </Space>

      <Card size="small" title={`自定义节点包（${cn.packages.length}）—— ${cn.path}`}>
        {cn.packages.length === 0 ? (
          <EmptyGuide description="本地未安装目录型自定义节点包；在出图界面或 ComfyUI-Manager 安装后，此处自动出现" />
        ) : (
          <Table
            columns={[
              { title: "包名", dataIndex: "name" },
              { title: "仓库", dataIndex: "repo", render: (v: string | null) => v ?? "非 git 目录" },
              { title: "commit", dataIndex: "commit", render: (v: string | null) => v ?? "—" },
              {
                title: "依赖声明",
                dataIndex: "has_requirements",
                render: (v: boolean) => (v ? <Tag color="blue">requirements.txt</Tag> : <Tag>无</Tag>),
              },
              {
                title: "状态",
                dataIndex: "enabled",
                render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag color="warning">已停用</Tag>),
              },
            ]}
            dataSource={cn.packages}
            rowKey="name"
            pagination={false}
            size="small"
          />
        )}
      </Card>

      <Card
        size="small"
        title={`class_type 全量（${data.total_class_types} 项）`}
      >
        <ClassTypeTable classTypes={data.class_types ?? []} />
      </Card>
    </Space>
  );
}

/* ---------------- 差异矩阵（本地 ↔ 云端数据卷真实对比） ---------------- */

const statusCell = (ok: boolean, extra?: string) =>
  ok ? (
    <span style={{ color: "#1a9e6c" }}>
      <CheckCircleOutlined /> 已安装{extra ? <Typography.Text type="secondary"> {extra}</Typography.Text> : null}
    </span>
  ) : (
    <span style={{ color: "#c0392b" }}>
      <CloseCircleOutlined /> 缺失
    </span>
  );

type DiffFilter = "all" | "only_local" | "only_cloud" | "synced";

function DiffMatrix() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<string>("pkg");
  const [deep, setDeep] = useState(false);
  const [filter, setFilter] = useState<DiffFilter>("all");
  const [clsKeyword, setClsKeyword] = useState("");
  const [clsPage, setClsPage] = useState(1);
  const [clsSize, setClsSize] = useState(20);
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);
  const [operating, setOperating] = useState(false);

  const { data: diff, isLoading, refetch, isFetching } = useNodeDiff(deep);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["nodes"] });
  };

  const install = async (names: string[]) => {
    setOperating(true);
    try {
      const res = await syncCloudNodes(names);
      invalidate();
      setSelectedKeys([]);
      const failed = res.results.filter((r) => !r.ok);
      if (failed.length > 0) {
        Modal.error({
          title: "部分节点同步失败",
          content: (
            <pre style={{ maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap" }}>
              {failed.map((f) => `${f.name}: ${f.error ?? "未知错误"}`).join("\n")}
            </pre>
          ),
        });
        return;
      }
      Modal.confirm({
        title: `已同步 ${res.results.length} 个节点到云端数据卷`,
        content: res.note,
        okText: "立即重启云端容器生效",
        cancelText: "稍后（下次冷启动自动生效）",
        onOk: async () => {
          const stopRes = await stopModalContainers();
          if (!stopRes.ok) {
            message.error(stopRes.detail ?? "停止云端容器失败");
            return;
          }
          message.loading({ content: "云端容器启动中（约 30s）…", key: "warmup", duration: 0 });
          const warm = await warmupModal();
          message.destroy("warmup");
          if (warm.ok) {
            message.success(`云端容器已就绪（${warm.seconds}s），节点与依赖已生效`);
          } else {
            message.error(warm.error ?? "云端容器预热失败");
          }
          queryClient.invalidateQueries({ queryKey: ["health"] });
        },
      });
    } catch (exc) {
      message.error(`同步请求失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setOperating(false);
    }
  };

  const removeFromCloud = async (names: string[]) => {
    setOperating(true);
    try {
      const res = await removeCloudNodes(names);
      invalidate();
      if (res.ok) {
        message.success(`已从云端移除 ${res.results.length} 个节点`);
      } else {
        Modal.error({
          title: "部分节点移除失败",
          content: (
            <pre style={{ maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap" }}>
              {res.results.filter((r) => !r.ok).map((f) => `${f.name}: ${f.error ?? "未知错误"}`).join("\n")}
            </pre>
          ),
        });
      }
    } catch (exc) {
      message.error(`移除请求失败：${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setOperating(false);
    }
  };

  const rows = useMemo(() => {
    const all = diff?.rows ?? [];
    if (filter === "only_local") return all.filter((r) => r.local && !r.cloud);
    if (filter === "only_cloud") return all.filter((r) => r.cloud && !r.local);
    if (filter === "synced") return all.filter((r) => r.local && r.cloud);
    return all;
  }, [diff, filter]);

  const columns: ColumnsType<NodeDiffRow> = [
    {
      title: "名称",
      dataIndex: "name",
      render: (v: string, r) => (
        <Space size={6}>
          <Typography.Text code>{v}</Typography.Text>
          {r.kind === "single" && <Tag>单文件</Tag>}
          {r.local && !r.enabled && <Tag color="warning">已停用</Tag>}
        </Space>
      ),
    },
    {
      title: "commit",
      dataIndex: "commit",
      width: 110,
      render: (v: string | null, r) => {
        if (r.kind === "single") return "—";
        if (!r.local) return r.cloud_commit ?? "云端未知";
        if (!r.cloud) return v ?? "非 git 目录";
        return v === r.cloud_commit
          ? (v ?? "非 git 目录")
          : `${v ?? "本地未知"} → ${r.cloud_commit ?? "云端未知"}`;
      },
    },
    {
      title: "依赖声明",
      dataIndex: "has_requirements",
      width: 130,
      render: (v: boolean) => (v ? <Tag color="blue">requirements.txt</Tag> : <Tag>无</Tag>),
    },
    { title: "本地", dataIndex: "local", width: 100, render: (v: boolean) => statusCell(v) },
    {
      title: "云端",
      dataIndex: "cloud",
      width: 140,
      render: (v: boolean, r) => statusCell(v, v ? (r.cloud_size ?? undefined) : undefined),
    },
    {
      title: "一致性",
      dataIndex: "state",
      width: 100,
      render: (v: NodeDiffRow["state"]) =>
        v === "same" ? <Tag color="green">一致</Tag>
          : v === "changed" ? <Tag color="orange">版本不同</Tag>
            : v === "missing_cloud" ? <Tag color="blue">待同步</Tag>
              : v === "missing_local" ? <Tag color="purple">云端独有</Tag>
                : "—",
    },
    {
      title: "操作",
      width: 120,
      render: (_v, r) => {
        if (r.local && !r.cloud) {
          return (
            <Button
              size="small"
              type="link"
              icon={<CloudUploadOutlined />}
              disabled={operating}
              onClick={() => install([r.name])}
            >
              安装到云端
            </Button>
          );
        }
        if (r.cloud) {
          return (
            <Popconfirm
              title="从云端移除该节点？"
              description="只删除云端数据卷上的副本，本地不受影响。"
              okButtonProps={{ danger: true }}
              onConfirm={() => removeFromCloud([r.name])}
            >
              <Button size="small" type="link" danger disabled={operating}>
                从云端移除
              </Button>
            </Popconfirm>
          );
        }
        return null;
      },
    },
  ];

  const clsData = useMemo(() => {
    const ct = diff?.class_types;
    if (!ct) return { ready: false, rows: [] as { name: string; local: boolean; cloud: boolean }[] };
    const localSet = new Set(ct.local ?? []);
    const cloudSet = new Set(ct.cloud ?? []);
    const all = [...new Set([...localSet, ...cloudSet])].sort();
    return {
      ready: ct.cloud !== null,
      rows: all.map((name) => ({ name, local: localSet.has(name), cloud: cloudSet.has(name) })),
    };
  }, [diff]);

  const clsFiltered = useMemo(() => {
    const kw = clsKeyword.trim().toLowerCase();
    if (!kw) return clsData.rows;
    return clsData.rows.filter((r) => r.name.toLowerCase().includes(kw));
  }, [clsData, clsKeyword]);

  if (isLoading && !diff) return <Spin style={{ display: "block", margin: "48px auto" }} />;

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      {diff && !diff.ok && (
        <Alert type="error" showIcon message="云端清单读取失败" description={diff.cloud_error ?? undefined} />
      )}
      {diff && diff.ok && (
        <Alert
          type="info"
          showIcon
          message={`云端数据卷 ${diff.volume} · 应用状态 ${diff.app_state ?? "未知"} · 已同步 ${diff.counts.synced} / 仅本地 ${diff.counts.only_local} / 仅云端 ${diff.counts.only_cloud}`}
          description="「安装到云端」把节点文件写入云端数据卷；Python 依赖由容器下次冷启动时按 requirements.txt 自动安装。"
          action={
            <Space>
              <Button size="small" onClick={() => refetch()} loading={isFetching}>
                重新扫描
              </Button>
              {deep ? (
                <Button size="small" onClick={() => refetch()} loading={isFetching}>
                  刷新云端对比
                </Button>
              ) : (
                <Popconfirm
                  title="对比云端 class_type？"
                  description="需要拉取云端 /object_info；容器未运行时会触发冷启动（数十秒、约 $0.02）。"
                  onConfirm={() => setDeep(true)}
                >
                  <Button size="small">对比云端 class_type</Button>
                </Popconfirm>
              )}
            </Space>
          }
        />
      )}
      {diff?.class_types.error && (
        <Alert type="warning" showIcon message="云端 class_type 对比失败" description={diff.class_types.error} />
      )}

      <Card
        size="small"
        title="节点差异"
        extra={
          <Space>
            <Segmented
              value={view}
              onChange={(v) => setView(v as string)}
              options={[
                { label: "按节点包", value: "pkg" },
                { label: "按 class_type", value: "cls" },
              ]}
            />
            {view === "pkg" && (
              <Select<DiffFilter>
                size="small"
                style={{ width: 140 }}
                value={filter}
                onChange={setFilter}
                options={[
                  { value: "all", label: "全部" },
                  { value: "only_local", label: "仅云端缺失" },
                  { value: "only_cloud", label: "仅本地缺失" },
                  { value: "synced", label: "已同步" },
                ]}
              />
            )}
          </Space>
        }
      >
        {view === "pkg" ? (
          <Space direction="vertical" size={10} style={{ width: "100%" }}>
            <Button
              type="primary"
              size="small"
              icon={<CloudUploadOutlined />}
              disabled={selectedKeys.length === 0 || operating}
              loading={operating && selectedKeys.length > 0}
              onClick={() => install(selectedKeys.map(String))}
            >
              安装到云端（{selectedKeys.length}）
            </Button>
            <Table<NodeDiffRow>
              columns={columns}
              dataSource={rows}
              pagination={rows.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
              size="small"
              rowKey="name"
              rowSelection={{
                selectedRowKeys: selectedKeys,
                onChange: setSelectedKeys,
                getCheckboxProps: (r) => ({ disabled: !r.local || r.cloud || !r.enabled }),
              }}
            />
          </Space>
        ) : (
          <Space direction="vertical" size={10} style={{ width: "100%" }}>
            {!clsData.ready ? (
              <EmptyGuide
                description="尚未对比云端 class_type —— 点击右上角「对比云端 class_type」拉取云端节点注册表（可能触发容器冷启动）"
                actionLabel="对比云端 class_type"
                onAction={() => setDeep(true)}
              />
            ) : (
              <>
                <Input.Search
                  allowClear
                  placeholder="搜索 class_type"
                  onSearch={(v) => {
                    setClsKeyword(v);
                    setClsPage(1);
                  }}
                  onChange={(e) => {
                    setClsKeyword(e.target.value);
                    setClsPage(1);
                  }}
                  style={{ maxWidth: 360 }}
                />
                <Table
                  dataSource={clsFiltered}
                  rowKey={(r) => r.name}
                  size="small"
                  scroll={{ y: 360 }}
                  pagination={{
                    current: clsPage,
                    pageSize: clsSize,
                    onChange: (p, s) => {
                      setClsPage(p);
                      setClsSize(s);
                    },
                    showSizeChanger: true,
                    pageSizeOptions: PAGE_SIZE_OPTIONS,
                    showTotal: (t) => `共 ${t} 项`,
                  }}
                  columns={[
                    {
                      title: "class_type",
                      dataIndex: "name",
                      render: (v: string) => <Typography.Text code>{v}</Typography.Text>,
                    },
                    { title: "本地", dataIndex: "local", width: 110, render: (v: boolean) => statusCell(v) },
                    { title: "云端", dataIndex: "cloud", width: 110, render: (v: boolean) => statusCell(v) },
                  ]}
                />
              </>
            )}
          </Space>
        )}
      </Card>
    </Space>
  );
}

export default function NodesPage() {
  return (
    <Card size="small">
      <Tabs
        defaultActiveKey="local"
        items={[
          { key: "local", label: "本地清单", children: <LocalInventory /> },
          { key: "diff", label: "差异矩阵", children: <DiffMatrix /> },
        ]}
      />
    </Card>
  );
}
