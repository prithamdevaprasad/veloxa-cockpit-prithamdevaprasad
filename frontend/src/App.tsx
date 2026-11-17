import { useCallback, useEffect, useState } from "react";
import { getToken, setToken } from "./api/client";
import { AgentsPage } from "./pages/Agents";
import { AskPage } from "./pages/Ask";
import { ConflictsPage } from "./pages/Conflicts";
import { DepartmentsPage } from "./pages/Departments";
import { ScenariosPage } from "./pages/Scenarios";
import { TodayPage } from "./pages/Today";
import { Button, PageHead, Section } from "./ui";

const ROUTES = { today: "Today", ask: "Ask", conflicts: "Conflicts", agents: "Agents", departments: "Departments",
                 scenarios: "Scenarios", access: "Access" } as const;
type Route = keyof typeof ROUTES;

function current(): Route {
  const r = window.location.hash.replace(/^#\/?/, "") as Route;
  return r in ROUTES ? r : "today";
}

function AccessPage({ onChange }: { onChange: () => void }) {
  const [value, setValue] = useState("");
  const signedIn = Boolean(getToken());
  return (
    <>
      <PageHead title="Access" intro="Veloxa needs an authenticated principal. No identity provider is integrated yet,
        so locally you paste the bearer token of the development fixture. It is kept for this tab only." />
      <Section title={signedIn ? "Signed in" : "Not signed in"}>
        <form className="row" onSubmit={(e) => { e.preventDefault(); setToken(value.trim() || null); setValue(""); onChange(); }}>
          <input className="input" type="password" autoComplete="off" value={value} style={{ flex: 1, minWidth: 220 }}
                 onChange={(e) => setValue(e.target.value)} aria-label="Bearer token" placeholder="Bearer token" />
          <Button variant="primary" type="submit" disabled={!value.trim()}>Use token</Button>
          {signedIn && <Button type="button" onClick={() => { setToken(null); onChange(); }}>Sign out</Button>}
        </form>
      </Section>
    </>
  );
}

export function App() {
  const [route, setRoute] = useState<Route>(current());
  const [askSeed, setAskSeed] = useState<string | undefined>();
  const [authTick, setAuthTick] = useState(0);
  useEffect(() => {
    const on = () => setRoute(current());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = (r: Route) => { window.location.hash = `/${r}`; };
  const consumed = useCallback(() => setAskSeed(undefined), []);

  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <div className="brand">Veloxa<small>CXO Cockpit</small></div>
        {(Object.keys(ROUTES) as Route[]).filter((r) => r !== "access").map((r) => (
          <button key={r} aria-current={route === r ? "page" : undefined} onClick={() => go(r)}>{ROUTES[r]}</button>
        ))}
        <div className="spacer" />
        <button aria-current={route === "access" ? "page" : undefined} onClick={() => go("access")}>
          {getToken() ? "Access" : "Sign in"}
        </button>
      </nav>
      <main className="main" key={authTick}>
        {route === "today" && <TodayPage onAsk={(q) => { setAskSeed(q); go("ask"); }} />}
        {route === "ask" && <AskPage initial={askSeed} onConsumed={consumed} />}
        {route === "conflicts" && <ConflictsPage />}
        {route === "agents" && <AgentsPage />}
        {route === "departments" && <DepartmentsPage />}
        {route === "scenarios" && <ScenariosPage />}
        {route === "access" && <AccessPage onChange={() => setAuthTick((t) => t + 1)} />}
      </main>
    </div>
  );
}
