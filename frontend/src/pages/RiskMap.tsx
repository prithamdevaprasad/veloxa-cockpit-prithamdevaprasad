// Business risk and dependency map: every admitted source (HR, finance, GitHub, email, transcripts, Jira, data
// room uploads) pooled into one graph of the links the records state. A force layout on canvas: zoom and pan, drag
// nodes, search to fly to a node, click to see its links, and ask Veloxa about any node with that context kept.
import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "../api/client";
import { useApi } from "../api/useApi";
import { useAsk } from "../ui/Ask";
import { Icon } from "../ui/kit";
import { Button, EmptyState, ErrorState, Loading } from "../ui";

interface MapNode { id: string; kind: string; label: string; degree: number; detail?: string | null; status?: string;
  due?: string; assignee?: string; count?: number; system?: string; record?: string; department?: string;
  warnings?: string[]; unknowns?: string[] }
interface MapEdge { source: string; target: string; relation: string }
interface MapOut { nodes: MapNode[]; edges: MapEdge[]; hidden_for_access: number; notes: string[];
  read_basis: { batch_seq: number; banner?: string } }

const KINDS: Record<string, { label: string; color: string }> = {
  concern: { label: "Concerns", color: "#e0566e" },
  commitment: { label: "Releases & commitments", color: "#e39b3a" },
  issue: { label: "Tracker issues", color: "#9b82f0" },
  person: { label: "People", color: "#3fb39a" },
  department: { label: "Departments", color: "#e7e1f7" },
  system: { label: "Sources", color: "#5aa0e6" },
  record: { label: "Records", color: "#8b84a3" },
};
const RELATION: Record<string, string> = {
  works_in: "works in", reports_to: "reports to", from_system: "comes from", stated_in: "stated in",
  evidence_for: "rests on", assigned_to: "assigned to", mentions: "mentions", concerns: "concerns", conflict: "conflicts with",
};

type Sim = { x: number; y: number; vx: number; vy: number; fixed?: boolean };

export function RiskMapPage() {
  const [m, reload] = useApi((s) => get<MapOut>("/v1/map", s), []);
  return (
    <div className="map-page">
      <header className="page-hero compact">
        <span className="hero-icon"><Icon name="graph" size={24} /></span>
        <div><h1>Risk & dependency map</h1>
          <p>Every source Veloxa has admitted, joined by the links the records state: people, teams, releases, Jira
            issues, documents, transcripts and the concerns they support. Nothing is inferred.</p></div>
      </header>
      {m.state === "loading" && <Loading lines={6} label="Building the map" />}
      {m.state === "error" && <ErrorState error={m.error} onRetry={reload} />}
      {m.state === "ready" && (m.data.nodes.length === 0
        ? <EmptyState title="Nothing to map yet">{m.data.notes[0]}</EmptyState>
        : <MapView data={m.data} />)}
    </div>
  );
}

