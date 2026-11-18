// Executive design-system primitives: icons, KPI cards, charts, tabs, avatars and DEMO markers.
// Charts are plain SVG driven by API data (no chart library); each has a text equivalent for accessibility.
import { useId, useState, type ReactNode } from "react";
import { AgentAvatar } from "./art";

const PATHS: Record<string, string> = {
  overview: "M4 13h6V4H4v9zm0 7h6v-4H4v4zm10 0h6v-9h-6v9zm0-16v4h6V4h-6z",
  ask: "M5 5h14v10H9l-4 4V5zm4 4h6M9 12h4",
  agents: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 8c0-3.3 3.1-6 7-6s7 2.7 7 6",
  departments: "M4 20V8l8-4 8 4v12M9 20v-5h6v5M4 20h16",
  evidence: "M7 3h7l5 5v13H7V3zm7 0v5h5M10 13h6M10 17h6",
  intelligence: "M12 3a6 6 0 0 0-3.5 10.9V17h7v-3.1A6 6 0 0 0 12 3zM10 21h4",
  whatif: "M4 19h16M5 15l4-4 3 3 6-7m0 0h-4m4 0v4",
  timeline: "M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  admin: "M12 3l8 3.5v5.5c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6.5L12 3z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 2-4.3-4.3",
  send: "M4 12h14m-5-6 6 6-6 6",
  close: "M6 6l12 12M18 6 6 18",
  expand: "M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7",
  collapse: "M4 14h6v6M20 10h-6V4M10 14l-7 7M14 10l7-7",
  menu: "M4 7h16M4 12h16M4 17h16",
  plus: "M12 5v14M5 12h14",
  file: "M7 3h7l5 5v13H7V3z",
  chat: "M5 5h14v10H9l-4 4V5z",
  upload: "M12 15V4m-5 5 5-5 5 5M4 20h16",
  back: "M15 6l-6 6 6 6",
  alert: "M12 4l9 16H3L12 4zm0 6v4m0 3v.5",
};

export function Icon({ name, size = 18, label }: { name: keyof typeof PATHS | string; size?: number; label?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7}
         strokeLinecap="round" strokeLinejoin="round" role={label ? "img" : undefined} aria-label={label}
         aria-hidden={label ? undefined : true}>
      <path d={PATHS[name] ?? PATHS.file} />
    </svg>
  );
}

/** Veloxa's mark: a stylised V cut from a lens. */
export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="8" fill="#ffffff" fillOpacity="0.12" />
      <path d="M7.5 8.5l6.5 12 6.5-12" fill="none" stroke="#ffffff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="14" cy="9.5" r="2" fill="#f5c26b" />
    </svg>
  );
}

export function DemoBadge({ show }: { show: boolean }) {
  return show ? <span className="demo-badge" title="Synthetic demo data">DEMO</span> : null;
}

/** Kept for existing call sites: an avatar for an agent or person id (illustration only for synthetic agents). */
export function Avatar({ name, size }: { name: string; size?: number }) {
  return <AgentAvatar id={name} size={size} />;
}

export function KpiCard({ label, value, basis, onSource }: { label: string; value: ReactNode; basis: string;
                                                               onSource?: () => void }) {
  return (
    <div className="kpi">
      <div className="kpi-label"><span>{label}</span>
        {onSource && <button className="src" onClick={onSource} aria-label={`Source for ${label}`}>Source</button>}</div>
      <div className={`kpi-value${typeof value === "string" && value.length > 14 ? " long" : ""}`}>{value}</div>
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

export function fmt(v: number): string {
  const a = Math.abs(v);
  if (a >= 1_000_000) return `${(v / 1_000_000).toFixed(a >= 10_000_000 ? 0 : 1)}M`;
  if (a >= 10_000) return `${(v / 1_000).toFixed(0)}k`;
  if (a >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return String(Math.round(v * 10) / 10);
}

/** A line chart over ordered points (x = category labels). Hover or focus shows each value. */
export function LineChart({ points, label, height = 220 }: { points: { x: string; value: number }[]; label: string;
                                                             height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");
  if (points.length === 0) return <Guidance title="No data">Nothing admitted supports this chart yet.</Guidance>;
  const w = 720, h = height, padL = 44, padR = 12, padT = 12, padB = 26;
  const vals = points.map((p) => p.value);
  const max = Math.max(...vals), min = Math.min(0, ...vals);
  const sx = (i: number) => padL + (i * (w - padL - padR)) / Math.max(1, points.length - 1);
  const sy = (v: number) => h - padB - ((v - min) / Math.max(1e-9, max - min)) * (h - padT - padB);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.value).toFixed(1)}`).join(" ");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + (max - min) * f);
  const every = Math.max(1, Math.ceil(points.length / 8));
  return (
    <figure className="chart" aria-label={label} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label}>
        <defs>
          <linearGradient id={`area-${gid}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#5b3cc4" stopOpacity="0.22" />
            <stop offset="1" stopColor="#5b3cc4" stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={w - padR} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={padL - 8} y={sy(t) + 4} className="axis" textAnchor="end">{fmt(t)}</text>
          </g>
        ))}
        {points.map((p, i) => i % every === 0 || i === points.length - 1 ? (
          <text key={p.x} x={sx(i)} y={h - 6} className="axis" textAnchor="middle">{p.x}</text>) : null)}
        <path d={`${d} L${sx(points.length - 1)},${sy(min)} L${sx(0)},${sy(min)} Z`} fill={`url(#area-${gid})`} />
        <path d={d} className="line" />
        {hover !== null && <line x1={sx(hover)} x2={sx(hover)} y1={padT} y2={h - padB} className="guide" />}
        {points.map((p, i) => (
          <g key={p.x}>
            <rect className="hit" x={sx(i) - (w - padL - padR) / Math.max(1, points.length - 1) / 2} y={padT}
                  width={(w - padL - padR) / Math.max(1, points.length - 1)} height={h - padT - padB}
                  onMouseEnter={() => setHover(i)} />
            <circle cx={sx(i)} cy={sy(p.value)} r={hover === i ? 5 : 2.5} className="dot" />
          </g>
        ))}
      </svg>
      {hover !== null && (
        <div className="chart-tip" style={{ left: `${(sx(hover) / w) * 100}%`, top: `${(sy(points[hover].value) / h) * 100}%` }}>
          <span>{points[hover].x}</span>{points[hover].value.toLocaleString()}
        </div>
      )}
      <figcaption className="sr-only">{points.map((p) => `${p.x}: ${p.value}`).join("; ")}</figcaption>
    </figure>
  );
}

