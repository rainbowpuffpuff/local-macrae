// Applies the saved light/dark choice before first paint (a separate file because the CSP allows no inline script).
try {
  const t = localStorage.getItem("macrae-theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch {}
