import { invoke } from "@tauri-apps/api/core";
export const isDesktop = () => "__TAURI_INTERNALS__" in window;
export async function request<T>(
  op: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const payload = { op, ...args };
  const response = isDesktop()
    ? await invoke<{ ok: boolean; result: T; error?: string }>("core_request", {
        payload,
      })
    : await fetch("/__mmtr__/rpc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(async (r) => {
        if (!r.ok)
          throw new Error(
            "本地核心不可用；请通过 Tauri 应用或 npm run dev 启动。",
          );
        return r.json() as Promise<{ ok: boolean; result: T; error?: string }>;
      });
  if (!response.ok) throw new Error(response.error ?? "操作失败");
  return response.result;
}
