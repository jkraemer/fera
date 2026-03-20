import { GatewayClient, EventFrame } from "../gateway-client";

type LogEntry = {
  ts: string;
  level: string;
  event: string;
  session: string | null;
  data: Record<string, unknown>;
};

let liveEntries: LogEntry[] = [];
let liveMode = true;
let activeFilter = { level: "", category: "" };
let logEventHandler: ((event: EventFrame) => void) | null = null;
let currentClient: GatewayClient | null = null;

const CATEGORY_PREFIXES: Record<string, string> = {
  system: "system.",
  session: "session.",
  adapter: "adapter.",
  client: "client.",
  turn: "turn.",
  tool: "tool.",
  exception: "exception",
};

function categoryFor(event: string): string {
  for (const [cat, prefix] of Object.entries(CATEGORY_PREFIXES)) {
    if (event.startsWith(prefix) || event === prefix.replace(".", "")) return cat;
  }
  return "other";
}

function levelColor(level: string): string {
  switch (level) {
    case "error": return "text-red-400";
    case "warning": return "text-amber-400";
    default: return "text-gray-300";
  }
}

function categoryColor(event: string): string {
  const cat = categoryFor(event);
  switch (cat) {
    case "adapter":
    case "client": return "text-blue-300";
    case "turn":
    case "session": return "text-purple-300";
    case "tool": return "text-yellow-300";
    default: return "text-gray-400";
  }
}

function formatTs(ts: string): string {
  return ts.slice(11, 23); // HH:MM:SS.mmm
}

function inlineSummary(entry: LogEntry): string {
  const d = entry.data;
  const parts: string[] = [];
  if (entry.session) parts.push(`<span class="text-gray-500">${esc(entry.session)}</span>`);
  if (d.tool_name) parts.push(`<span class="text-yellow-300">${esc(String(d.tool_name))}</span>`);
  if (d.adapter) parts.push(`<span class="text-blue-300">${esc(String(d.adapter))}</span>`);
  if (d.agent) parts.push(`<span class="text-indigo-300">[${esc(String(d.agent))}]</span>`);
  if (d.duration_ms != null) parts.push(`<span class="text-gray-500">${esc(String(d.duration_ms))}ms</span>`);
  if (d.input_tokens != null && d.output_tokens != null) parts.push(`<span class="text-gray-500">${esc(String(d.input_tokens))}in/${esc(String(d.output_tokens))}out tok</span>`);
  if (d.error) parts.push(`<span class="text-red-300">${esc(String(d.error)).slice(0, 80)}</span>`);
  return parts.join(" · ");
}

function renderEntry(entry: LogEntry): string {
  const summary = inlineSummary(entry);
  const dataJson = esc(JSON.stringify(entry.data, null, 2));
  return `
    <details class="border-b border-gray-800 hover:bg-gray-900/50">
      <summary class="flex items-baseline gap-3 px-4 py-1.5 cursor-pointer select-none list-none">
        <span class="font-mono text-xs text-gray-600 w-28 shrink-0">${esc(formatTs(entry.ts))}</span>
        <span class="text-xs font-medium w-36 shrink-0 ${categoryColor(entry.event)}">${esc(entry.event)}</span>
        <span class="text-xs w-14 shrink-0 ${levelColor(entry.level)}">${esc(entry.level)}</span>
        <span class="text-xs text-gray-400 truncate">${summary}</span>
      </summary>
      <pre class="px-6 pb-2 text-xs text-gray-400 overflow-x-auto">${dataJson}</pre>
    </details>
  `;
}

function matchesFilter(entry: LogEntry): boolean {
  if (activeFilter.level && entry.level !== activeFilter.level) return false;
  if (activeFilter.category && categoryFor(entry.event) !== activeFilter.category) return false;
  return true;
}

function renderLog(container: HTMLElement) {
  const visible = liveEntries.filter(matchesFilter);
  if (visible.length === 0) {
    container.innerHTML = `<div class="p-4 text-sm text-gray-600">No entries.</div>`;
    return;
  }
  container.innerHTML = visible.map(renderEntry).join("");
  container.scrollTop = container.scrollHeight;
}

