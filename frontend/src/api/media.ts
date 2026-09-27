import { useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

export type MediaKind = "image" | "video";

export interface MediaItem {
  /** 相对 output 根目录的路径（POSIX 分隔符） */
  path: string;
  kind: MediaKind;
  size: number;
  /** epoch 秒 */
  mtime: number;
}

export interface LocalMedia {
  root: string;
  total: number;
  items: MediaItem[];
}

export function useLocalMedia(kind: "all" | MediaKind, limit = 500) {
  return useQuery({
    queryKey: ["media", "local", kind, limit],
    queryFn: ({ signal }) =>
      apiGet<LocalMedia>(`/api/v1/media/local?kind=${kind}&limit=${limit}`, signal),
    refetchInterval: 30_000,
  });
}

export const mediaFileUrl = (path: string) =>
  `/api/v1/media/local/file?path=${encodeURIComponent(path)}`;

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function formatMediaTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString("zh-CN", { hour12: false });
}
