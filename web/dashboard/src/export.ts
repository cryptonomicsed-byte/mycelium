// Shared export helpers (work package Phase 2 Part 0 #3): every table view
// gets CSV + JSON download and copy-as-curl. Blob + a[download] -- no
// server round-trip, works offline against whatever the view already holds.

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  // Revoke on a delay -- revoking synchronously races the browser actually
  // starting the download in some engines.
  setTimeout(() => URL.revokeObjectURL(url), 5_000);
}

function stamp(): string {
  return new Date().toISOString().slice(0, 10);
}

/** CSV of an array of flat-ish objects. Nested values are JSON-encoded into
 * their cell rather than dropped -- the point of an export is not losing
 * data. Cells are quoted per RFC 4180 (doubled quotes). */
export function downloadCSV(rows: Record<string, unknown>[], name: string) {
  if (!rows.length) return;
  const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  const cell = (v: unknown): string => {
    if (v == null) return "";
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    return `"${s.replace(/"/g, '""')}"`;
  };
  const lines = [cols.map((c) => cell(c)).join(",")];
  for (const r of rows) {
    lines.push(cols.map((c) => cell(r[c])).join(","));
  }
  download(new Blob([lines.join("\n")], { type: "text/csv" }), `${name}-${stamp()}.csv`);
}

export function downloadJSON(data: unknown, name: string) {
  download(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    `${name}-${stamp()}.json`,
  );
}

/** Copies a ready-to-run curl for a gateway API path to the clipboard.
 * Returns false when the clipboard is unavailable (insecure context) so the
 * caller can fall back to showing the command instead. */
export async function copyAsCurl(apiPath: string): Promise<boolean> {
  const cmd = `curl -s ${location.origin}${apiPath}`;
  try {
    await navigator.clipboard.writeText(cmd);
    return true;
  } catch {
    return false;
  }
}
