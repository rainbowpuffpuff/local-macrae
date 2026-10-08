// Stand-in for @cloudflare/containers (which needs the Workers runtime): just what index.js uses.
export class Container {
  envVars = {};
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.stops = 0;
  }
  async stop() {
    this.stops++;
    this.ctx.container.running = false;
  }
  async containerFetch(url, init) {
    if (url instanceof Request) return this.ctx.containerFetch(url.url, { method: url.method });
    return this.ctx.containerFetch(String(url), init);
  }
  async getState() {
    return { status: this.ctx.container.healthy ? "healthy" : "stopped" };
  }
  async startAndWaitForPorts(args) {
    this.startArgs = args;
    if (this.ctx.failStart) throw new Error(this.ctx.failStart);
    this.ctx.container.running = true;
    this.ctx.container.healthy = true;
  }
}
export class ContainerProxy {}
