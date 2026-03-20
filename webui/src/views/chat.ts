import { GatewayClient, EventFrame } from "../gateway-client";

type SessionStats = {
  turns?: number;
  context_pct?: number | null;
  model?: string | null;
  total_cost_usd?: number;
  compactions?: number;
};

type Session = { id: string; name: string; agent?: string; stats?: SessionStats; pooled?: boolean };
type Message =
  | { type: "user"; text: string; source: string; ts?: string }
  | { type: "text"; text: string; html?: string; ts?: string }
  | { type: "tool_use"; name: string; input: unknown }
  | { type: "tool_result"; content: unknown; is_error: boolean }
  | { type: "error"; error: string }
  | { type: "done" };

type HistoryEntry = { type: string; ts: string; [key: string]: unknown };

export function historyEntryToMessage(entry: HistoryEntry): Message {
  switch (entry.type) {
    case "user":
      return { type: "user", text: entry.text as string, source: entry.source as string, ...(entry.ts && { ts: entry.ts }) };
    case "agent":
      return { type: "text", text: entry.text as string, html: entry.html as string | undefined, ...(entry.ts && { ts: entry.ts }) };
    case "tool_use":
      return { type: "tool_use", name: entry.name as string, input: entry.input };
    case "tool_result":
      return { type: "tool_result", content: entry.content, is_error: entry.is_error as boolean };
    case "done":
      return { type: "done" };
    case "error":
      return { type: "error", error: entry.error as string };
    default:
      console.warn("Unknown history entry type:", entry.type);
      return { type: "done" };
  }
}

const messages = new Map<string, Message[]>();
const historyLoaded = new Set<string>();
let activeSession = "";
let eventHandler: ((event: EventFrame) => void) | null = null;
let gatewayClient: GatewayClient | null = null;
let sessions: Session[] = [];
let sidebarElement: HTMLElement | null = null;
let knownAgents: string[] = ["main"];

export function initChat(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement,
  initialSessions: Session[],
  agents: string[] = ["main"]
) {
  gatewayClient = client;
  sidebarElement = sidebar;
  sessions = initialSessions;
  knownAgents = agents.length > 0 ? agents : ["main"];

  // Initialize session message buffers
  for (const s of sessions) {
    if (!messages.has(s.id)) messages.set(s.id, []);
  }

  // Event handler — accumulate messages per session
  eventHandler = (event: EventFrame) => {
    const session = event.session;
    if (session === "$system") return;
    if (!messages.has(session)) messages.set(session, []);
    const msgs = messages.get(session)!;

    switch (event.event) {
      case "user.message":
        msgs.push({ type: "user", text: event.data.text as string, source: event.data.source as string, ts: new Date().toISOString() });
        break;
      case "agent.text":
        msgs.push({
          type: "text",
          text: event.data.text as string,
          html: event.data.html as string | undefined,
          ts: new Date().toISOString(),
        });
        break;
      case "agent.tool_use":
        msgs.push({
          type: "tool_use",
          name: event.data.name as string,
          input: event.data.input,
        });
        break;
      case "agent.tool_result":
        msgs.push({
          type: "tool_result",
          content: event.data.content,
          is_error: event.data.is_error as boolean,
        });
        break;
      case "agent.done": {
        msgs.push({ type: "done" });
        // Update session stats from the event data
        const sess = sessions.find(s => s.id === session);
        if (sess) {
          if (!sess.stats) sess.stats = {};
          const d = event.data;
          sess.stats.turns = (sess.stats.turns || 0) + 1;
          if (d.model) sess.stats.model = d.model as string;
          // Compute context_pct from token fields
          const inputT = (d.input_tokens as number) || 0;
          const cacheCreation = (d.cache_creation_input_tokens as number) || 0;
          const cacheRead = (d.cache_read_input_tokens as number) || 0;
          const contextUsed = inputT + cacheCreation + cacheRead;
          if (contextUsed > 0) {
            sess.stats.context_pct = Math.round(contextUsed / 200000 * 1000) / 10;
          }
          if (d.cost_usd) {
            sess.stats.total_cost_usd = (sess.stats.total_cost_usd || 0) + (d.cost_usd as number);
          }
        }
        // Re-render sidebar to update stats display
        if (sidebarElement && gatewayClient) renderSidebar(gatewayClient, sidebarElement);
        break;
      }
      case "agent.error":
        msgs.push({ type: "error", error: event.data.error as string });
        break;
    }

    if (session === activeSession) renderMessages(content);
  };
  client.onEvent(eventHandler);

  renderSidebar(client, sidebar);
  if (sessions.length > 0) {
    switchSession(sessions[0].id, content);
  }
  renderContent(client, content);
}

