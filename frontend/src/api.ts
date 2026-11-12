/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = any;

let actorId = (() => {
  try {
    return localStorage.getItem("actor") || "user-alex";
  } catch {
    return "user-alex";
  }
})();

export function getActor() {
  return actorId;
}
export function setActor(id: string) {
  actorId = id;
  try {
    localStorage.setItem("actor", id);
  } catch {
    /* storage unavailable */
  }
}

async function request<T = Json>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-Actor-Id": actorId },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? data ?? res.statusText));
  }
  return data as T;
}

export const api = {
  get: <T = Json>(p: string) => request<T>("GET", p),
  post: <T = Json>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  patch: <T = Json>(p: string, b?: unknown) => request<T>("PATCH", p, b ?? {}),
};
