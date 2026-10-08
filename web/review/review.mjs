// Headless review of web/ against the mock backend; writes the screenshots next to this file. See web/FIXES.md.
//   MOCK_SPEED=3 node cloudflare/dev/mock-backend.mjs
//   BACKEND_URL=http://127.0.0.1:8080 MACRAE_TOOL_SECRET=dev-secret node cloudflare/dev/serve.mjs
//   PORT=8788 BACKEND_URL=http://127.0.0.1:9 MACRAE_TOOL_SECRET=dev-secret node cloudflare/dev/serve.mjs   (backend down)
//   PLAYWRIGHT=/path/to/node_modules/playwright/index.mjs node web/review/review.mjs [bug desktop dark phone offline voice notconfigured
//                                                                                    live evolution planfail v1backend v2dark v2phone]
// OLD_WEB=<dir with the previous app.js, core.js, index.html> adds the "before" half of the bug scenario.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const { chromium, devices } = await import(process.env.PLAYWRIGHT || "playwright");
const BASE = process.env.BASE || "http://127.0.0.1:8787";
const OFF = process.env.OFF_BASE || "http://127.0.0.1:8788";
const OUT = path.dirname(fileURLToPath(import.meta.url));
const OLD = process.env.OLD_WEB || "";
const only = process.argv.slice(2);
const results = [];
const check = (name, ok, extra = "") => { results.push({ name, ok: !!ok, extra }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? `  (${extra})` : ""}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const startRun = async (task = "methods-card", inputs = {}) => (await (await fetch(`${BASE}/api/tasks/${task}/start`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ inputs }) })).json()).run_id;

const browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] });
async function page({ phone = false, dark = false, voiceStub = false, old = false } = {}) {
  const ctx = await browser.newContext({
    ...(phone ? devices["iPhone 13"] : { viewport: { width: 1440, height: 900 } }),
    colorScheme: dark ? "dark" : "light", permissions: ["microphone"],
  });
  const p = await ctx.newPage();
  p.errors = [];
  p.on("pageerror", (e) => p.errors.push(e.message));
  p.on("console", (m) => { if (m.type() === "error") p.errors.push(m.text()); });
  if (old) {
    // the page as it was before this change (files restored from the session log, see FIXES.md)
    for (const f of ["app.js", "core.js", "index.html"]) {
      await p.route(new RegExp(`^${BASE}/${f === "index.html" ? "(\\?.*)?$" : f.replace(".", "\\.")}`), (r) => r.fulfill({ body: fs.readFileSync(`${OLD}/${f}`), contentType: f.endsWith(".js") ? "text/javascript" : "text/html" }));
    }
  }
  if (voiceStub) {
    await p.route("**/voice/signed-url", (r) => r.fulfill({ json: { signed_url: "wss://stub.invalid/convai" } }));
    await p.addInitScript(() => {
      window.__voice = { sent: [], ctx: [] };
      window.ElevenLabsClient = { Conversation: { startSession: async (o) => {
        window.__voice.opts = o;
        setTimeout(() => o.onConnect && o.onConnect(), 30);
        return { endSession: async () => o.onDisconnect && o.onDisconnect({ reason: "user" }), sendUserMessage: (t) => window.__voice.sent.push(t),
          setMicMuted: () => {}, getInputVolume: () => 0.25, getOutputVolume: () => 0.4, sendContextualUpdate: (t) => window.__voice.ctx.push(t) };
      } } };
    });
  }
  return p;
}
const shot = (p, name) => p.screenshot({ path: `${OUT}/${name}.png` });
const text = (p, sel) => p.locator(sel).first().textContent().then((t) => (t || "").trim()).catch(() => "");
// Delay some API routes, as a slow backend (cold container, first rag import) would.
async function slow(p, ms) {
  await p.route(/\/api\/health$/, async (r) => { await sleep(ms); r.continue().catch(() => {}); });
  await p.route(/\/api\/runs\/[^/]+$/, async (r) => { await sleep(ms); r.continue().catch(() => {}); });
}

