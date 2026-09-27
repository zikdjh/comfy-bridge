/** 桥接器实时日志流（SSE）。两个数据源共用同一协议：
 * - /api/v1/logs/local/stream  本地 ComfyUI stdout（后端经 /internal 采集转发）
 * - /api/v1/logs/modal/stream  Modal Runner 执行控制台（执行器结构化事件，桥接器内存环形缓冲）
 */

export type LogLevel = "debug" | "detail" | "info" | "warning" | "error" | "critical";

export interface LogEntry {
  /** ISO 时间（写入时刻） */
  t: string;
  /** 已去 ANSI 的日志文本 */
  m: string;
  level: LogLevel;
}

/** Modal 通道可用性（随 SSE 心跳刷新；本地流不携带） */
export interface ChannelStatus {
  available: boolean;
  /** 不可用原因概要（应用状态：deployed/stopped/未部署/未配置…） */
  detail?: string | null;
}

export interface LogStreamHandlers {
  onSnapshot?: (entries: LogEntry[]) => void;
  onAppend?: (entries: LogEntry[]) => void;
  onStatus?: (online: boolean, error?: string) => void;
  /** 仅 Modal 流：通道就绪状态（无此回调时本地流不受影响） */
  onChannel?: (channel: ChannelStatus) => void;
}

/** 订阅日志流，返回关闭函数。断线由后端重连并重发快照，EventSource 自身断开时也会自动重试。 */
export function openLogStream(path: string, handlers: LogStreamHandlers, tail = 300): () => void {
  const es = new EventSource(`${path}?tail=${tail}`);

  const parse = <T,>(e: MessageEvent): T => JSON.parse((e as MessageEvent<string>).data) as T;

  es.addEventListener("snapshot", (e) => {
    const data = parse<{ online: boolean; entries: LogEntry[] }>(e);
    handlers.onStatus?.(data.online);
    if (data.online) handlers.onSnapshot?.(data.entries);
  });
  es.addEventListener("append", (e) => {
    handlers.onAppend?.(parse<{ entries: LogEntry[] }>(e).entries);
  });
  es.addEventListener("status", (e) => {
    const data = parse<{ online: boolean; error?: string; channel?: boolean; channel_detail?: string | null }>(e);
    handlers.onStatus?.(data.online, data.error);
    if (data.channel !== undefined) {
      handlers.onChannel?.({ available: data.channel, detail: data.channel_detail });
    }
  });
  es.onerror = () => handlers.onStatus?.(false);

  return () => es.close();
}

export function openLocalLogStream(handlers: LogStreamHandlers, tail = 300): () => void {
  return openLogStream("/api/v1/logs/local/stream", handlers, tail);
}

export function openModalLogStream(handlers: LogStreamHandlers, tail = 300): () => void {
  return openLogStream("/api/v1/logs/modal/stream", handlers, tail);
}

/** "2026-09-25T22:37:40.390353" → "22:37:40.390" */
export function formatLogTime(iso: string): string {
  const m = /\d{2}:\d{2}:\d{2}(?:\.\d+)?/.exec(iso);
  if (!m) return iso;
  const [hms, frac] = m[0].split(".");
  return frac ? `${hms}.${frac.slice(0, 3)}` : hms;
}
