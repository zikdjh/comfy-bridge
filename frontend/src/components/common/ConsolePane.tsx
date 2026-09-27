import { useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { Button, Card, Input, Segmented, Select, Space, Switch, Tooltip, Typography } from "antd";
import {
  ClearOutlined,
  DownloadOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { Virtuoso } from "react-virtuoso";
import {
  formatLogTime,
  type ChannelStatus,
  type LogEntry,
  type LogLevel,
  type LogStreamHandlers,
} from "../../api/logs";

/** 缓冲上限可选档位（控制日志总长度；超出丢弃最旧行）。 */
const CAP_OPTIONS = [200, 500, 1000, 3000, 5000];
const DEFAULT_CAP = 1000;

type Status = "connecting" | "online" | "offline";
type LevelFilter = "all" | "warning" | "error";
/** 缓冲条目：id 单调递增，供虚拟列表做稳定 key（裁剪后仍可锚定位置）。 */
type BufEntry = LogEntry & { id: number };

const LEVEL_COLOR: Record<LogLevel, string> = {
  debug: "#7d8799",
  detail: "#8fa3bf",
  info: "#c9d1d9",
  warning: "#e3b341",
  error: "#f85149",
  critical: "#ff7b72",
};

/** 工具行占位（含下间距），供两栏标题/脚注对齐参考。 */
export const TOOLBAR_MIN_HEIGHT = 32;
/** 终端区的绝对下限：页面整体高度不足时靠外层 minHeight 撑开滚动，这里只防极端压缩 */
const TERMINAL_FLOOR = 240;
const EMPTY_FLOOR = 200;

export const TERMINAL_BOX_STYLE: React.CSSProperties = {
  overflowX: "hidden",
  background: "#0d1117",
  borderRadius: 8,
  padding: "10px 14px",
  fontFamily: "'Cascadia Mono', Consolas, 'Courier New', monospace",
  fontSize: 12,
  lineHeight: 1.6,
};

function levelPasses(level: LogLevel, filter: LevelFilter): boolean {
  if (filter === "all") return true;
  if (filter === "warning") return level === "warning" || level === "error" || level === "critical";
  return level === "error" || level === "critical";
}

function LogLine({ e }: { e: BufEntry }) {
  return (
    <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
      <span style={{ color: "#6e7681", marginRight: 10, userSelect: "none" }}>{formatLogTime(e.t)}</span>
      <span style={{ color: LEVEL_COLOR[e.level] ?? LEVEL_COLOR.info }}>{e.m.trimEnd() || " "}</span>
    </div>
  );
}

export interface ConsolePaneProps {
  title: string;
  /** 订阅本栏数据流，返回关闭函数（本地=stdout SSE / Modal=执行器事件 SSE） */
  openStream: (handlers: LogStreamHandlers) => () => void;
  downloadPrefix: string;
  footnote: string;
  emptyHint: (streamOnline: boolean) => string;
  /** 标题右侧状态区：推送状态与（可选的）通道状态由调用方渲染 */
  headerExtra: (info: {
    status: Status;
    online: boolean;
    statusError?: string;
    channel?: ChannelStatus;
  }) => ReactNode;
}

/** 实时控制台通用终端面板：SSE 快照+增量、虚拟滚动、过滤/暂停/跟随/导出。
 *  本地 ComfyUI 与 Modal Runner 两栏共用，行为保持一致。 */
export function ConsolePane({ title, openStream, downloadPrefix, footnote, emptyHint, headerExtra }: ConsolePaneProps) {
  const [version, bump] = useReducer((x: number) => x + 1, 0);
  const [status, setStatus] = useState<Status>("connecting");
  const [statusError, setStatusError] = useState<string>();
  const [channel, setChannel] = useState<ChannelStatus>();
  const [stick, setStick] = useState(true);
  const [paused, setPaused] = useState(false);
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("all");
  const [textFilter, setTextFilter] = useState("");
  const [cap, setCap] = useState(DEFAULT_CAP);

  const bufferRef = useRef<BufEntry[]>([]);
  const stickRef = useRef(true);
  const pausedRef = useRef(false);
  const capRef = useRef(DEFAULT_CAP);
  const idRef = useRef(0);

  useEffect(() => {
    const close = openStream({
      onSnapshot: (entries) => {
        bufferRef.current = entries.map((e) => ({ ...e, id: idRef.current++ }));
        if (!pausedRef.current) bump();
      },
      onAppend: (entries) => {
        const buf = bufferRef.current;
        for (const e of entries) buf.push({ ...e, id: idRef.current++ });
        if (buf.length > capRef.current) buf.splice(0, buf.length - capRef.current);
        if (!pausedRef.current) bump();
      },
      onStatus: (online, error) => {
        setStatus(online ? "online" : "offline");
        setStatusError(error);
      },
      onChannel: (ch) => setChannel(ch),
    });
    return close;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toolbarRef = useRef<HTMLDivElement>(null);
  // 窄卡片下工具行换成紧凑密度（图标按钮、去文字标签），尽量保持单行，减少换行抖动
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const el = toolbarRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setCompact(width > 0 && width < 700);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const togglePause = () => {
    const next = !pausedRef.current;
    pausedRef.current = next;
    setPaused(next);
    if (!next) bump(); // 恢复时把暂停期间累积的行一次性放出
  };

  const changeCap = (value: number) => {
    capRef.current = value;
    setCap(value);
    const buf = bufferRef.current;
    if (buf.length > value) buf.splice(0, buf.length - value);
    bump();
  };

  const visible = useMemo(() => {
    const kw = textFilter.trim().toLowerCase();
    return bufferRef.current.filter(
      (e) => levelPasses(e.level, levelFilter) && (!kw || e.m.toLowerCase().includes(kw)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, levelFilter, textFilter]);

  const download = () => {
    const text = visible.map((e) => `${e.t.replace("T", " ")} ${e.m}`).join("");
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${downloadPrefix}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const online = status === "online";

  return (
    <Card
      size="small"
      title={
        <Space size={10}>
          {title}
          {headerExtra({ status, online, statusError, channel })}
        </Space>
      }
      extra={
        <span style={{ fontSize: 12, color: "#5a6272", fontVariantNumeric: "tabular-nums" }}>
          {visible.length}/{bufferRef.current.length} 行
        </span>
      }
      style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}
      styles={{ body: { flex: 1, display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" } }}
    >
      <div ref={toolbarRef} style={{ minHeight: TOOLBAR_MIN_HEIGHT, marginBottom: 8 }}>
        <Space size={8} wrap>
          <Input
            size="small"
            allowClear
            prefix={<SearchOutlined style={{ color: "#8a90a2" }} />}
            placeholder="过滤关键字"
            style={{ width: compact ? 100 : 160 }}
            value={textFilter}
            onChange={(e) => setTextFilter(e.target.value)}
          />
          <Segmented
            size="small"
            value={levelFilter}
            onChange={(v) => setLevelFilter(v as LevelFilter)}
            options={[
              { value: "all", label: "全部" },
              { value: "warning", label: "警告" },
              { value: "error", label: "错误" },
            ]}
          />
          <Tooltip title="开启后新日志自动滚动到底部；向上查看历史会自动暂停跟随">
            <Space size={4}>
              {!compact && <span style={{ fontSize: 12, color: "#5a6272" }}>跟随</span>}
              <Switch
                size="small"
                checked={stick}
                onChange={(v) => {
                  stickRef.current = v;
                  setStick(v);
                }}
              />
            </Space>
          </Tooltip>
          <Tooltip title={paused ? "继续渲染新日志（暂停期间后台仍在缓冲）" : "暂停渲染，后台继续缓冲"}>
            <Button size="small" icon={paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />} onClick={togglePause}>
              {!compact && (paused ? "继续" : "暂停")}
            </Button>
          </Tooltip>
          <Tooltip title="清空当前缓冲（不影响后端日志）">
            <Button
              size="small"
              icon={<ClearOutlined />}
              onClick={() => {
                bufferRef.current = [];
                bump();
              }}
            >
              {!compact && "清屏"}
            </Button>
          </Tooltip>
          <Tooltip title="导出当前过滤后的日志">
            <Button size="small" icon={<DownloadOutlined />} onClick={download}>
              {!compact && "导出"}
            </Button>
          </Tooltip>
          <Tooltip title="缓冲上限：超出后自动丢弃最旧行">
            <Select
              size="small"
              style={{ width: compact ? 76 : 104 }}
              value={cap}
              onChange={changeCap}
              options={CAP_OPTIONS.map((v) => ({ value: v, label: compact ? String(v) : `上限 ${v}` }))}
            />
          </Tooltip>
        </Space>
      </div>

      {visible.length > 0 ? (
        <div
          style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
          onWheel={(e) => {
            // 只在用户主动上滚时关闭跟随：突发日志下滚动滞后会触发 atBottom=false 误报，
            // 不能据此关跟随，否则推送会被永久卡住
            if (e.deltaY < 0 && stickRef.current) {
              stickRef.current = false;
              setStick(false);
            }
          }}
          onKeyDown={(e) => {
            if (["ArrowUp", "PageUp", "Home"].includes(e.key) && stickRef.current) {
              stickRef.current = false;
              setStick(false);
            }
          }}
        >
          <Virtuoso
            data={visible}
            computeItemKey={(_, e) => e.id}
            // 回调形式：只要跟随开着就强制吸底。布尔模式内部带“仅在贴底时跟随”检查，
            // 突发追加时上一次滚动未落定会被判为离开底部而跳过，越落越远
            followOutput={() => stickRef.current && !pausedRef.current}
            atBottomStateChange={(atBottom: boolean) => {
              // 仅用 true 恢复跟随（用户手动滚回底部）；false 一律忽略
              if (atBottom && !stickRef.current) {
                stickRef.current = true;
                setStick(true);
              }
            }}
            initialItemCount={40}
            increaseViewportBy={{ top: 600, bottom: 800 }}
            itemContent={(_, e) => <LogLine e={e} />}
            style={{ ...TERMINAL_BOX_STYLE, flex: 1, minHeight: TERMINAL_FLOOR }}
          />
        </div>
      ) : (
        <div
          style={{
            ...TERMINAL_BOX_STYLE,
            flex: 1,
            minHeight: EMPTY_FLOOR,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#6e7681",
          }}
        >
          {emptyHint(online)}
        </div>
      )}

      <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 8 }}>
        {footnote}
      </Typography.Text>
    </Card>
  );
}
