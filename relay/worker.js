// Cloudflare Worker: relay for the fantasy tracker.
//
// BasketNews refuses requests from GitHub's servers, so the tracker's scheduled job sends
// its BasketNews requests here and this Worker forwards them from Cloudflare's network.
// Only the BasketNews addresses the tracker uses are allowed, so this is not an open proxy.
//
//   GET  /                      -> short status text (to check the deploy)
//   ANY  /?url=<BasketNews URL> -> that URL's answer (method, body and main headers passed on)
//
// Optional: add a secret RELAY_KEY in the Worker's settings; requests must then send it in
// the "x-relay-key" header.

const ALLOWED = [
  /^https:\/\/fantasy\.basketnews\.com\/backend\/graphql$/,
  /^https:\/\/basketnews\.com\/advanced-stats\/team-profile\/(players|overview)\.json$/,
  /^https:\/\/(www\.)?basketnews\.(com|lt)\/[\w./%-]+\.html$/, // injury reports, player pages
];

const PASS_HEADERS = ["content-type", "accept", "accept-language", "user-agent", "x-requested-with", "referer", "origin"];

export default {
  async fetch(request, env) {
    if (env.RELAY_KEY && request.headers.get("x-relay-key") !== env.RELAY_KEY) {
      return new Response("forbidden", { status: 403 });
    }
    const target = new URL(request.url).searchParams.get("url");
    if (!target) {
      return new Response("fantasy-tracker relay is running\n", { headers: { "content-type": "text/plain" } });
    }
    if (!ALLOWED.some((re) => re.test(target))) {
      return new Response("address not allowed\n", { status: 400 });
    }
    const headers = new Headers();
    for (const name of PASS_HEADERS) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    const hasBody = !["GET", "HEAD"].includes(request.method);
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "follow",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") || "application/octet-stream" },
    });
  },
};
