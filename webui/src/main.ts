import { GatewayClient } from "./gateway-client";
import { initChat, destroyChat } from "./views/chat";
import { initWorkspace, destroyWorkspace } from "./views/workspace";
import { initMcp, destroyMcp } from "./views/mcp";
import { initLogs, destroyLogs } from "./views/logs";
import { initStatus, destroyStatus } from "./views/status";

const TOKEN_KEY = "fera_auth_token";

let client: GatewayClient;
let currentView = "chat";
let sessions: { id: string; name: string; agent?: string }[] = [];
let agents: string[] = ["main"];
let gatewayWs: string;

async function init() {
  const resp = await fetch("/config.json");
  const config = await resp.json();
  gatewayWs = config.gateway_ws;

  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    await tryConnect(token);
  } else {
    showLoginForm();
  }
}

async function tryConnect(token: string) {
  client = new GatewayClient(gatewayWs);
  const sidebar = document.getElementById("sidebar-content")!;
  const content = document.getElementById("content")!;

  // Tab switching
  document.querySelectorAll(".nav-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const view = (tab as HTMLElement).dataset.view!;
      if (view !== currentView) switchView(view, sidebar, content);
    });
  });

  try {
    const info = (await client.connect({ token })) as {
      sessions: { id: string; name: string; agent?: string }[];
      agents?: string[];
    };
    localStorage.setItem(TOKEN_KEY, token);
    sessions =
      info.sessions.length > 0
        ? info.sessions
        : [{ id: "main/default", name: "default", agent: "main" }];
    agents = info.agents ?? ["main"];

    showApp();
    initChat(client, sidebar, content, sessions, agents);
    setActiveTab("chat");
  } catch (e) {
    console.error("Failed to connect:", e);
    localStorage.removeItem(TOKEN_KEY);
    showLoginForm(String(e));
  }
}

function showApp() {
  document.getElementById("topbar")!.style.display = "";
  document.getElementById("sidebar")!.style.display = "";
  document.getElementById("content")!.style.display = "";
  const login = document.getElementById("login-overlay");
  if (login) login.remove();
}

function showLoginForm(error?: string) {
  document.getElementById("topbar")!.style.display = "none";
  document.getElementById("sidebar")!.style.display = "none";
  document.getElementById("content")!.style.display = "none";

  const existing = document.getElementById("login-overlay");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "login-overlay";
  overlay.className =
    "fixed inset-0 bg-zinc-950 flex items-center justify-center";
  overlay.innerHTML = `
    <div class="w-80 bg-zinc-900 border border-zinc-800 rounded-lg p-6">
      <h1 class="text-lg font-bold mb-4">Fera</h1>
      ${error ? `<p class="text-red-400 text-sm mb-3">${error}</p>` : ""}
      <form id="login-form">
        <label class="block text-sm text-zinc-400 mb-1" for="token-input">Auth Token</label>
        <input
          id="token-input"
          type="password"
          class="w-full bg-zinc-800 border border-zinc-700 rounded px-3 py-2 text-sm text-zinc-100 mb-4 focus:outline-none focus:border-orange-500"
          placeholder="Paste your token"
          autocomplete="off"
        />
        <button
          type="submit"
          class="w-full bg-orange-600 hover:bg-orange-500 text-white text-sm font-medium rounded px-3 py-2"
        >Connect</button>
      </form>
    </div>
  `;
  document.body.appendChild(overlay);

  const form = document.getElementById("login-form")!;
  const input = document.getElementById("token-input") as HTMLInputElement;
  input.focus();

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = input.value.trim();
    if (value) tryConnect(value);
  });
}

function switchView(
  view: string,
  sidebar: HTMLElement,
  content: HTMLElement
) {
  // Tear down current view
  if (currentView === "chat") destroyChat(client);
  if (currentView === "workspace") destroyWorkspace();
  if (currentView === "mcp") destroyMcp();
  if (currentView === "logs") destroyLogs(client);
  if (currentView === "status") destroyStatus();

  currentView = view;
  setActiveTab(view);
  sidebar.innerHTML = "";
  content.innerHTML = "";

  if (view === "chat") {
    initChat(client, sidebar, content, sessions, agents);
  } else if (view === "workspace") {
    initWorkspace(client, sidebar, content, agents);
  } else if (view === "mcp") {
    initMcp(client, sidebar, content);
  } else if (view === "logs") {
    initLogs(client, sidebar, content);
  } else if (view === "status") {
    initStatus(client, sidebar, content);
  }
}

function setActiveTab(view: string) {
  document.querySelectorAll(".nav-tab").forEach((tab) => {
    const el = tab as HTMLElement;
    const isActive = el.dataset.view === view;
    el.classList.toggle("bg-zinc-800", isActive);
    el.classList.toggle("text-orange-400", isActive);
    el.classList.toggle("text-zinc-400", !isActive);
  });
}

init();