const S = {};
S.bug = async () => {
  // The run-methods-card.png state: events are there, but /api/health and GET /api/runs/{id} are slow.
  for (const old of OLD ? [true, false] : [false]) {
    const tag = old ? "before" : "after";
    const id = await startRun();
    await sleep(4000); // a few events exist
    const p = await page({ old });
    await slow(p, 15000);
    await p.goto(`${BASE}/?run=${id}`);
    await sleep(3500);
    const title = await text(p, "#run-title");
    const state = await text(p, "#run-state");
    const pill = await text(p, "#health-text");
    const rows = await p.locator("#timeline li").count();
    const stats = await p.locator("#run-stats b").allTextContents();
    const counted = stats.map((x) => parseInt(x, 10) || 0).reduce((a, b) => a + b, 0);
    console.log(`[${tag}] title=${JSON.stringify(title)} state=${state} pill=${pill} rows=${rows} counters=${stats.join("/")}`);
    if (old) {
      check("before: bug reproduced (title stuck while events are shown)", /Loading the run/.test(title) && state === "Starting" && /Checking/.test(pill) && counted > 0, `title=${title}, state=${state}, pill=${pill}`);
    } else {
      check("after: title filled from the task while GET /api/runs/{id} is slow", title === "Methods card for a paper", title);
      check("after: status from the trace", state === "Running" || state === "Done", state);
      check("after: pill online from any API answer while /api/health is slow", /^Online/.test(pill), pill);
      check("after: one timeline row per event", rows > 0 && rows >= counted, `${rows} rows, ${counted} counted`);
      const op = await p.locator("#timeline li").first().evaluate((el) => getComputedStyle(el).opacity + "|" + getComputedStyle(el).animationName);
      check("after: rows are visible without an animation", op.startsWith("1|"), op);
      await shot(p, "bug-after-slow-backend");
      check("after: no page errors", !p.errors.length, p.errors.join(" | "));
    }
    await p.context().close();
  }
};

