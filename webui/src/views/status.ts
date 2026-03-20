import { GatewayClient } from "../gateway-client";

let refreshTimer: ReturnType<typeof setInterval> | null = null;
let currentClient: GatewayClient | null = null;

type Summary = {
  uptime_seconds: number;
  started_at: number;
  active_sessions: number;
  turns_today: number;
  tokens_today: { input: number; output: number };
  cost_today_usd: number;
  adapters: Record<string, string>;
};

type MetricPoint = { ts: number; value: number };
type MetricsResponse = Record<string, MetricPoint[]>;

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function sparklineSvg(points: MetricPoint[], width = 200, height = 40): string {
  if (points.length === 0) {
    return `<svg width="${width}" height="${height}" class="inline-block"><text x="${width / 2}" y="${height / 2}" text-anchor="middle" fill="#71717a" font-size="11">No data</text></svg>`;
  }
  const maxVal = Math.max(...points.map((p) => p.value), 1);
  const step = width / Math.max(points.length - 1, 1);
  const coords = points
    .map((p, i) => `${(i * step).toFixed(1)},${(height - (p.value / maxVal) * (height - 4) - 2).toFixed(1)}`)
    .join(" ");
  return `<svg width="${width}" height="${height}" class="inline-block">
    <polyline points="${coords}" fill="none" stroke="#f97316" stroke-width="1.5" stroke-linejoin="round"/>
  </svg>`;
}

function renderCards(summary: Summary): string {
  const adapterHtml = Object.entries(summary.adapters)
    .map(([name, status]) => {
      const dot = status === "running"
        ? '<span class="inline-block w-2 h-2 rounded-full bg-green-500 mr-1.5"></span>'
        : '<span class="inline-block w-2 h-2 rounded-full bg-red-500 mr-1.5"></span>';
      return `<span class="flex items-center text-sm text-zinc-300">${dot}${name}</span>`;
    })
    .join("");

  return `
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
      <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <div class="text-xs text-zinc-500 uppercase tracking-wide mb-1">Uptime</div>
        <div class="text-2xl font-bold text-zinc-100">${formatUptime(summary.uptime_seconds)}</div>
      </div>
      <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <div class="text-xs text-zinc-500 uppercase tracking-wide mb-1">Sessions</div>
        <div class="text-2xl font-bold text-zinc-100">${summary.active_sessions}</div>
      </div>
      <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <div class="text-xs text-zinc-500 uppercase tracking-wide mb-1">Turns Today</div>
        <div class="text-2xl font-bold text-zinc-100">${summary.turns_today}</div>
      </div>
      <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
        <div class="text-xs text-zinc-500 uppercase tracking-wide mb-1">Cost Today</div>
        <div class="text-2xl font-bold text-zinc-100">$${summary.cost_today_usd.toFixed(2)}</div>
      </div>
    </div>
    <div class="mb-8">
      <div class="text-xs text-zinc-500 uppercase tracking-wide mb-2">Tokens Today</div>
      <div class="flex gap-6 text-sm text-zinc-300">
        <span>In: <strong class="text-zinc-100">${formatTokens(summary.tokens_today.input)}</strong></span>
        <span>Out: <strong class="text-zinc-100">${formatTokens(summary.tokens_today.output)}</strong></span>
      </div>
    </div>
    <div class="mb-8">
      <div class="text-xs text-zinc-500 uppercase tracking-wide mb-2">Adapters</div>
      <div class="flex gap-4">${adapterHtml || '<span class="text-sm text-zinc-500">None</span>'}</div>
    </div>
  `;
}

function renderSparklines(metrics: MetricsResponse): string {
  const labels: Record<string, string> = {
    turn: "Turns / hr",
    tokens_in: "Input tokens / hr",
    tokens_out: "Output tokens / hr",
    cost: "Cost / hr",
  };
  return Object.entries(labels)
    .map(([key, label]) => {
      const points = metrics[key] || [];
      return `
        <div class="mb-6">
          <div class="text-xs text-zinc-500 uppercase tracking-wide mb-1">${label}</div>
          ${sparklineSvg(points, 400, 48)}
        </div>
      `;
    })
    .join("");
}

async function loadAndRender(client: GatewayClient, content: HTMLElement): Promise<void> {
  try {
    const [summary, metrics] = await Promise.all([
      client.request("status.summary") as Promise<Summary>,
      client.request("status.metrics", {
        metrics: ["turn", "tokens_in", "tokens_out", "cost"],
        range: "24h",
        bucket: "1h",
      }) as Promise<MetricsResponse>,
    ]);
    content.innerHTML = `
      <div class="p-6 max-w-4xl">
        <h2 class="text-lg font-bold text-zinc-100 mb-6">System Status</h2>
        ${renderCards(summary)}
        <h3 class="text-sm font-semibold text-zinc-300 mb-4">Last 24 Hours</h3>
        ${renderSparklines(metrics)}
        <div class="text-xs text-zinc-600 mt-4">Auto-refreshes every 60s</div>
      </div>
    `;
  } catch (e) {
    content.innerHTML = `<div class="p-6 text-red-400">Failed to load status: ${e}</div>`;
  }
}

export function initStatus(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement,
): void {
  currentClient = client;
  sidebar.innerHTML = "";
  content.innerHTML = `<div class="p-6 text-zinc-400">Loading status...</div>`;
  loadAndRender(client, content);
  refreshTimer = setInterval(() => loadAndRender(client, content), 60_000);
}

export function destroyStatus(): void {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  currentClient = null;
}
