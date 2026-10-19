import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  FileImageOutlined,
  PlayCircleFilled,
  ReloadOutlined,
  VideoCameraOutlined,
} from "@ant-design/icons";
import {
  formatBytes,
  formatMediaTime,
  mediaFileUrl,
  useLocalMedia,
  type MediaItem,
} from "../../api/media";
import { bridgeTokens as t } from "../../theme";

const TILE_HEIGHT = 160;

function MediaTile({ item, onOpen }: { item: MediaItem; onOpen: (item: MediaItem) => void }) {
  const name = item.path.split("/").pop() ?? item.path;
  return (
    <Tooltip title={item.path}>
      <div
        onClick={() => onOpen(item)}
        style={{
          position: "relative",
          borderRadius: 8,
          overflow: "hidden",
          cursor: "pointer",
          background: t.colors.console.background,
          border: `1px solid ${t.colors.border}`,
        }}
      >
        {item.kind === "image" ? (
          <img
            src={mediaFileUrl(item.path)}
            alt={name}
            loading="lazy"
            style={{ width: "100%", height: TILE_HEIGHT, objectFit: "cover", display: "block" }}
          />
        ) : (
          <>
            <video
              src={mediaFileUrl(item.path)}
              muted
              preload="metadata"
              style={{ width: "100%", height: TILE_HEIGHT, objectFit: "cover", display: "block" }}
            />
            <PlayCircleFilled
              style={{
                position: "absolute",
                inset: 0,
                margin: "auto",
                width: "fit-content",
                height: "fit-content",
                fontSize: 40,
                color: "rgba(255,255,255,0.85)",
                filter: "drop-shadow(0 1px 4px rgba(0,0,0,0.6))",
              }}
            />
          </>
        )}
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 0,
            padding: "4px 8px",
            background: "linear-gradient(transparent, rgba(0,0,0,0.65))",
            color: "#ffffff",
            fontSize: 12,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {name}
        </div>
      </div>
    </Tooltip>
  );
}

/** 媒体库：浏览本地 ComfyUI output 目录产出的图片/视频。 */
export default function MediaPage() {
  const [kind, setKind] = useState<"all" | "image" | "video">("all");
  const [folder, setFolder] = useState<string>("all");
  const [keyword, setKeyword] = useState("");
  const [selected, setSelected] = useState<MediaItem | null>(null);
  const { data, isLoading, isFetching, refetch } = useLocalMedia("all", 1000);

  const folders = useMemo(() => {
    const set = new Set<string>();
    for (const item of data?.items ?? []) {
      const dir = item.path.split("/").slice(0, -1).join("/");
      set.add(dir || "/");
    }
    return [...set].sort();
  }, [data]);

  const filtered = useMemo(() => {
    return (data?.items ?? []).filter((item) => {
      if (kind !== "all" && item.kind !== kind) return false;
      const dir = item.path.split("/").slice(0, -1).join("/") || "/";
      if (folder !== "all" && dir !== folder) return false;
      if (keyword && !item.path.toLowerCase().includes(keyword.toLowerCase())) return false;
      return true;
    });
  }, [data, kind, folder, keyword]);

  const counts = useMemo(() => {
    const items = data?.items ?? [];
    return {
      all: items.length,
      image: items.filter((i) => i.kind === "image").length,
      video: items.filter((i) => i.kind === "video").length,
    };
  }, [data]);

  return (
    <Card
      title={
        <Space size={8}>
          <FileImageOutlined />
          <span>媒体库</span>
        </Space>
      }
      extra={
        <Space size={8}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            共 {data?.total ?? 0} 个文件（图片 {counts.image} / 视频 {counts.video}）
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching} size="small">
            刷新
          </Button>
        </Space>
      }
    >
      <Space size={12} wrap style={{ marginBottom: 16 }}>
        <Segmented
          value={kind}
          onChange={(v) => setKind(v as typeof kind)}
          options={[
            { label: `全部 ${counts.all}`, value: "all" },
            { label: `图片 ${counts.image}`, value: "image" },
            { label: `视频 ${counts.video}`, value: "video" },
          ]}
        />
        <Select
          value={folder}
          onChange={setFolder}
          style={{ width: 200 }}
          options={[
            { label: "全部目录", value: "all" },
            ...folders.map((f) => ({ label: f, value: f })),
          ]}
        />
        <Input.Search
          placeholder="按文件名过滤"
          allowClear
          style={{ width: 220 }}
          onSearch={setKeyword}
          onChange={(e) => !e.target.value && setKeyword("")}
        />
      </Space>

      {isLoading ? (
        <div style={{ padding: 60, textAlign: "center" }}>
          <Spin tip="扫描 output 目录…" />
        </div>
      ) : filtered.length === 0 ? (
        <Empty
          description={
            <span>
              暂无匹配的产出文件
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                ComfyUI SaveImage / 视频节点写入 output 目录后会自动出现在这里（{data?.root ?? "—"}）
              </Typography.Text>
            </span>
          }
          style={{ padding: "48px 0" }}
        />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            gap: 12,
          }}
        >
          {filtered.map((item) => (
            <MediaTile key={item.path} item={item} onOpen={setSelected} />
          ))}
        </div>
      )}

      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}>
        来源：本地 ComfyUI output 目录（图片 {counts.image} / 视频 {counts.video}）；M1 接入后，Modal 远端回传结果将一并汇入。
      </Typography.Paragraph>

      <Modal
        open={!!selected}
        onCancel={() => setSelected(null)}
        footer={null}
        width="min(1100px, 92vw)"
        title={
          selected && (
            <Space size={8} wrap>
              {selected.kind === "video" ? <VideoCameraOutlined /> : <FileImageOutlined />}
              <span style={{ fontWeight: 400 }}>{selected.path}</span>
              <Tag>{formatBytes(selected.size)}</Tag>
              <Tag>{formatMediaTime(selected.mtime)}</Tag>
            </Space>
          )
        }
        destroyOnHidden
      >
        {selected &&
          (selected.kind === "image" ? (
            <img
              src={mediaFileUrl(selected.path)}
              alt={selected.path}
              style={{ maxWidth: "100%", maxHeight: "70vh", display: "block", margin: "0 auto" }}
            />
          ) : (
            <video
              src={mediaFileUrl(selected.path)}
              controls
              autoPlay
              style={{ maxWidth: "100%", maxHeight: "70vh", display: "block", margin: "0 auto" }}
            />
          ))}
        <div style={{ marginTop: 12, textAlign: "center" }}>
          <Typography.Link href={mediaFileUrl(selected?.path ?? "")} target="_blank">
            在新标签页打开原文件
          </Typography.Link>
        </div>
      </Modal>
    </Card>
  );
}
