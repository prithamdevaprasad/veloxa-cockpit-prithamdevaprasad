import { useCallback, useEffect, useState } from "react";
import { getToken } from "./api/client";
import { product } from "./api/product";
import type { Me } from "./api/product-types";
import { AdminPage } from "./pages/Admin";
import { AgentsPage } from "./pages/Agents";
import { AskPage } from "./pages/Ask";
import { ConflictsPage } from "./pages/Conflicts";
import { DepartmentsPage } from "./pages/Departments";
import { EvidenceDataPage } from "./pages/EvidenceData";
import { IntelligencePage } from "./pages/Intelligence";
import { OverviewPage } from "./pages/Overview";
import { ScenariosPage } from "./pages/Scenarios";
import { TimelinePage } from "./pages/Timeline";
import { TodayPage } from "./pages/Today";
import { AskProvider, useAsk } from "./ui/AskPill";
import { EvidenceProvider } from "./ui/EvidenceDrawer";
import { Avatar, DemoBadge, Icon } from "./ui/kit";

const NAV = [
  { route: "overview", label: "Overview", icon: "overview" },
  { route: "agents", label: "Agents", icon: "agents" },
  { route: "departments", label: "Departments", icon: "departments" },
  { route: "evidence", label: "Evidence & Data", icon: "evidence" },
  { route: "intelligence", label: "Intelligence", icon: "intelligence" },
  { route: "whatif", label: "What-if Analysis", icon: "whatif" },
  { route: "timeline", label: "Timeline", icon: "timeline" },
] as const;
// Earlier routes stay reachable (bookmarks and tests): today, ask, conflicts, scenarios, access.
const ALIASES: Record<string, string> = { scenarios: "whatif", access: "admin" };
const KNOWN = new Set<string>([...NAV.map((n) => n.route), "admin", "today", "ask", "conflicts", "scenarios", "access"]);

function current(): string {
  const r = window.location.hash.replace(/^#\/?/, "");
  return KNOWN.has(r) ? r : "overview";
}

function TopBar({ me, onSearch }: { me: Me | null; onSearch: () => void }) {
  return (
    <header className="topbar">
      <div className="small muted">{me ? <>{me.tenant.display_name} <DemoBadge show={me.tenant.synthetic} /></> : "Veloxa"}</div>
      <button className="search" onClick={onSearch} aria-label="Search and ask (Ctrl+K)">
        <Icon name="search" size={16} /> <span>Search or ask…</span> <kbd>Ctrl K</kbd>
      </button>
      {me && <span className="profile" title={me.principal.principal_id}>
        <Avatar name={me.principal.display_name ?? me.principal.principal_id} />
        <span className="small">{me.principal.display_name ?? me.principal.principal_id.replace("person:", "")}</span>
      </span>}
    </header>
  );
}

function Shell() {
  const [route, setRoute] = useState<string>(current());
  const [askSeed, setAskSeed] = useState<string | undefined>();
  const [authTick, setAuthTick] = useState(0);
  const [me, setMe] = useState<Me | null>(null);
  const ask = useAsk();
  useEffect(() => {
    const on = () => setRoute(current());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (!getToken()) { setMe(null); return; }
    product.me().then(setMe, () => setMe(null));
  }, [authTick]);
  const go = useCallback((r: string) => { window.location.hash = `/${r}`; }, []);
  const consumed = useCallback(() => setAskSeed(undefined), []);
  const shown = ALIASES[route] ?? route;
  const active = (r: string) => shown === r || (r === "overview" && route === "today")
                                || (r === "intelligence" && route === "conflicts");
  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <div className="brand">Veloxa<small>CEO intelligence</small></div>
        {NAV.map((n) => (
          <button key={n.route} aria-current={active(n.route) ? "page" : undefined} onClick={() => go(n.route)}>
            <Icon name={n.icon} /> <span>{n.label}</span>
          </button>
        ))}
        <div className="spacer" />
        <button aria-current={shown === "admin" ? "page" : undefined} onClick={() => go("admin")}>
          <Icon name="admin" /> <span>{getToken() ? "Administration & Access" : "Sign in"}</span>
        </button>
      </nav>
      <div className="workspace">
        <TopBar me={me} onSearch={() => ask.ask("")} />
        <main className="main" key={authTick}>
          {route === "overview" && <OverviewPage go={go} />}
          {route === "today" && <TodayPage onAsk={(q) => { setAskSeed(q); go("ask"); }} />}
          {route === "ask" && <AskPage initial={askSeed} onConsumed={consumed} />}
          {route === "conflicts" && <ConflictsPage />}
          {route === "agents" && <AgentsPage />}
          {route === "departments" && <DepartmentsPage />}
          {route === "evidence" && <EvidenceDataPage />}
          {route === "intelligence" && <IntelligencePage />}
          {shown === "whatif" && <ScenariosPage />}
          {route === "timeline" && <TimelinePage synthetic={Boolean(me?.tenant.synthetic)} />}
          {shown === "admin" && <AdminPage onAuthChange={() => setAuthTick((t) => t + 1)} />}
        </main>
      </div>
    </div>
  );
}

export function App() {
  return (
    <EvidenceProvider>
      <AskProvider>
        <Shell />
      </AskProvider>
    </EvidenceProvider>
  );
}