function MapView({ data }: { data: MapOut }) {
  const ask = useAsk();
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set(["record"].filter(() => data.nodes.length > 260)));
  const [query, setQuery] = useState("");
  const [question, setQuestion] = useState("");

  const byId = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data]);
  const links = useMemo(() => {
    const adj = new Map<string, { other: string; relation: string; out: boolean }[]>();
    for (const e of data.edges) {
      if (!adj.has(e.source)) adj.set(e.source, []);
      if (!adj.has(e.target)) adj.set(e.target, []);
      adj.get(e.source)!.push({ other: e.target, relation: e.relation, out: true });
      adj.get(e.target)!.push({ other: e.source, relation: e.relation, out: false });
    }
    return adj;
  }, [data]);

  // Simulation state lives in refs: the canvas redraws every frame without re-rendering React.
  const sim = useRef(new Map<string, Sim>());
  const view = useRef({ x: 0, y: 0, k: 0.8 });
  const alpha = useRef(1);
  const target = useRef<{ x: number; y: number; k: number } | null>(null);
  const state = useRef({ selected, hover, hidden });
  state.current = { selected, hover, hidden };

  useEffect(() => {
    const kinds = Object.keys(KINDS);
    data.nodes.forEach((n, i) => {
      if (sim.current.has(n.id)) return;
      const a = (kinds.indexOf(n.kind) / kinds.length) * Math.PI * 2 + (i % 7) * 0.05;
      const r = 120 + (i % 13) * 14;
      sim.current.set(n.id, { x: Math.cos(a) * r, y: Math.sin(a) * r, vx: 0, vy: 0 });
    });
    alpha.current = 1;
  }, [data]);

  useEffect(() => {
    const c = canvas.current!, ctx = c.getContext("2d")!;
    let raf = 0;
    const visible = () => data.nodes.filter((n) => !state.current.hidden.has(n.kind));
    function step() {
      if (alpha.current < 0.005) return;
      const ns = visible(), P = sim.current;
      for (let i = 0; i < ns.length; i++) {
        const a = P.get(ns[i].id)!;
        for (let j = i + 1; j < ns.length; j++) {
          const b = P.get(ns[j].id)!;
          let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
          if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
          if (d2 > 250000) continue;
          const f = (900 / d2) * alpha.current;
          a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
        }
        a.vx -= a.x * 0.004 * alpha.current; a.vy -= a.y * 0.004 * alpha.current;
      }
      for (const e of data.edges) {
        if (state.current.hidden.has(byId.get(e.source)?.kind ?? "") || state.current.hidden.has(byId.get(e.target)?.kind ?? "")) continue;
        const a = P.get(e.source), b = P.get(e.target);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = ((d - 60) / d) * 0.06 * alpha.current;
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      }
      for (const n of ns) {
        const p = P.get(n.id)!;
        if (p.fixed) { p.vx = p.vy = 0; continue; }
        p.vx *= 0.6; p.vy *= 0.6; p.x += p.vx; p.y += p.vy;
      }
      alpha.current *= 0.985;
    }
    function draw() {
      const w = c.clientWidth, h = c.clientHeight, dpr = window.devicePixelRatio || 1;
      if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
      if (target.current) {
        const v = view.current, t = target.current;
        v.x += (t.x - v.x) * 0.15; v.y += (t.y - v.y) * 0.15; v.k += (t.k - v.k) * 0.15;
        if (Math.abs(t.x - v.x) + Math.abs(t.y - v.y) + Math.abs(t.k - v.k) < 0.5) target.current = null;
      }
      const v = view.current, P = sim.current;
      const { selected: sel, hover: hov, hidden: hid } = state.current;
      const focus = hov ?? sel;
      const near = new Set<string>(focus ? [focus, ...(links.get(focus) ?? []).map((l) => l.other)] : []);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#17122a"; ctx.fillRect(0, 0, w, h);
      ctx.translate(w / 2 + v.x, h / 2 + v.y); ctx.scale(v.k, v.k);
      for (const e of data.edges) {
        const sa = byId.get(e.source), sb = byId.get(e.target);
        if (!sa || !sb || hid.has(sa.kind) || hid.has(sb.kind)) continue;
        const a = P.get(e.source)!, b = P.get(e.target)!;
        const on = focus && (e.source === focus || e.target === focus);
        ctx.strokeStyle = e.relation === "conflict" ? "rgba(224,86,110,0.8)" : on ? "rgba(201,186,255,0.9)"
          : focus ? "rgba(140,128,180,0.08)" : "rgba(140,128,180,0.28)";
        ctx.lineWidth = (on ? 1.6 : 0.8) / Math.sqrt(v.k);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
      for (const n of data.nodes) {
        if (hid.has(n.kind)) continue;
        const p = P.get(n.id)!;
        const r = radius(n);
        const dim = focus && !near.has(n.id);
        ctx.globalAlpha = dim ? 0.18 : 1;
        ctx.fillStyle = KINDS[n.kind]?.color ?? "#999";
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.fill();
        if (n.id === sel) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2 / v.k; ctx.stroke(); }
        const showLabel = near.has(n.id) || (v.k > 1.4) || ["department", "system", "commitment", "concern"].includes(n.kind) && v.k > 0.55;
        if (showLabel && !dim) {
          ctx.font = `${(n.id === focus ? 13 : 11) / v.k}px "IBM Plex Sans", sans-serif`;
          ctx.fillStyle = "rgba(236,232,250,0.92)";
          const label = n.label.length > 42 ? n.label.slice(0, 40) + "…" : n.label;
          ctx.fillText(label, p.x + r + 3 / v.k, p.y + 4 / v.k);
        }
        ctx.globalAlpha = 1;
      }
    }
    const loop = () => { step(); draw(); raf = requestAnimationFrame(loop); };
    loop();
    return () => cancelAnimationFrame(raf);
  }, [data, byId, links]);

  // Pointer: drag background to pan, drag a node to move it, wheel to zoom at the cursor, click to select.
  useEffect(() => {
    const c = canvas.current!;
    let drag: { id: string | null; sx: number; sy: number; moved: boolean } | null = null;
    const toWorld = (ev: { clientX: number; clientY: number }) => {
      const b = c.getBoundingClientRect(), v = view.current;
      return { x: (ev.clientX - b.left - b.width / 2 - v.x) / v.k, y: (ev.clientY - b.top - b.height / 2 - v.y) / v.k };
    };
    const hit = (ev: MouseEvent) => {
      const w = toWorld(ev);
      let best: string | null = null, bd = Infinity;
      for (const n of data.nodes) {
        if (state.current.hidden.has(n.kind)) continue;
        const p = sim.current.get(n.id)!, d = Math.hypot(p.x - w.x, p.y - w.y);
        if (d < radius(n) + 4 / view.current.k && d < bd) { best = n.id; bd = d; }
      }
      return best;
    };
    const down = (ev: MouseEvent) => { target.current = null; drag = { id: hit(ev), sx: ev.clientX, sy: ev.clientY, moved: false }; };
    const move = (ev: MouseEvent) => {
      if (!drag) { const h = hit(ev); setHover(h); c.style.cursor = h ? "pointer" : "grab"; return; }
      const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.id) {
        const p = sim.current.get(drag.id)!, w = toWorld(ev);
        p.x = w.x; p.y = w.y; p.fixed = true; alpha.current = Math.max(alpha.current, 0.3);
      } else { view.current.x += dx; view.current.y += dy; drag.sx = ev.clientX; drag.sy = ev.clientY; }
    };
    const up = () => {
      if (drag && !drag.moved) setSelected(drag.id);
      if (drag?.id) sim.current.get(drag.id)!.fixed = false;
      drag = null;
    };
    const wheel = (ev: WheelEvent) => {
      ev.preventDefault(); target.current = null;
      const v = view.current, b = c.getBoundingClientRect();
      const k = Math.min(6, Math.max(0.15, v.k * Math.exp(-ev.deltaY * 0.0015)));
      const mx = ev.clientX - b.left - b.width / 2, my = ev.clientY - b.top - b.height / 2;
      v.x = mx - ((mx - v.x) / v.k) * k; v.y = my - ((my - v.y) / v.k) * k; v.k = k;
    };
    c.addEventListener("mousedown", down); window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up); c.addEventListener("wheel", wheel, { passive: false });
    return () => { c.removeEventListener("mousedown", down); window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up); c.removeEventListener("wheel", wheel); };
  }, [data]);

  function flyTo(id: string) {
    const p = sim.current.get(id);
    const n = byId.get(id);
    if (!p || !n) return;
    if (hidden.has(n.kind)) setHidden(new Set([...hidden].filter((k) => k !== n.kind)));
    setSelected(id);
    target.current = { x: -p.x * 2.2, y: -p.y * 2.2, k: 2.2 };
  }
  const matches = query.trim().length > 1
    ? data.nodes.filter((n) => `${n.label} ${n.detail ?? ""}`.toLowerCase().includes(query.toLowerCase())).slice(0, 8) : [];
  const sel = selected ? byId.get(selected) : undefined;
  const selLinks = sel ? (links.get(sel.id) ?? []) : [];
  const counts = Object.fromEntries(Object.keys(KINDS).map((k) => [k, data.nodes.filter((n) => n.kind === k).length]));

  function askAbout(q: string) {
    if (!q.trim()) return;
    ask.ask(sel ? `${q.trim()} (about ${KINDS[sel.kind]?.label.replace(/s$/, "").toLowerCase() ?? sel.kind} "${sel.label}")` : q.trim(),
            sel ? { kind: "map_node", subject: sel.label } : undefined, true);
    setQuestion("");
  }

  return (
    <div className="map-layout">
      <div className="map-canvas" ref={wrap}>
        <canvas ref={canvas} aria-label="Risk and dependency map. Use the search and the panel to explore it with a keyboard." />
        <div className="map-legend" role="group" aria-label="Show or hide node types">
          {Object.entries(KINDS).map(([k, v]) => counts[k] > 0 && (
            <label key={k} className={hidden.has(k) ? "off" : ""}>
              <input type="checkbox" checked={!hidden.has(k)} onChange={() => {
                const s = new Set(hidden); if (s.has(k)) s.delete(k); else s.add(k); setHidden(s); alpha.current = 0.6; }} />
              <span className="dot" style={{ background: v.color }} /> {v.label} <span className="muted">{counts[k]}</span>
            </label>))}
        </div>
        <div className="map-zoom">
          <button className="icon-btn" aria-label="Zoom in" onClick={() => { target.current = { ...view.current, k: Math.min(6, view.current.k * 1.4) }; }}>+</button>
          <button className="icon-btn" aria-label="Zoom out" onClick={() => { target.current = { ...view.current, k: Math.max(0.15, view.current.k / 1.4) }; }}>−</button>
          <button className="icon-btn" aria-label="Reset view" onClick={() => { setSelected(null); target.current = { x: 0, y: 0, k: 0.8 }; }}>⟲</button>
        </div>
      </div>
      <aside className="map-panel" aria-label="Map details">
        <div className="map-search">
          <Icon name="search" size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a person, release, issue, document…"
                 aria-label="Find a node" onKeyDown={(e) => { if (e.key === "Enter" && matches[0]) { flyTo(matches[0].id); setQuery(""); } }} />
        </div>
        {matches.length > 0 && <ul className="plain map-results">{matches.map((n) => (
          <li key={n.id}><button onClick={() => { flyTo(n.id); setQuery(""); }}>
            <span className="dot" style={{ background: KINDS[n.kind]?.color }} />{n.label}
            <span className="muted small">{KINDS[n.kind]?.label}</span></button></li>))}</ul>}

        {sel ? (
          <div className="map-node">
            <span className="kind-tag" style={{ borderColor: KINDS[sel.kind]?.color }}>{KINDS[sel.kind]?.label.replace(/s$/, "")}</span>
            <h2>{sel.label}</h2>
            {sel.detail && <p>{sel.detail}</p>}
            <dl className="meta small">
              {sel.status && <><dt>Status</dt><dd>{sel.status}</dd></>}
              {sel.due && <><dt>Due</dt><dd>{sel.due}</dd></>}
              {sel.assignee && <><dt>Assignee</dt><dd>{sel.assignee}</dd></>}
              {sel.department && <><dt>Department</dt><dd>{sel.department}</dd></>}
              {sel.system && <><dt>Source</dt><dd>{sel.system.replace(/_/g, " ")}</dd></>}
              {sel.count !== undefined && <><dt>Records</dt><dd>{sel.count}</dd></>}
            </dl>
            {(sel.warnings?.length ?? 0) > 0 && <><h4>Early warnings</h4>
              <ul className="bullets small">{sel.warnings!.map((w) => <li key={w}>{w}</li>)}</ul></>}
            {(sel.unknowns?.length ?? 0) > 0 && <><h4>Still unknown</h4>
              <ul className="bullets small">{sel.unknowns!.map((w) => <li key={w}>{w}</li>)}</ul></>}
            <h4>Linked to ({selLinks.length})</h4>
            <ul className="plain map-links">{selLinks.slice(0, 40).map((l, i) => {
              const o = byId.get(l.other);
              return o && <li key={l.other + i}><button onClick={() => flyTo(o.id)}>
                <span className="dot" style={{ background: KINDS[o.kind]?.color }} />
                <span className="rel">{l.out ? RELATION[l.relation] ?? l.relation : `← ${RELATION[l.relation] ?? l.relation}`}</span>
                {o.label}</button></li>;
            })}</ul>
            <div className="row" style={{ gap: 8 }}>
              <Button variant="primary" onClick={() => askAbout(`What do the records say about this, what depends on it, and what is still unknown?`)}>
                Ask about this</Button>
              {["concern", "commitment", "issue"].includes(sel.kind) &&
                <Button onClick={() => ask.task(sel.kind === "issue" ? { key: sel.label, mode: "update" }
                  : { summary: `Follow up: ${sel.label}`, description: `From the Veloxa risk map: ${sel.label}` })}>
                  {sel.kind === "issue" ? "Update in Jira" : "Create Jira task"}</Button>}
            </div>
          </div>
        ) : (
          <div className="map-node muted">
            <h2>Explore the map</h2>
            <p className="small">Click any dot to see what it is linked to. Drag to move around, scroll to zoom, and
              use the search to jump to a node. Red lines are figures the sources disagree on.</p>
            <ul className="bullets small">{data.notes.map((x) => <li key={x}>{x}</li>)}
              {data.hidden_for_access > 0 && <li>{data.hidden_for_access} items are hidden because you can't read their evidence.</li>}</ul>
          </div>
        )}
        <form className="map-ask" onSubmit={(e) => { e.preventDefault(); askAbout(question); }}>
          <label className="small muted" htmlFor="map-q">{sel ? `Ask about “${sel.label}”` : "Ask about the map"}</label>
          <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
            <input id="map-q" className="input" value={question} onChange={(e) => setQuestion(e.target.value)}
                   placeholder={sel ? "e.g. who is affected if this slips?" : "e.g. what depends on Atlas GA?"} />
            <Button variant="primary" type="submit" disabled={!question.trim()}>Ask</Button>
          </div>
        </form>
      </aside>
    </div>
  );
}

function radius(n: MapNode) {
  const base = n.kind === "department" ? 9 : n.kind === "system" ? 8 : n.kind === "concern" ? 7 : n.kind === "commitment" ? 8 : 3.5;
  return base + Math.min(8, Math.sqrt(n.degree || 0));
}