/** Horizontal bars, optionally two series (e.g. budget vs actual). Negative values are drawn in the failure colour. */
export function BarChart({ rows, label, series, listLabel }: { rows: { label: string; values: number[] }[]; label: string;
                                                               series: string[]; listLabel?: string }) {
  if (rows.length === 0) return <Guidance title="No data">Nothing admitted supports this chart yet.</Guidance>;
  const max = Math.max(1, ...rows.flatMap((r) => r.values.map(Math.abs)));
  return (
    <figure className="bars" aria-label={label}>
      {series.length > 1 && (
        <div className="legend small">{series.map((s, i) => <span key={s}><i className={`sw s${i}`} />{s}</span>)}</div>
      )}
      <ul className="plain" aria-label={listLabel}>
        {rows.map((r) => (
          <li key={r.label} className="bar-row">
            <span className="bar-label small" title={r.label}>{r.label}</span>
            <span className="bar-tracks">
              {r.values.map((v, i) => (
                <span key={i} className={`bar-fill s${i}${v < 0 ? " neg" : ""}`} style={{ width: `${(Math.abs(v) / max) * 100}%` }}
                      title={`${series[i] ?? ""} ${v.toLocaleString()}`} />
              ))}
            </span>
            <span className="bar-val small">{r.values.map(fmt).join(" / ")}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

export const PALETTE = ["#5b3cc4", "#2f9e7a", "#2f6fc4", "#d9822b", "#c0567a", "#8e74e0", "#3ea6b5", "#9a8f2e"];

/** A donut over category counts (from the API). The centre shows the total; hover or focus shows a slice. */
export function Donut({ data, label, centreLabel }: { data: { label: string; count: number }[]; label: string;
                                                     centreLabel: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const total = data.reduce((a, d) => a + d.count, 0);
  if (!total) return <Guidance title="No data">Nothing admitted supports this chart yet.</Guidance>;
  const r = 70, c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <figure className="donut" aria-label={label}>
      <svg viewBox="0 0 200 200" role="img" aria-label={label}>
        <circle cx="100" cy="100" r={r} fill="none" stroke="var(--surface-2)" strokeWidth="26" />
        {data.map((d, i) => {
          const len = (d.count / total) * c;
          const seg = (
            <circle key={d.label} cx="100" cy="100" r={r} fill="none" stroke={PALETTE[i % PALETTE.length]}
                    strokeWidth={hover === i ? 30 : 26} strokeDasharray={`${Math.max(0, len - 2)} ${c}`}
                    strokeDashoffset={-offset} transform="rotate(-90 100 100)" className="donut-seg"
                    onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />);
          offset += len;
          return seg;
        })}
        <text x="100" y="96" textAnchor="middle" className="donut-total">{hover === null ? total : data[hover].count}</text>
        <text x="100" y="118" textAnchor="middle" className="donut-sub">{hover === null ? centreLabel : data[hover].label}</text>
      </svg>
      <ul className="plain donut-legend">
        {data.map((d, i) => (
          <li key={d.label} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <i style={{ background: PALETTE[i % PALETTE.length] }} /><span>{d.label}</span>
            <b>{d.count}</b>
          </li>))}
      </ul>
      <figcaption className="sr-only">{data.map((d) => `${d.label}: ${d.count}`).join("; ")}</figcaption>
    </figure>
  );
}