S.desktop = async () => {
  const p = await page();
  await p.goto(BASE);
  await p.waitForSelector(".task:not(.skeleton)");
  await sleep(300);
  check("hero with 3 chips", (await p.locator("#suggest .chip").count()) === 3);
  check("pill online", /^Online · \d+ papers/.test(await text(p, "#health-text")));
  await shot(p, "01-empty-light");
  // typed question → passages as a Jarvis answer with citations
  await p.locator("#suggest .chip").nth(1).click();
  await p.waitForSelector(".msg.jarvis:not(.pending) .src-chip");
  check("answer has [n] buttons", (await p.locator(".msg.jarvis .text .ref").count()) > 0);
  await p.locator(".msg.jarvis .text .ref").first().click();
  await sleep(500);
  check("[1] opens its source card", (await p.locator(".msg.jarvis .cite").count()) === 1);
  check("source card has a DOI link", /^https:\/\/doi\.org\//.test(await p.locator(".msg.jarvis .cite a.cite-link").first().getAttribute("href")));
  await shot(p, "02-answer-with-sources");
  // start a task from the panel
  await p.locator('form.task[data-task="methods-card"] button[type=submit]').click();
  await p.waitForSelector(".msg.jarvis .run-chip");
  check("starting a task posts a Jarvis message linking to the run", true);
  check("URL is /?run=<id>", /\?run=/.test(p.url()), p.url());
  await sleep(4500);
  check("timeline streaming", (await p.locator("#timeline li").count()) >= 3);
  await shot(p, "03-run-live");
  await p.waitForSelector("#run-end:not([hidden])", { timeout: 30000 });
  await sleep(800);
  check("run done: Done state", (await text(p, "#run-state")) === "Done");
  check("run done: Jarvis posts the result with cited papers", (await p.locator(".msg.jarvis", { hasText: "finished" }).count()) === 1);
  check("run chip says Done", (await text(p, ".run-chip .state")) === "Done");
  await p.locator(".msg.jarvis", { hasText: "finished" }).locator(".src-all").click().catch(() => {});
  await sleep(300);
  await shot(p, "04-run-done");
  // reload the run URL: the same run
  await p.reload();
  await sleep(2500);
  check("reload keeps the conversation", (await p.locator(".msg.user").count()) >= 1 && (await p.locator(".run-chip").count()) === 1);
  check("reload of /?run= shows the run (title)", (await text(p, "#run-title")) === "Methods card for a paper");
  check("reload: status Done", (await text(p, "#run-state")) === "Done");
  check("no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.dark = async () => {
  const p = await page({ dark: true });
  const id = await startRun("small-calc", { ion: "K+" });
  await p.goto(BASE);
  await p.waitForSelector(".task:not(.skeleton)");
  await p.locator("#suggest .chip").first().click();
  await p.waitForSelector(".msg.jarvis:not(.pending) .src-chip");
  await p.locator(".src-chip").first().click();
  await p.goto(`${BASE}/?run=${id}`);
  await sleep(6000);
  await shot(p, "05-dark-run");
  await p.goto(BASE);
  await sleep(800);
  await shot(p, "05-dark-empty");
  check("dark: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.phone = async () => {
  const p = await page({ phone: true });
  await p.goto(BASE);
  await p.waitForSelector(".task:not(.skeleton)", { state: "attached" });
  await sleep(400);
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  check("phone: no sideways scroll", !overflow);
  await shot(p, "06-phone-empty");
  await p.locator("#suggest .chip").first().click();
  await p.waitForSelector(".msg.jarvis:not(.pending)");
  await p.locator(".src-chip").first().click();
  await sleep(300);
  await shot(p, "06-phone-answer");
  await p.locator("#panel-head").click();
  await sleep(500);
  check("phone: sheet opens", await p.evaluate(() => document.getElementById("app").classList.contains("sheet-open")));
  await shot(p, "06-phone-sheet-tasks");
  await p.locator('form.task[data-task="small-calc"] button[type=submit]').click();
  await sleep(6000);
  await shot(p, "06-phone-sheet-run");
  await p.locator("#scrim").click({ position: { x: 20, y: 20 } });
  await sleep(500);
  check("phone: scrim closes the sheet", await p.evaluate(() => !document.getElementById("app").classList.contains("sheet-open")));
  await shot(p, "06-phone-run-message");
  check("phone: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.offline = async () => {
  // 1) backend down from the start (a dead BACKEND_URL)
  let p = await page();
  await p.goto(OFF);
  await sleep(2500);
  check("offline: banner", await p.locator("#offline").isVisible());
  check("offline: pill", (await text(p, "#health-text")) === "Agent offline");
  await p.fill("#input", "How do ions behave at the air/water interface?");
  await p.press("#input", "Enter");
  await sleep(500);
  check("offline: a typed question gets a clear message", /offline/.test(await text(p, ".msg.jarvis.bad")));
  await shot(p, "07-offline-fresh");
  await p.context().close();
  // 2) backend goes down mid-session: tasks stay, greyed out
  p = await page();
  await p.goto(BASE);
  await p.waitForSelector(".task:not(.skeleton)");
  await p.route(/\/api\//, (r) => r.fulfill({ status: 502, json: { error: "agent offline", offline: true } }));
  await p.locator("#health").click();
  await sleep(1200);
  check("offline mid-session: tasks greyed and disabled", (await p.locator(".tasks.off").count()) === 1 && (await p.locator("form.task button[disabled]").count()) >= 2);
  await p.locator("#suggest .chip").nth(2).click();
  await sleep(600);
  await shot(p, "07-offline-mid-session");
  // and back
  await p.unroute(/\/api\//);
  await p.locator("#offline [data-retry]").click();
  await sleep(1500);
  check("back online: banner gone, tasks enabled", !(await p.locator("#offline").isVisible()) && (await p.locator("form.task button[disabled]").count()) === 0);
  await p.context().close();
};

S.voice = async () => {
  const p = await page({ voiceStub: true });
  await p.goto(BASE);
  await p.waitForSelector(".task:not(.skeleton)");
  await p.locator("#mic").click();
  await p.waitForSelector('#composer[data-voice="live"]');
  check("voice: mic starts the session", true);
  const cites = await p.evaluate(async () => (await (await fetch("/api/search", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ query: "ions water interface", k: 3 }) })).json()).passages.map((x) => x.citation));
  await p.evaluate(async (cites) => {
    const o = window.__voice.opts;
    o.onMessage({ source: "user", message: "What do the papers say about ions at the water surface?", event_id: 1 });
    o.onAgentToolRequest({ tool_name: "search_papers" });
    await new Promise((r) => setTimeout(r, 300));
    o.onAgentToolResponse({ tool_name: "search_papers", is_error: false });
    o.clientTools.show_citations({ citations: cites });
    o.onMessage({ source: "ai", message: "Large, soft anions like iodide come to the surface [1], while small hard ions stay in the bulk [2].", response_id: "r1" });
  }, cites);
  await sleep(400);
  check("voice: spoken turns appear as messages", (await p.locator(".msg.user .via").count()) === 1 && (await p.locator(".msg.jarvis .via").count()) === 1);
  check("voice: show_citations attaches sources to Jarvis's answer", (await p.locator(".msg.jarvis .src-chip").count()) >= 2);
  await p.locator(".msg.jarvis .text .ref").nth(1).click();
  await sleep(300);
  await p.fill("#input", "And for calcium?");
  await p.press("#input", "Enter");
  await sleep(200);
  check("voice: typed text goes to the agent during a call", (await p.evaluate(() => window.__voice.sent)).includes("And for calcium?"));
  await shot(p, "08-voice-call");
  // the agent starts a task
  const id = await p.evaluate(async () => (await (await fetch("/api/tasks/small-calc/start", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" })).json()).run_id);
  await p.evaluate((id) => {
    const o = window.__voice.opts;
    o.onAgentToolRequest({ tool_name: "start_task" });
    o.onAgentToolResponse({ tool_name: "start_task", is_error: false, full_tool_result: JSON.stringify({ run_id: id, message: "Started." }) });
  }, id);
  await sleep(5000);
  check("voice: start_task opens the run and posts a message", p.url().includes(id) && (await p.locator(`.run-chip[data-run="${id}"]`).count()) === 1);
  await shot(p, "08-voice-agent-run");
  await p.locator("#mic").click();
  await sleep(300);
  check("voice: mic ends the session", (await p.getAttribute("#composer", "data-voice")) === "idle");
  check("voice: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.notconfigured = async () => {
  const p = await page();
  await p.goto(BASE);
  await p.locator("#mic").click();
  await sleep(2500);
  const st = await text(p, "#voice-status");
  check("voice not configured: clear message", /isn't set up|not configured|Voice/.test(st), st);
  await p.context().close();
};

// ---------- v2: planner, live costs, evolution (web/V2_NOTES.md) ----------
const money = (t) => Number(String(t || "").replace(/[^\d.]/g, "")) || 0;

S.live = async () => {
  const p = await page();
  await p.goto(BASE);
  await p.waitForSelector(".task:not(.skeleton)");
  await p.locator('form.task[data-task="bff-charges"] button[type=submit]').click();
  await p.waitForSelector("#run-plan:not([hidden]) .plan", { timeout: 8000 });
  const order = await p.evaluate(() => [...document.querySelectorAll("#run > *")].filter((el) => !el.hidden).map((el) => el.id || el.className));
  check("v2 live: the plan is the run's first card, above the meter and the trace", order.indexOf("run-plan") >= 0 && order.indexOf("run-plan") < order.indexOf("run-costs") && order.indexOf("run-costs") < order.indexOf("timeline"), order.join(" "));
  check("v2 live: plan headline", /^Decided: GPU A10G, 7 stages, budget \$2\.00$/.test(await text(p, ".plan-head b")), await text(p, ".plan-head b"));
  check("v2 live: plan says why and lists stages and params", (await p.locator(".plan-stages li").count()) === 7 && (await p.locator(".plan-params .kv").count()) >= 5 && /GROMACS/.test(await text(p, ".plan-why")));
  check("v2 live: the first trace row is the plan", (await p.locator("#timeline li").first().getAttribute("class")).includes("ev-plan"));
  await p.waitForSelector(".msg.jarvis .rc-sub");
  check("v2 live: the run chip in the thread shows the decision", /Decided: GPU A10G/.test(await text(p, ".run-chip .rc-sub")));
  await sleep(1500);
  await shot(p, "10-v2-plan-decided");
  const c1 = money(await text(p, ".meter-total b"));
  const t1 = await text(p, ".phase-head b");
  await sleep(6000);
  const c2 = money(await text(p, ".meter-total b"));
  const t2 = await text(p, ".phase-head b");
  check("v2 live: the cost meter grows while the run goes", c2 > c1, `${c1} → ${c2}`);
  check("v2 live: the time grows", t1 !== t2, `${t1} → ${t2}`);
  check("v2 live: tokens shown", /tokens · [\d.]+k in/.test(await text(p, ".meter-tokens")));
  check("v2 live: budget bar", /% of the \$2\.00 budget/.test(await text(p, ".budget-text")));
  check("v2 live: the current phase is marked", (await p.locator(".ph.now").count()) === 1);
  const agentRows = await p.locator("#timeline li .ev-step", { hasText: "fit[0]" }).count();
  const stepState = await text(p, ".step:has-text('fit[0]') .sub");
  check("v2 live: agent tool calls show while the agent step is still running", agentRows >= 3 && /running/.test(stepState), `${agentRows} rows, step ${stepState}`);
  check("v2 live: rows carry their own time and cost", (await p.locator("#timeline .ev-step", { hasText: /\$0\.\d+ · \d+k tok/ }).count()) >= 2);
  await sleep(9000); // past the hold: the trace now follows its newest row, the strip sticks on top
  check("v2 live: the trace follows the newest row", await p.evaluate(() => { const b = document.getElementById("panel-body"); const r = document.querySelector("#timeline li:last-child").getBoundingClientRect(); return r.bottom <= b.getBoundingClientRect().bottom + 2 && b.scrollTop > 0; }));
  check("v2 live: the cost strip sticks while the meter is out of view", await p.locator("#run-strip").isVisible());
  await shot(p, "11-v2-live-trace-costs");
  await p.locator("#cost-steps summary").click().catch(() => {});
  await p.waitForSelector("#run-end:not([hidden])", { timeout: 60000 });
  await sleep(1000);
  check("v2 done: banner has the final cost", /Finished in .* for \$\d/.test(await text(p, "#run-end")), await text(p, "#run-end"));
  check("v2 done: Jarvis says what it cost", /cost \$[\d.]+ \(\$[\d.]+ LLM \+ \$[\d.]+ compute\)/.test(await text(p, ".msg.jarvis:has-text('finished')")));
  check("v2 done: the chip shows the cost", /\$\d/.test(await text(p, ".run-chip .rc-sub")));
  await p.evaluate(() => (document.getElementById("panel-body").scrollTop = 0));
  await p.locator("#cost-steps").evaluate((d) => (d.open = true));
  await sleep(300);
  check("v2 done: per-step table", (await p.locator("table.by-step tbody tr").count()) >= 3);
  await shot(p, "12-v2-run-done-costs");
  await p.waitForSelector(".msg.jarvis:has-text('From that run I learned')", { timeout: 30000 }).catch(() => {});
  check("v2 lessons: Jarvis says what it learned from the run", (await p.locator(".msg.jarvis:has-text('From that run I learned') .evo-chip").count()) === 1);
  await shot(p, "12b-v2-lesson-message");
  await p.locator(".evo-chip").click();
  await p.waitForSelector(".evo-task");
  check("v2 lessons: the link opens Evolution and keeps the run", (await p.getAttribute("#tab-evo", "aria-selected")) === "true" && p.url().includes("?run="));
  check("v2 live: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.evolution = async () => {
  const p = await page();
  await p.goto(`${BASE}/?view=evolution`);
  await p.waitForSelector(".evo-task");
  check("v2 evolution: /?view=evolution opens the tab", (await p.getAttribute("#tab-evo", "aria-selected")) === "true");
  check("v2 evolution: a card per task", (await p.locator(".evo-task").count()) >= 3);
  check("v2 evolution: time, cost and pass rows per task", (await p.locator(".evo-task").first().locator(".evo-row").count()) === 3 && (await p.locator("svg.spark").count()) >= 6);
  check("v2 evolution: improvements shown", (await p.locator(".delta.good").count()) >= 2);
  check("v2 evolution: lessons with evidence links", (await p.locator(".lesson .ev-link").count()) >= 6);
  check("v2 evolution: cards in task order with the tasks' titles", (await p.locator(".evo-task").first().getAttribute("data-task")) === "methods-card" && (await text(p, '.evo-task[data-task="bff-charges"] .task-text b')) === "Bayesian charges for a fragment (BFF)");
  await p.evaluate(() => { const b = document.getElementById("panel-body"); b.scrollTop += document.querySelector('.evo-task[data-task="bff-charges"]').getBoundingClientRect().top - b.getBoundingClientRect().top - 12; });
  await sleep(300);
  await shot(p, "13-v2-evolution");
  const link = p.locator('.evo-task[data-task="bff-charges"] .ev-link').last();
  const seq = await link.getAttribute("data-seq");
  await link.click();
  await p.waitForSelector(`#timeline li[data-seq="${seq}"]`);
  await sleep(700);
  check("v2 evolution: an evidence link opens the run at that event", p.url().includes(`seq=${seq}`) && (await p.getAttribute("#tab-runs", "aria-selected")) === "true");
  check("v2 evolution: the event is flashed and in view", await p.evaluate((seq) => { const li = document.querySelector(`#timeline li[data-seq="${seq}"]`); const b = document.getElementById("panel-body").getBoundingClientRect(); const r = li.getBoundingClientRect(); return li.classList.contains("flash") && r.top >= b.top - 2 && r.bottom <= b.bottom + 2; }, seq));
  await shot(p, "14-v2-lesson-evidence");
  // a sparkline point is a link to its run too
  await p.goBack();
  await sleep(800);
  check("v2 evolution: back returns to the tab", (await p.getAttribute("#tab-evo", "aria-selected")) === "true");
  check("v2 evolution: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.planfail = async () => {
  // The planner's API call failed: the backend says so in the plan event and runs the task's defaults.
  const p = await page();
  await p.route(/\/api\/runs\/[^/]+\/events/, async (r) => {
    const res = await r.fetch();
    const body = await res.json();
    for (const e of body.events) if (e.type === "plan") Object.assign(e, { title: "Planner unavailable, using the task's defaults", detail: "Anthropic API: 529 overloaded.", plan: { ...e.plan, source: "defaults", why: "The planner call failed (Anthropic API: 529 overloaded), so the run uses the task's defaults." }, cost: { usd: 0, tokens: { in: 0, out: 0, cache: 0 } } });
    r.fulfill({ response: res, json: body });
  });
  const id = await startRun("small-calc", { ion: "Ca2+" });
  await p.goto(`${BASE}/?run=${id}`);
  await p.waitForSelector(".plan");
  await sleep(2500);
  check("v2 planner fallback: shown as such", (await p.locator(".plan.fallback").count()) === 1 && /Planner unavailable/.test(await text(p, ".plan-head b")));
  await shot(p, "15-v2-planner-fallback");
  check("v2 planner fallback: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.v1backend = async () => {
  // A backend without the v2 fields (no costs, no plan events, no /api/evolution): the page is the v1 page.
  const p = await page();
  const strip = (e) => { const { cost, elapsed_s, plan, ...rest } = e; return rest; };
  await p.route(/\/api\/runs\/[^/]+\/events/, async (r) => { const res = await r.fetch(); const b = await res.json(); b.events = b.events.filter((e) => e.type !== "plan").map(strip); r.fulfill({ response: res, json: b }); });
  await p.route(/\/api\/runs\/[^/?]+$/, async (r) => { const res = await r.fetch(); const b = await res.json(); delete b.costs; delete b.plan; r.fulfill({ response: res, json: b }); });
  await p.route(/\/api\/evolution$/, (r) => r.fulfill({ status: 404, json: { detail: "Not Found" } }));
  const id = await startRun("methods-card");
  await p.goto(`${BASE}/?run=${id}`);
  await sleep(5000);
  check("v1 backend: no plan card, no meter", (await p.locator("#run-plan").isHidden()) && (await p.locator("#run-costs").isHidden()));
  check("v1 backend: the trace still streams", (await p.locator("#timeline li").count()) >= 2);
  await p.locator("#tab-evo").click();
  await sleep(800);
  check("v1 backend: evolution says it isn't there", /No evolution data/.test(await text(p, "#evo")));
  const errs = p.errors.filter((e) => !/Failed to load resource: .*404/.test(e)); // the expected /api/evolution 404, as logged by the browser
  check("v1 backend: no page errors", !errs.length, errs.join(" | "));
  await p.context().close();
};

S.v2dark = async () => {
  const p = await page({ dark: true });
  const id = await startRun("bff-charges");
  await p.goto(`${BASE}/?run=${id}`);
  await sleep(7000);
  await shot(p, "16-v2-dark-run");
  await p.locator("#tab-evo").click();
  await p.waitForSelector(".evo-task");
  await sleep(400);
  await shot(p, "16-v2-dark-evolution");
  check("v2 dark: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

S.v2phone = async () => {
  const p = await page({ phone: true });
  await p.goto(BASE);
  await p.waitForSelector(".task:not(.skeleton)", { state: "attached" });
  await p.locator("#panel-head").click();
  await sleep(400);
  await p.locator('form.task[data-task="bff-charges"] button[type=submit]').click();
  await sleep(6000);
  check("v2 phone: no sideways scroll", !(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)));
  await shot(p, "17-v2-phone-run");
  await p.locator("#scrim").click({ position: { x: 20, y: 20 } });
  await sleep(1500);
  check("v2 phone: the peek bar shows the run's cost", /\$\d/.test(await text(p, "#panel-peek")), await text(p, "#panel-peek"));
  await shot(p, "17-v2-phone-peek");
  await p.locator("#panel-head").click();
  await sleep(400);
  await p.locator("#tab-evo").click();
  await p.waitForSelector(".evo-task");
  await sleep(400);
  check("v2 phone: evolution fits", !(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)));
  await shot(p, "17-v2-phone-evolution");
  check("v2 phone: no page errors", !p.errors.length, p.errors.join(" | "));
  await p.context().close();
};

for (const [name, fn] of Object.entries(S)) {
  if (only.length && !only.includes(name)) continue;
  console.log(`\n== ${name}`);
  try { await fn(); } catch (e) { check(`${name} crashed`, false, e.message.split("\n")[0]); }
}
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
