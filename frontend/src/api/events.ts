/** 统一事件流（P1-5）：任务、节点同步、部署、成本告警。 */

export type BridgeEventName = "job" | "node_sync" | "deploy" | "cost_alert" | "resync";

export interface BridgeEvent<T = unknown> {
  id: number;
  event: BridgeEventName;
  data: T;
  ts_ms: number;
}

export interface BridgeEventHandlers {
  onEvent: (item: BridgeEvent) => void;
  onStatus?: (online: boolean, detail?: string) => void;
}

/** 订阅统一事件流；浏览器的 EventSource 会按 Last-Event-ID 自动重连。 */
export function openBridgeEventStream(handlers: BridgeEventHandlers): () => void {
  const es = new EventSource("/api/v1/events/stream");
  let opened = false;

  const parse = <T,>(e: MessageEvent<string>): BridgeEvent<T> => ({
    id: Number(e.lastEventId || 0),
    event: "job",
    data: JSON.parse(e.data) as T,
    ts_ms: Date.now(),
  });
  const names: BridgeEventName[] = ["job", "node_sync", "deploy", "cost_alert", "resync"];
  for (const name of names) {
    es.addEventListener(name, (e) => {
      handlers.onEvent({ ...parse(e as MessageEvent<string>), event: name });
    });
  }
  es.addEventListener("ready", () => {
    opened = true;
    handlers.onStatus?.(true);
  });
  es.onerror = () => {
    // EventSource 会自动重连；仅在曾经连上后掉线时提示回退，避免首次连接失败刷屏。
    handlers.onStatus?.(false, opened ? "事件流连接失败，已回退轮询" : "事件流连接中");
  };

  return () => es.close();
}
