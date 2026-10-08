// node:module register() hook: resolve @cloudflare/containers to the fake next to this file.
export async function resolve(specifier, context, next) {
  if (specifier === "@cloudflare/containers") return { url: new URL("./containers.mjs", import.meta.url).href, shortCircuit: true };
  return next(specifier, context);
}
