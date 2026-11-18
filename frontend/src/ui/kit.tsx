// Executive design-system primitives: icons, KPI cards, charts, tabs, avatars and DEMO markers.
// Charts are plain SVG driven by API data (no chart library); each has a text equivalent for accessibility.
import { useState, type ReactNode } from "react";

const PATHS: Record<string, string> = {
  overview: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  agents: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5z",
  departments: "M3 21V7l9-4 9 4v14h-6v-6H9v6H3z",
  evidence: "M6 2h9l5 5v15H6V2zm8 1v5h5",
  intelligence: "M12 2a7 7 0 0 0-4 12.7V18h8v-3.3A7 7 0 0 0 12 2zM9 21h6",
  whatif: "M4 19h16M6 15l4-4 3 3 5-6",
  timeline: "M12 6v6l4 2M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
  admin: "M12 1l9 4v6c0 5-4 9.7-9 11-5-1.3-9-6-9-11V5l9-4z",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm10 2-5-5",
  send: "M3 20l18-8L3 4v6l12 2-12 2v6z",
  close: "M6 6l12 12M18 6 6 18",
  file: "M6 2h9l5 5v15H6V2z",
  chat: "M4 4h16v12H8l-4 4V4z",
  upload: "M12 3v12m-5-7 5-5 5 5M4 21h16",
};

export function Icon({ name, size = 18, label }: { name: keyof typeof PATHS | string; size?: number; label?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
         strokeLinecap="round" strokeLinejoin="round" role={label ? "img" : undefined} aria-label={label}
         aria-hidden={label ? undefined : true}>
      <path d={PATHS[name] ?? PATHS.file} />
    </svg>
  );
}

export function DemoBadge({ show }: { show: boolean }) {
  return show ? <span className="demo-badge" title="Synthetic demo data">DEMO</span> : null;
}

export function Avatar({ name }: { name: string }) {
  const initials = name.replace(/^syn-/, "").split(/[-\s]+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0]?.toUpperCase()).join("");
  return <span className="avatar" aria-hidden="true">{initials || "?"}</span>;
}

export function KpiCard({ label, value, basis, dark }: { label: string; value: ReactNode; basis: string; dark?: boolean }) {
  return (
    <div className={`kpi${dark ? " dark" : ""}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-basis">{basis}</div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, label }:
  { tabs: { value: T; label: string; count?: number }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)}>
          {t.label}{t.count !== undefined && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Guidance({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="guidance">
      <strong>{title}</strong>
      <div className="small muted">{children}</div>
    </div>
  );
}

function fmt(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(0)}k`;
  return String(Math.round(v * 10) / 10);
}

/** A line chart over ordered points (x = category labels). */
export function LineChart({ points, label, height = 180 }: { points: { x: string; value: number }[]; label: string;
                                                             height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length === 0) return <Guidance title="No data">Nothing admitted supports this chart yet.</Guidance>;
  const w = 600, h = height, pad = 32;
  const max = Math.max(...points.map((p) => p.value)), min = Math.min(0, ...points.map((p) => p.value));
  const sx = (i: number) => pad + (i * (w - pad * 2)) / Math.max(1, points.length - 1);
  const sy = (v: number) => h - pad - ((v - min) / Math.max(1, max - min)) * (h - pad * 2);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.value).toFixed(1)}`).join(" ");
  return (
    <figure className="chart" aria-label={label}>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label} preserveAspectRatio="none">
        {[0, 0.5, 1].map((f) => <line key={f} x1={pad} x2={w - pad} y1={sy(min + (max - min) * f)}
                                      y2={sy(min + (max - min) * f)} className="grid" />)}
        <path d={`${d} L${sx(points.length - 1)},${sy(min)} L${sx(0)},${sy(min)} Z`} className="area" />
        <path d={d} className="line" />
        {points.map((p, i) => (
          <circle key={p.x} cx={sx(i)} cy={sy(p.value)} r={hover === i ? 5 : 3} className="dot"
                  onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
        <text x={pad} y={14} className="axis">{fmt(max)}</text>
        <text x={pad} y={h - 8} className="axis">{points[0].x}</text>
        <text x={w - pad} y={h - 8} className="axis" textAnchor="end">{points[points.length - 1].x}</text>
      </svg>
      {hover !== null && <div className="chart-tip">{points[hover].x}: {points[hover].value.toLocaleString()}</div>}
      <figcaption className="sr-only">{points.map((p) => `${p.x}: ${p.value}`).join("; ")}</figcaption>
    </figure>
  );
}

/** Horizontal bars, optionally two series (e.g. budget vs actual). */
export function BarChart({ rows, label, series }: { rows: { label: string; values: number[] }[]; label: string;
                                                   series: string[] }) {
  if (rows.length === 0) return <Guidance title="No data">Nothing admitted supports this chart yet.</Guidance>;
  const max = Math.max(1, ...rows.flatMap((r) => r.values));
  return (
    <figure className="bars" aria-label={label}>
      {series.length > 1 && (
        <div className="legend small">{series.map((s, i) => <span key={s}><i className={`sw s${i}`} />{s}</span>)}</div>
      )}
      <ul className="plain">
        {rows.map((r) => (
          <li key={r.label} className="bar-row">
            <span className="bar-label small">{r.label}</span>
            <span className="bar-tracks">
              {r.values.map((v, i) => (
                <span key={i} className={`bar-fill s${i}`} style={{ width: `${(v / max) * 100}%` }}
                      title={`${series[i] ?? ""} ${v.toLocaleString()}`} />
              ))}
            </span>
            <span className="bar-val small mono">{r.values.map(fmt).join(" / ")}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
