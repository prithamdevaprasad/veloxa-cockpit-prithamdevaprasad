import { useCallback, useEffect, useState } from "react";
import { getToken } from "./api/client";
import { product } from "./api/product";
import type { Me } from "./api/product-types";
import { AdminPage } from "./pages/Admin";
import { AgentsPage } from "./pages/Agents";
import { ConflictsPage } from "./pages/Conflicts";
import { DepartmentsPage } from "./pages/Departments";
import { EvidenceDataPage } from "./pages/EvidenceData";
import { IntelligencePage } from "./pages/Intelligence";
import { OverviewPage } from "./pages/Overview";
import { ScenariosPage } from "./pages/Scenarios";
import { TimelinePage } from "./pages/Timeline";
import { TodayPage } from "./pages/Today";
import { AskProvider, useAsk } from "./ui/Ask";
import { EvidenceProvider } from "./ui/EvidenceDrawer";
import { Avatar, BrandMark, DemoBadge, Icon } from "./ui/kit";
import { EmptyState } from "./ui";

const NAV = [
  { route: "overview", label: "Overview", icon: "overview" },
  { route: "ask", label: "Ask Veloxa", icon: "ask" },
  { route: "agents", label: "Agents", icon: "agents" },
  { route: "departments", label: "Departments", icon: "departments" },
  { route: "evidence", label: "Evidence & Data", icon: "evidence" },
  { route: "intelligence", label: "Intelligence", icon: "intelligence" },
  { route: "whatif", label: "What-if Analysis", icon: "whatif" },
  { route: "timeline", label: "Timeline", icon: "timeline" },
] as const;
// Earlier routes stay reachable (bookmarks and tests): today, conflicts, scenarios, access.
const ALIASES: Record<string, string> = { scenarios: "whatif", access: "admin" };
const KNOWN = new Set<string>([...NAV.map((n) => n.route), "admin", "today", "conflicts", "scenarios", "access"]);

function current(): string {
  const r = window.location.hash.replace(/^#\/?/, "");
  return KNOWN.has(r) ? r : "overview";
}

function TopBar({ me, onMenu }: { me: Me | null; onMenu: () => void }) {
  const ask = useAsk();
  return (
    <header className="topbar">
      <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Open navigation"><Icon name="menu" /></button>
      <div className="tenant">
        {me ? <><strong className="tname">{me.tenant.display_name}</strong><DemoBadge show={me.tenant.synthetic} /></>
          : <strong>Veloxa</strong>}
      </div>
      <button className="search" onClick={() => ask.open()} aria-label="Ask Veloxa (Ctrl+K)">
        <Icon name="search" size={16} /> <span>Ask a question about the company</span> <kbd>Ctrl K</kbd>
      </button>
      {me && <span className="profile" title={me.principal.principal_id}>
        <Avatar name={me.principal.display_name ?? me.principal.principal_id} size={30} />
        <span className="small">{me.principal.display_name ?? me.principal.principal_id.replace("person:", "")}</span>
      </span>}
    </header>
  );
}

function Shell() {
  const [route, setRoute] = useState<string>(current());
  const [authTick, setAuthTick] = useState(0);
  const [me, setMe] = useState<Me | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const ask = useAsk();
  useEffect(() => {
    const on = () => { setRoute(current()); setNavOpen(false); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (!getToken()) { setMe(null); return; }
    product.me().then(setMe, () => setMe(null));
  }, [authTick]);
  const go = useCallback((r: string) => { window.location.hash = `/${r}`; }, []);
  const shown = ALIASES[route] ?? route;
  const active = (r: string) => shown === r || (r === "overview" && route === "today")
                                || (r === "intelligence" && route === "conflicts") || (r === "ask" && ask.full);
  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <nav className="nav" aria-label="Main">
        <div className="brand"><BrandMark />
          <span className="brand-text"><span className="brand-name">Veloxa</span><small>Executive intelligence</small></span></div>
        {NAV.map((n) => (
          <button key={n.route} aria-current={active(n.route) ? "page" : undefined} onClick={() => go(n.route)}
                  title={n.label}>
            <Icon name={n.icon} /> <span>{n.label}</span>
          </button>
        ))}
        <div className="spacer" />
        <button aria-current={shown === "admin" ? "page" : undefined} onClick={() => go("admin")}
                title={getToken() ? "Administration & Access" : "Sign in"}>
          <Icon name="admin" /> <span>{getToken() ? "Administration & Access" : "Sign in"}</span>
        </button>
      </nav>
      {navOpen && <div className="nav-scrim" onClick={() => setNavOpen(false)} />}
      <div className="workspace">
        <TopBar me={me} onMenu={() => setNavOpen(true)} />
        <main className="main" key={authTick}>
          {route === "overview" && <OverviewPage go={go} />}
          {route === "today" && <TodayPage onAsk={(q) => ask.ask(q)} />}
          {route === "ask" && !getToken() && (
            <EmptyState title="Sign in to ask Veloxa">Open Administration &amp; Access and use your token.</EmptyState>)}
          {route === "conflicts" && <ConflictsPage />}
          {route === "agents" && <AgentsPage />}
          {route === "departments" && <DepartmentsPage go={go} />}
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
