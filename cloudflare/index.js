// Entry point of the "macrae" Worker on Cloudflare: the request handling in worker.js, plus the backend container.
//
// MacraeBackend is a Durable Object that owns one container built from ../deploy/Dockerfile (FastAPI on :8080).
// worker.js sends /api/* to the single instance "macrae-backend" unless BACKEND_URL overrides it.
//   - envVars: the Worker secrets the backend needs (containerEnv in worker.js), read when the container starts.
//   - sleepAfter "2h": no requests for 2 h → the container stops (runs, traces and the index are in R2). If task
//     runs are still going at that point it stays up and checks again 2 h later, so a flow is never cut off.
//   - http://r2.macrae/* from inside the container is answered by dataHandler next to the DATA (R2) binding.
//     The container never holds R2 credentials. ContainerProxy must be exported for this interception to work.
import { Container, ContainerProxy } from "@cloudflare/containers";
import worker, { containerEnv, countRunning, dataHandler, DATA_HOST } from "./worker.js";

export { ContainerProxy };

export class MacraeBackend extends Container {
  defaultPort = 8080; // deploy/start.py → uvicorn on $PORT (8080 in deploy/Dockerfile)
  sleepAfter = "2h";
  enableInternet = true; // Modal, Anthropic, OpenAlex

  constructor(ctx, env) {
    super(ctx, env);
    this.envVars = containerEnv(env);
  }

  // A cold start pulls a large image (Python, fastembed, Harbor), so give it more than the library's 8 s + 20 s
  // before answering "not ready" (worker.js turns that into the page's offline state, which retries).
  async fetch(request) {
    if (!this.ctx.container.running || (await this.getState()).status !== "healthy") {
      try {
        await this.startAndWaitForPorts({
          ports: this.defaultPort,
          cancellationOptions: { instanceGetTimeoutMS: 30_000, portReadyTimeoutMS: 60_000 },
        });
      } catch (err) {
        return new Response(`Failed to start container: ${err && err.message ? err.message : err}`, { status: 503 });
      }
    }
    return this.containerFetch(request, this.defaultPort);
  }

  async onActivityExpired() {
    if (!this.ctx.container.running) return;
    const running = await this.runningTasks();
    if (running > 0) {
      // Not stopping renews the timer: this hook runs again after another sleepAfter.
      console.log(`no requests for ${this.sleepAfter}, but ${running} task run(s) still going: keeping the backend up`);
      return;
    }
    console.log(`no requests for ${this.sleepAfter} and no task runs: stopping the backend container`);
    await this.stop(); // SIGTERM: start.py saves state to R2, then exits
  }

  async runningTasks() {
    try {
      const res = await this.containerFetch("http://backend/api/runs", {
        headers: { "X-Macrae-Secret": this.env.MACRAE_TOOL_SECRET || "" },
      });
      return res.ok ? countRunning(await res.json()) : 0;
    } catch (err) {
      console.error("could not ask the backend for its runs", err && err.message ? err.message : err);
      return 0;
    }
  }

  // RPC from worker.js /admin/restart.
  async restart() {
    const wasRunning = this.ctx.container.running;
    if (wasRunning) await this.stop();
    return { ok: true, was_running: wasRunning, note: wasRunning ? "stopping gracefully; the next request starts it again" : "was not running" };
  }
}

MacraeBackend.outboundByHost = {
  [DATA_HOST]: (request, env) => dataHandler(request, env),
};

export default worker;
