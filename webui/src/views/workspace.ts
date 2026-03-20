import { GatewayClient } from "../gateway-client";
import { EditorView, basicSetup } from "codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { oneDark } from "@codemirror/theme-one-dark";

let editor: EditorView | null = null;
let currentPath = "";
let selectedAgent = "main";

export function initWorkspace(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement,
  agents: string[] = ["main"]
) {
  selectedAgent = agents[0];

  renderAgentSelector(client, sidebar, content, agents);
  loadTree(client, sidebar, content, "");
  renderEditor(client, content);
}

function renderAgentSelector(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement,
  agents: string[]
) {
  if (agents.length <= 1) return; // no selector needed for single agent

  const bar = document.createElement("div");
  bar.className = "flex gap-1 p-2 border-b border-zinc-800 agent-selector-bar";

  for (const agent of agents) {
    const btn = document.createElement("button");
    btn.textContent = agent;
    btn.className = `text-xs px-2 py-1 rounded ${
      agent === selectedAgent
        ? "bg-orange-600 text-white"
        : "text-zinc-400 hover:text-white bg-zinc-800"
    }`;
    btn.onclick = () => {
      selectedAgent = agent;
      currentPath = "";
      renderAgentSelector(client, sidebar, content, agents);
      loadTree(client, sidebar, content, "");
    };
    bar.appendChild(btn);
  }

  const existingBar = sidebar.querySelector(".agent-selector-bar");
  if (existingBar) {
    existingBar.replaceWith(bar);
  } else {
    sidebar.insertBefore(bar, sidebar.firstChild);
  }
}

async function loadTree(
  client: GatewayClient,
  sidebar: HTMLElement,
  content: HTMLElement,
  path: string
) {
  const result = (await client.request("workspace.list", { path, agent: selectedAgent })) as {
    files: { name: string; type: string }[];
  };

  // Remove existing file tree entries (keep agent selector bar if present)
  const bar = sidebar.querySelector(".agent-selector-bar");
  sidebar.innerHTML = "";
  if (bar) sidebar.appendChild(bar);

  // Back button if in subdir
  if (path) {
    const back = document.createElement("div");
    back.className = "p-2 text-sm text-zinc-400 hover:text-white cursor-pointer";
    back.textContent = "\u2190 ..";
    back.onclick = () => {
      const parent = path.split("/").slice(0, -1).join("/");
      loadTree(client, sidebar, content, parent);
    };
    sidebar.appendChild(back);
  }

  for (const file of result.files) {
    const item = document.createElement("div");
    const fullPath = path ? `${path}/${file.name}` : file.name;
    item.className = `p-2 text-sm cursor-pointer rounded ${
      fullPath === currentPath ? "bg-zinc-700 text-white" : "text-zinc-400 hover:text-white"
    }`;

    if (file.type === "directory") {
      item.textContent = `\uD83D\uDCC1 ${file.name}`;
      item.onclick = () => loadTree(client, sidebar, content, fullPath);
    } else {
      item.textContent = file.name;
      item.onclick = () => openFile(client, content, fullPath);
    }
    sidebar.appendChild(item);
  }
}

async function openFile(
  client: GatewayClient,
  content: HTMLElement,
  path: string
) {
  currentPath = path;
  const result = (await client.request("workspace.get", { path, agent: selectedAgent })) as {
    content: string;
    path: string;
  };

  if (editor) {
    editor.dispatch({
      changes: {
        from: 0,
        to: editor.state.doc.length,
        insert: result.content,
      },
    });
  }

  const title = content.querySelector("#file-title");
  if (title) title.textContent = path;
}

function renderEditor(client: GatewayClient, content: HTMLElement) {
  content.innerHTML = `
    <div class="flex items-center justify-between p-3 border-b border-zinc-800">
      <span id="file-title" class="text-sm text-zinc-400">Select a file</span>
      <button id="save-btn" class="bg-green-700 hover:bg-green-600 text-white text-sm px-3 py-1 rounded">Save</button>
    </div>
    <div id="editor-container" class="flex-1 overflow-hidden"></div>
  `;

  const container = content.querySelector("#editor-container") as HTMLElement;
  editor = new EditorView({
    doc: "",
    extensions: [basicSetup, markdown(), oneDark, EditorView.lineWrapping],
    parent: container,
  });

  const style = document.createElement("style");
  style.textContent = `
    #editor-container .cm-editor { height: 100%; }
    #editor-container .cm-scroller { overflow: auto; }
  `;
  content.appendChild(style);

  content.querySelector("#save-btn")!.addEventListener("click", async () => {
    if (!currentPath || !editor) return;
    const text = editor.state.doc.toString();
    await client.request("workspace.set", { path: currentPath, content: text, agent: selectedAgent });
    const title = content.querySelector("#file-title")!;
    title.textContent = `${currentPath} \u2713 saved`;
    setTimeout(() => {
      title.textContent = currentPath;
    }, 2000);
  });
}

export function destroyWorkspace() {
  if (editor) {
    editor.destroy();
    editor = null;
  }
  currentPath = "";
  selectedAgent = "main";
}