function renderSidebar(client: GatewayClient, sidebar: HTMLElement) {
  sidebar.innerHTML = "";

  // New session button
  const btn = document.createElement("button");
  btn.className = "w-full p-2 text-sm text-zinc-400 hover:text-white border-b border-zinc-800";
  btn.textContent = "+ New session";
  btn.onclick = () => {
    // Remove any existing modal
    document.getElementById("new-session-modal")?.remove();

    const overlay = document.createElement("div");
    overlay.id = "new-session-modal";
    overlay.className = "fixed inset-0 bg-black/50 flex items-center justify-center z-50";
    overlay.innerHTML = `
      <div class="w-72 bg-zinc-900 border border-zinc-700 rounded-lg p-5">
        <h2 class="text-sm font-semibold mb-4">New Session</h2>
        <form id="new-session-form">
          <label class="block text-xs text-zinc-400 mb-1">Name</label>
          <input name="session-name" type="text" autofocus
            class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm mb-3 outline-none focus:border-orange-500"
            placeholder="e.g. work" required />
          <label class="block text-xs text-zinc-400 mb-1">Agent</label>
          <select name="agent"
            class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-1.5 text-sm mb-4 outline-none">
            ${knownAgents.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join("")}
          </select>
          <div class="flex gap-2 justify-end">
            <button type="button" id="cancel-new-session"
              class="text-sm text-zinc-400 hover:text-white px-3 py-1.5">Cancel</button>
            <button type="submit"
              class="bg-orange-600 hover:bg-orange-500 text-white text-sm rounded px-3 py-1.5">Create</button>
          </div>
        </form>
      </div>
    `;
    document.body.appendChild(overlay);

    const form = document.getElementById("new-session-form")!;
    document.getElementById("cancel-new-session")!.onclick = () => overlay.remove();
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") overlay.remove(); });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const nameInput = (form as HTMLFormElement).elements.namedItem("session-name") as HTMLInputElement;
      const agentSelect = (form as HTMLFormElement).elements.namedItem("agent") as HTMLSelectElement;
      const name = nameInput.value.trim();
      const agent = agentSelect.value;
      if (!name || !gatewayClient) return;
      overlay.remove();
      const result = await gatewayClient.request("session.create", { name, agent }) as {
        id: string; name: string; agent: string;
      };
      messages.set(result.id, []);
      sessions.push({ id: result.id, name: result.name, agent: result.agent });
      if (sidebarElement && gatewayClient) renderSidebar(gatewayClient, sidebarElement);
    });
  };
  sidebar.appendChild(btn);

  // Group sessions by agent
  const byAgent = new Map<string, string[]>(); // agent -> sessionIds
  for (const sessionId of messages.keys()) {
    const agent = sessions.find(s => s.id === sessionId)?.agent ?? "main";
    if (!byAgent.has(agent)) byAgent.set(agent, []);
    byAgent.get(agent)!.push(sessionId);
  }

  for (const [agent, sessionIds] of [...byAgent.entries()].sort(([a], [b]) => {
    if (a === "main") return -1;
    if (b === "main") return 1;
    return a.localeCompare(b);
  })) {
    const header = document.createElement("div");
    header.className = "px-2 pt-3 pb-1 text-xs text-zinc-500 uppercase tracking-wide";
    header.dataset.agentHeader = agent;
    header.textContent = agent;
    sidebar.appendChild(header);

    for (const sessionId of sessionIds) {
      const session = sessions.find(s => s.id === sessionId);
      const displayName = session?.name ?? sessionId;
      const stats = session?.stats;
      const pooled = session?.pooled !== false; // default to true if missing
      const isActive = sessionId === activeSession;

      const item = document.createElement("div");
      item.className = `group relative px-2 py-1.5 text-sm cursor-pointer rounded ${
        isActive
          ? pooled ? "bg-zinc-700 text-white" : "bg-zinc-700 text-zinc-600"
          : pooled ? "text-zinc-400 hover:text-white" : "text-zinc-600"
      }`;
      item.dataset.sessionId = sessionId;

      let statsLine = "";
      if (stats && stats.turns && stats.turns > 0) {
        const parts: string[] = [];
        if (stats.context_pct != null) {
          const remaining = Math.round(100 - stats.context_pct);
          parts.push(`${remaining}% ctx`);
        }
        if (stats.model) {
          const short = stats.model.replace(/^claude-/, "").replace(/-\d.*$/, "");
          parts.push(short);
        }
        parts.push(`${stats.turns} turn${stats.turns !== 1 ? "s" : ""}`);
        statsLine = `<div class="text-xs ${pooled ? "text-zinc-500" : "text-zinc-700"} mt-0.5">${esc(parts.join(" \u00b7 "))}</div>`;
      }

      const actions = `<span class="absolute right-1 top-1 opacity-0 group-hover:opacity-100 flex gap-1">
        <button data-action="deactivate" data-session="${esc(sessionId)}" title="Deactivate"
          class="text-zinc-500 hover:text-amber-400 text-xs px-1">\u23F8</button>
        <button data-action="delete" data-session="${esc(sessionId)}" title="Delete"
          class="text-zinc-500 hover:text-red-400 text-xs px-1">\u00D7</button>
      </span>`;

      item.innerHTML = `<span class="truncate">${esc(displayName)}</span>${statsLine}${actions}`;

      item.onclick = (e) => {
        // Don't switch session if clicking an action button
        if ((e.target as HTMLElement).closest("[data-action]")) return;
        switchSession(sessionId, document.getElementById("content")!);
        renderSidebar(client, sidebar);
      };

      // Deactivate handler
      item.querySelector("[data-action='deactivate']")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        await client.request("session.deactivate", { session: sessionId });
        // Refresh session list to get updated pooled flags
        const result = await client.request("session.list") as { sessions: Session[] };
        sessions.length = 0;
        sessions.push(...result.sessions);
        for (const s of sessions) {
          if (!messages.has(s.id)) messages.set(s.id, []);
        }
        renderSidebar(client, sidebar);
      });

      // Delete handler
      item.querySelector("[data-action='delete']")?.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete session "${displayName}"?`)) return;
        await client.request("session.delete", { session: sessionId });
        const idx = sessions.findIndex(s => s.id === sessionId);
        if (idx >= 0) sessions.splice(idx, 1);
        messages.delete(sessionId);
        historyLoaded.delete(sessionId);
        if (activeSession === sessionId) {
          activeSession = sessions.length > 0 ? sessions[0].id : "";
          renderMessages(document.getElementById("content")!);
        }
        renderSidebar(client, sidebar);
      });

      sidebar.appendChild(item);
    }
  }
}

function switchSession(name: string, content: HTMLElement) {
  activeSession = name;
  renderMessages(content);

  if (!historyLoaded.has(name) && gatewayClient) {
    gatewayClient.request("session.history", { session: name })
      .then((result) => {
        historyLoaded.add(name);
        const history = ((result as any).messages ?? []) as HistoryEntry[];
        const existing = messages.get(name) || [];
        messages.set(name, [...history.map(historyEntryToMessage), ...existing]);
        if (activeSession === name) renderMessages(content);
      })
      .catch((e) => console.warn("Failed to load session history:", e));
  }
}

function renderContent(client: GatewayClient, content: HTMLElement) {
  // Input bar at the bottom
  const input = content.querySelector("#chat-input") as HTMLFormElement | null;
  if (input) return; // already rendered

  const form = document.createElement("form");
  form.id = "chat-input";
  form.className = "p-4 border-t border-zinc-800 flex gap-2";
  form.innerHTML = `
    <textarea name="text" rows="3" placeholder="Send a message..."
      class="flex-1 bg-zinc-800 text-white rounded px-3 py-2 outline-none focus:ring-1 focus:border-orange-500 resize-none overflow-hidden"></textarea>
    <button type="submit" class="bg-orange-600 hover:bg-orange-500 text-white px-4 py-2 rounded self-end">Send</button>
  `;
  const textarea = form.elements.namedItem("text") as HTMLTextAreaElement;
  const autoResize = () => {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  };
  textarea.addEventListener("input", autoResize);
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  form.onsubmit = async (e) => {
    e.preventDefault();
    const text = textarea.value.trim();
    if (!text || !activeSession) return;
    textarea.value = "";
    autoResize();
    await client.request("chat.send", { text, session: activeSession });
  };
  content.appendChild(form);
}

function renderMessages(content: HTMLElement) {
  let log = content.querySelector("#chat-log") as HTMLElement | null;
  if (!log) {
    log = document.createElement("div");
    log.id = "chat-log";
    log.className = "flex-1 overflow-y-auto p-4 space-y-2 flex flex-col";
    content.insertBefore(log, content.firstChild);
  }

  const msgs = messages.get(activeSession) || [];
  log.innerHTML = msgs
    .map((m) => {
      switch (m.type) {
        case "user": {
          const label = !m.source || m.source === "web" ? "You" : `[${m.source.charAt(0).toUpperCase() + m.source.slice(1)}]`;
          const ts = m.ts ? `<div class="text-xs text-zinc-500 mt-1 text-right" data-message-ts>${fmtTime(m.ts)}</div>` : "";
          return `<div class="flex justify-end">
            <div data-message-type="user" class="bg-zinc-900 text-zinc-100 border border-orange-500 rounded-2xl rounded-br-sm px-4 py-2 max-w-[75%] whitespace-pre-wrap">
              <span class="text-xs text-orange-400 block mb-1">${esc(label)}</span>${esc(m.text)}${ts}
            </div>
          </div>`;
        }
        case "text": {
          // html is server-rendered markdown — not user-controlled, assumed safe
          const body = m.html ? `<div class="prose">${m.html}</div>` : `<div class="whitespace-pre-wrap">${esc(m.text)}</div>`;
          const ts = m.ts ? `<div class="text-xs text-zinc-600 mt-1 text-right" data-message-ts>${fmtTime(m.ts)}</div>` : "";
          return `<div class="flex justify-start">
            <div data-message-type="agent" class="bg-zinc-800 rounded-2xl rounded-bl-sm px-4 py-2 max-w-[75%]">${body}${ts}</div>
          </div>`;
        }
        case "tool_use":
          return `<div class="flex justify-start">
            <details class="bg-zinc-800/60 rounded-xl px-3 py-2 max-w-[85%] text-sm">
              <summary class="text-amber-400 cursor-pointer">Tool: ${esc(m.name)}</summary>
              <pre class="mt-1 text-zinc-400 overflow-x-auto">${esc(JSON.stringify(m.input, null, 2))}</pre>
            </details>
          </div>`;
        case "tool_result":
          return `<div class="flex justify-start">
            <details class="bg-zinc-800/60 rounded-xl px-3 py-2 max-w-[85%] text-sm">
              <summary class="${m.is_error ? "text-red-400" : "text-green-400"} cursor-pointer">Result${m.is_error ? " (error)" : ""}</summary>
              <pre class="mt-1 text-zinc-400 overflow-x-auto">${esc(String(m.content))}</pre>
            </details>
          </div>`;
        case "error":
          return `<div class="flex justify-start">
            <div class="bg-red-900/30 text-red-300 rounded-2xl rounded-bl-sm px-4 py-2 max-w-[75%]">${esc(m.error)}</div>
          </div>`;
        case "done":
          return `<hr class="border-zinc-800 my-2" />`;
      }
    })
    .join("");

  log.scrollTop = log.scrollHeight;
}

export function destroyChat(client: GatewayClient): void {
  if (eventHandler) {
    client.offEvent(eventHandler);
    eventHandler = null;
  }
  historyLoaded.clear();
  messages.clear();
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function fmtTime(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
