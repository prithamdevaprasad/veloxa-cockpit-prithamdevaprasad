// Cloudflare Worker for the hosted Veloxa frontend: serves the built app and forwards /v1/* to the Cloud Run API,
// so the browser sees ONE origin (no cross-site requests, cookies and OAuth callbacks work unchanged).
// API_ORIGIN is set in wrangler.jsonc. Nothing is cached or logged here; responses pass through as-is.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/v1/") || url.pathname === "/health") {
      const target = new URL(url.pathname + url.search, env.API_ORIGIN);
      const headers = new Headers(request.headers);
      headers.set("X-Forwarded-Host", url.host);
      headers.set("X-Forwarded-Proto", "https");
      const ip = request.headers.get("CF-Connecting-IP");
      if (ip) headers.set("X-Forwarded-For", ip);
      return fetch(target, { method: request.method, headers, body: request.body, redirect: "manual" });
    }
    return env.ASSETS.fetch(request);
  },
};