export function initLogs(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement
) {
  currentClient = client;
  liveEntries = [];
  liveMode = true;
  activeFilter = { level: "", category: "" };

  // --- Sidebar ---
  sidebar.innerHTML = `
    <div class="p-2 space-y-3">
      <label class="flex items-center gap-2 text-sm cursor-pointer select-none">
        <input type="checkbox" id="live-toggle" checked class="accent-blue-500" />
        <span class="text-gray-300">Live</span>
      </label>
      <div>
        <label class="block text-xs text-gray-500 mb-1">Date</label>
        <select id="date-picker" class="w-full bg-gray-800 text-sm text-gray-200 rounded px-2 py-1 border border-gray-700">
          <option value="">— select —</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-500 mb-1">Level</label>
        <select id="filter-level" class="w-full bg-gray-800 text-sm text-gray-200 rounded px-2 py-1 border border-gray-700">
          <option value="">All</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-500 mb-1">Category</label>
        <select id="filter-category" class="w-full bg-gray-800 text-sm text-gray-200 rounded px-2 py-1 border border-gray-700">
          <option value="">All</option>
          <option value="system">system</option>
          <option value="session">session</option>
          <option value="adapter">adapter</option>
          <option value="client">client</option>
          <option value="turn">turn</option>
          <option value="tool">tool</option>
          <option value="exception">exception</option>
        </select>
      </div>
    </div>
  `;

  // --- Main content ---
  content.innerHTML = `
    <div id="log-container" class="flex-1 overflow-y-auto font-mono"></div>
  `;
  const container = content.querySelector("#log-container") as HTMLElement;

  renderLog(container);

  // Live toggle
  const liveToggle = sidebar.querySelector("#live-toggle") as HTMLInputElement;
  liveToggle.addEventListener("change", () => {
    liveMode = liveToggle.checked;
  });

  async function loadDateEntries(date: string) {
    const res = await client.request("logs.read", { date }) as { entries: LogEntry[] };
    liveEntries = res.entries;
    renderLog(container);
  }

  // Date picker — populate available dates and auto-load most recent
  const datePicker = sidebar.querySelector("#date-picker") as HTMLSelectElement;
  client.request("logs.list").then(async (res) => {
    const payload = res as { dates: string[] };
    for (const d of payload.dates.slice().reverse()) {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      datePicker.appendChild(opt);
    }
    if (payload.dates.length > 0) {
      const mostRecent = payload.dates[payload.dates.length - 1];
      datePicker.value = mostRecent;
      await loadDateEntries(mostRecent);
      // Keep live mode on so new events continue to append
    }
  }).catch((err) => {
    console.error("logs.list failed:", err);
  });

  datePicker.addEventListener("change", async () => {
    const date = datePicker.value;
    if (!date) return;
    liveToggle.checked = false;
    liveMode = false;
    await loadDateEntries(date);
  });

  // Filters
  const filterLevel = sidebar.querySelector("#filter-level") as HTMLSelectElement;
  const filterCategory = sidebar.querySelector("#filter-category") as HTMLSelectElement;
  filterLevel.addEventListener("change", () => {
    activeFilter.level = filterLevel.value;
    renderLog(container);
  });
  filterCategory.addEventListener("change", () => {
    activeFilter.category = filterCategory.value;
    renderLog(container);
  });

  // Live streaming
  logEventHandler = (event: EventFrame) => {
    if (event.event !== "log.entry") return;
    if (!liveMode) return;
    const entry = event.data as unknown as LogEntry;
    liveEntries.push(entry);
    // Keep max 2000 entries in memory
    if (liveEntries.length > 2000) liveEntries.shift();
    if (matchesFilter(entry)) {
      const div = document.createElement("div");
      div.innerHTML = renderEntry(entry);
      container.appendChild(div.firstElementChild!);
      container.scrollTop = container.scrollHeight;
    }
  };
  client.onEvent(logEventHandler);
}

export function destroyLogs(client: GatewayClient): void {
  if (logEventHandler) {
    client.offEvent(logEventHandler);
    logEventHandler = null;
  }
  currentClient = null;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
