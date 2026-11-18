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
import { WorryPage } from "./pages/Worry";
import { AskProvider, useAsk } from "./ui/Ask";
import { EvidenceProvider } from "./ui/EvidenceDrawer";
import { Avatar, BrandMark, DemoBadge, Icon } from "./ui/kit";
import { EmptyState } from "./ui";

// Sidebar = groups (icon above a short label); the sub-navigation shows the pages of the active group.
const GROUPS = [
  { id: "home", label: "Home", icon: "overview", pages: [["overview", "Overview"], ["worry", "What to worry about"],
                                                         ["timeline", "Timeline"]] },
  { id: "ask", label: "Ask Veloxa", icon: "ask", pages: [["ask", "Ask Veloxa"]] },
  { id: "people", label: "People", icon: "agents", pages: [["agents", "Agents"], ["departments", "Departments"]] },
  { id: "intel", label: "Intelligence", icon: "intelligence", pages: [["intelligence", "Intelligence"],
                                                                      ["evidence", "Evidence & Data"],
                                                                      ["whatif", "What-if Analysis"]] },
] as const;
const ADMIN = { id: "admin", label: "Admin", icon: "admin", pages: [["admin", "Administration & Access"]] } as const;
// Earlier routes stay reachable (bookmarks and tests): today, conflicts, scenarios, access.
const ALIASES: Record<string, string> = { scenarios: "whatif", access: "admin", today: "overview", conflicts: "intelligence" };
const KNOWN = new Set<string>([...GROUPS.flatMap((g) => g.pages.map((p) => p[0])), "admin", "today", "conflicts",
                               "scenarios", "access"]);
const groupOf = (route: string) => [...GROUPS, ADMIN].find((g) => g.pages.some((p) => p[0] === (ALIASES[route] ?? route)))
  ?? GROUPS[0];

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
  const group = ask.full ? GROUPS[1] : groupOf(route);
  const navButton = (g: (typeof GROUPS)[number] | typeof ADMIN, label: string, title: string) => (
    <button key={g.id} aria-current={group.id === g.id ? "page" : undefined} onClick={() => go(g.pages[0][0])}
            title={title}>
      <Icon name={g.icon} size={20} /> <span>{label}</span>
    </button>
  );
  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <nav className="nav" aria-label="Main">
        <div className="brand"><BrandMark />
          <span className="brand-text"><span className="brand-name">Veloxa</span><small>Executive intelligence</small></span></div>
        {GROUPS.map((g) => navButton(g, g.label, g.label))}
        <div className="spacer" />
        {navButton(ADMIN, getToken() ? "Admin" : "Sign in", getToken() ? "Administration & Access" : "Sign in")}
      </nav>
      {navOpen && <div className="nav-scrim" onClick={() => setNavOpen(false)} />}
      <div className="workspace">
        <TopBar me={me} onMenu={() => setNavOpen(true)} />
        {group.pages.length > 1 && (
          <nav className="subnav" aria-label={`${group.label} pages`}>
            {group.pages.map(([r, label]) => (
              <button key={r} aria-current={shown === r ? "page" : undefined} onClick={() => go(r)}>{label}</button>))}
          </nav>)}
        <main className="main" key={authTick}>
          {route === "overview" && <OverviewPage go={go} />}
          {route === "today" && <TodayPage onAsk={(q) => ask.ask(q)} />}
          {route === "ask" && !getToken() && (
            <EmptyState title="Sign in to ask Veloxa">Open Administration &amp; Access and use your token.</EmptyState>)}
          {route === "conflicts" && <ConflictsPage />}
          {route === "worry" && <WorryPage />}
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
