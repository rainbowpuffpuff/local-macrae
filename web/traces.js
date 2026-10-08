// The run's downloadable proof (server/traces.py): the whole trace as a zip, and a self-contained HTML report.
// Plain links to the backend through the Worker (which adds the secret), so the browser downloads or opens them itself.
export function traceLinks(runId) {
  const id = String(runId || "").trim();
  if (!id) return null;
  const p = `/api/runs/${encodeURIComponent(id)}`;
  return { zip: `${p}/trace.zip`, zipName: `macrae-trace-${id.replace(/[^\w.-]+/g, "-")}.zip`, report: `${p}/report.html` };
}

// Point the run view's two buttons at this run (or hide them when there is none).
export function setTraceLinks(box, runId) {
  const l = traceLinks(runId);
  box.hidden = !l;
  if (!l) return;
  const zip = box.querySelector("[data-trace=zip]");
  const rep = box.querySelector("[data-trace=report]");
  zip.href = l.zip;
  zip.setAttribute("download", l.zipName);
  rep.href = l.report;
}
