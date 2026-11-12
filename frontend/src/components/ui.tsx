import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, Json } from "../api";

export function useApi<T = Json>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(() => {
    if (!path) return;
    setLoading(true);
    api
      .get<T>(path)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(load, [load]);
  return { data, error, loading, reload: load };
}

type ToastCtx = { show: (msg: string, err?: boolean) => void; version: number; bump: () => void };
const Ctx = createContext<ToastCtx>({ show: () => {}, version: 0, bump: () => {} });
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<{ text: string; err: boolean } | null>(null);
  const [version, setVersion] = useState(0);
  const show = (text: string, err = false) => {
    setMsg({ text, err });
    setTimeout(() => setMsg(null), err ? 7000 : 3500);
  };
  return (
    <Ctx.Provider value={{ show, version, bump: () => setVersion((v) => v + 1) }}>
      {children}
      {msg && <div className={`toast ${msg.err ? "err" : ""}`}>{msg.text}</div>}
    </Ctx.Provider>
  );
}

const TONE: Record<string, string> = {
  SUCCESS: "ok", CONTINUE: "ok", APPROVED: "ok", PASSED: "ok", DEPLOYED: "ok", COMPLETED: "ok", LOW: "ok",
  HEALTHY: "ok", CANARY_HEALTHY: "ok", READY_FOR_RELEASE: "ok", RESOLVED: "ok", POSTMORTEM_COMPLETE: "ok",
  RETRY: "warn", WARNING: "warn", MEDIUM: "warn", PENDING: "warn", HUMAN_APPROVAL_REQUIRED: "warn",
  AWAITING_APPROVAL: "warn", IN_PROGRESS: "info", CANARY: "info", DEPLOYING: "info", CHANGES_REQUESTED: "warn",
  MITIGATED: "warn", INVESTIGATING: "info", OPEN: "bad", HIGH: "bad", FAILED: "bad", FAIL: "bad", BLOCK: "bad",
  BLOCKED: "bad", ROLLBACK: "bad", ROLLED_BACK: "bad", REJECTED: "bad", HEALTH_BREACH: "bad", ESCALATE: "bad",
  ESCALATED: "bad", BUDGET_EXCEEDED: "bad", MAX_ITERATIONS_REACHED: "bad", STAGNATION_DETECTED: "bad",
  CRITICAL: "crit", SEV1: "crit", SEV2: "bad", ERROR: "bad", INVALIDATED: "neutral", SKIPPED: "neutral",
  INFO: "neutral", CREATED: "neutral", DENIED: "bad", ALLOWED: "ok", PREPARED: "info",
};

export function Badge({ v, tone }: { v?: string | null; tone?: string }) {
  if (!v) return <span className="muted">—</span>;
  const t = tone || TONE[v] || "neutral";
  return <span className={`badge b-${t}`}>{v.replace(/_/g, " ")}</span>;
}

export function Sim({ label = "Simulated" }: { label?: string }) {
  return (
    <span className="badge b-sim" title="Synthetic data or simulated agent output">
      {label}
    </span>
  );
}

export const pct = (v?: number | null, digits = 0) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
export const fmtTime = (s?: string | null) => (s ? new Date(s).toLocaleString() : "—");
export const short = (h?: string | null, n = 12) => (h ? `${h.slice(0, n)}…` : "—");

export function DecisionCard({ decision, reasons, title }: { decision: string; reasons?: string[]; title?: string }) {
  const tone = TONE[decision] || "neutral";
  const cls = tone === "ok" ? "d-ok" : tone === "warn" ? "d-warn" : tone === "info" || tone === "neutral" ? "d-info" : "d-bad";
  return (
    <div className={`decision ${cls}`}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="small muted">{title || "Decision"}</div>
        <div className="title">{decision.replace(/_/g, " ")}</div>
        {reasons && reasons.length > 0 && (
          <ul>
            {reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Meter({ label, value, max = 100, color, right }: {
  label: ReactNode; value: number; max?: number; color?: string; right?: ReactNode;
}) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="meter">
      <div className="small" style={{ fontWeight: 600 }}>{label}</div>
      <div className="bar"><span style={{ width: `${w}%`, background: color || "var(--primary)" }} /></div>
      <div className="small num" style={{ textAlign: "right" }}>{right ?? value}</div>
    </div>
  );
}

export function DiffView({ diff }: { diff: string }) {
  return (
    <div className="diff">
      {diff.split("\n").map((l, i) => {
        const cls = l.startsWith("+++") || l.startsWith("---") ? "file" : l.startsWith("@@") ? "hunk"
          : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
        return cls ? <span key={i} className={cls}>{l || " "}</span> : <div key={i}>{l || " "}</div>;
      })}
    </div>
  );
}

export function Loading({ error }: { error?: string | null }) {
  return <div className="empty">{error ? `Error: ${error}` : "Loading…"}</div>;
}

export function riskColor(level?: string) {
  return level === "CRITICAL" ? "#7f1d1d" : level === "HIGH" ? "#dc2626" : level === "MEDIUM" ? "#d97706" : "#15803d";
}
