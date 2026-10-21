/** 管理面 API 薄封装（原生 fetch，见组件选型 §2.3）。 */

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 管理 API 统一从 JSON detail 提取可行动错误，避免把协议原文直接展示给用户。 */
export async function apiErrorFromResponse(res: Response): Promise<ApiError> {
  const raw = await res.text().catch(() => "");
  let detail = "";
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      detail = typeof parsed.detail === "string" ? parsed.detail : raw;
    } catch {
      detail = raw;
    }
  }
  return new ApiError(
    res.status,
    detail.slice(0, 400) || `${res.status} ${res.statusText}`,
  );
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw await apiErrorFromResponse(res);
  }
  return (await res.json()) as T;
}

export async function apiSend<T>(
  path: string,
  method: "POST" | "PUT" | "DELETE",
  body?: unknown,
): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    throw await apiErrorFromResponse(res);
  }
  return (await res.json()) as T;
}
