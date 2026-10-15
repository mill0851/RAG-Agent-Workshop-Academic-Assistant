// Helpers shared by the three pages. The pages talk to the same server that serves
// them, so every path used here is an API route listed at /docs.

// Call the API; returns parsed JSON, or throws an Error with the server's message.
async function api(path, options = {}) {
  const res = await fetch(path, options);
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = isJson ? body.detail : null;
    throw new Error(typeof detail === "string" ? detail : `${res.status} ${res.statusText}`);
  }
  return body;
}

// Escape text before putting it into HTML.
function esc(value) {
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(value ?? "").replace(/[&<>"']/g, (ch) => map[ch]);
}

// A link that opens in a new tab, only for real web addresses.
function link(url, label) {
  return /^https?:\/\//.test(url || "") ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>` : "";
}

// Download address for a saved item (path as returned by /documents or /search).
function fileUrl(courseId, path) {
  return `/courses/${encodeURIComponent(courseId)}/files?path=${encodeURIComponent(path)}`;
}

// Wait `ms` milliseconds: `await sleep(700)` (the Courses page polls a job's progress).
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
