import { GatewayClient } from "../gateway-client";

type McpServerConfig = {
  type: string;
  url: string;
  headers?: Record<string, string>;
};

type McpServer = {
  scope: string;
  name: string;
  config: McpServerConfig;
};

export function initMcp(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement
) {
  loadServers(client, sidebar, content);
}

async function loadServers(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement
) {
  const result = (await client.request("mcp.list", {})) as {
    servers: McpServer[];
  };
  renderList(client, sidebar, content, result.servers);
}

function renderList(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement,
  servers: McpServer[]
) {
  sidebar.innerHTML = "";

  if (servers.length === 0) {
    const empty = document.createElement("div");
    empty.className = "p-3 text-sm text-gray-500";
    empty.textContent = "No MCP servers configured.";
    sidebar.appendChild(empty);
    content.innerHTML = "";
    return;
  }

  servers.forEach((srv) => {
    const item = document.createElement("div");
    item.className =
      "p-2 text-sm cursor-pointer rounded text-gray-400 hover:text-white";

    const nameSpan = document.createElement("span");
    nameSpan.className = "font-medium text-white";
    nameSpan.textContent = srv.name;

    const scopeSpan = document.createElement("span");
    scopeSpan.className = "text-xs text-gray-500";
    scopeSpan.textContent = srv.scope;

    item.appendChild(nameSpan);
    item.appendChild(document.createElement("br"));
    item.appendChild(scopeSpan);
    item.onclick = () => renderDetail(content, srv);
    sidebar.appendChild(item);
  });

  // Show first server detail by default
  renderDetail(content, servers[0]);
}

function renderDetail(
  content: HTMLElement,
  srv: McpServer
) {
  content.innerHTML = `
    <div class="p-4 space-y-4">
      <div class="flex items-center justify-between">
        <h2 id="detail-name" class="text-lg font-semibold"></h2>
      </div>
      <table class="text-sm w-full">
        <tr><td class="py-1 pr-4 text-gray-400">Scope</td><td id="detail-scope"></td></tr>
        <tr class="border-t border-gray-800"><td class="py-1 pr-4 text-gray-400">Type</td><td id="detail-type"></td></tr>
        <tr class="border-t border-gray-800"><td class="py-1 pr-4 text-gray-400">URL</td><td id="detail-url" class="font-mono text-xs break-all"></td></tr>
      </table>
      <div id="detail-headers"></div>
    </div>
  `;

  content.querySelector("#detail-name")!.textContent = srv.name;
  content.querySelector("#detail-scope")!.textContent = srv.scope;
  content.querySelector("#detail-type")!.textContent = srv.config.type;
  content.querySelector("#detail-url")!.textContent = srv.config.url;

  const headerEntries = Object.entries(srv.config.headers ?? {});
  if (headerEntries.length > 0) {
    const headersDiv = content.querySelector("#detail-headers")!;
    const label = document.createElement("div");
    label.className = "text-xs text-gray-400 mb-1";
    label.textContent = "Headers";
    headersDiv.appendChild(label);

    const table = document.createElement("table");
    table.className = "text-sm w-full";
    headerEntries.forEach(([k, v]) => {
      const row = table.insertRow();
      row.className = "border-t border-gray-800";
      const keyCell = row.insertCell();
      keyCell.className = "py-1 pr-4 text-gray-400 text-xs";
      keyCell.textContent = k;
      const valCell = row.insertCell();
      valCell.className = "py-1 text-xs font-mono";
      valCell.textContent = v;
    });
    headersDiv.appendChild(table);
  }
}

export function destroyMcp(): void {
  // No persistent state to clean up
}
