/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GatewayClient, EventFrame } from "../gateway-client";
import { initChat, destroyChat, historyEntryToMessage } from "./chat";

function makeMinimalDOM(): { sidebar: HTMLElement; content: HTMLElement } {
  const sidebar = document.createElement("div");
  const content = document.createElement("div");
  content.id = "content";
  document.body.appendChild(sidebar);
  document.body.appendChild(content);
  return { sidebar, content };
}

function makeEvent(event: string, session: string, data: Record<string, unknown> = {}): EventFrame {
  return { type: "event", event, session, data };
}

describe("chat view lifecycle", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("destroyChat prevents duplicate messages on re-init", () => {
    const { sidebar, content } = makeMinimalDOM();
    const sessions = [{ id: "main/default", name: "default", agent: "main" }];

    // First init
    initChat(client, sidebar, content, sessions);

    // Simulate leaving chat view and coming back
    destroyChat(client);
    initChat(client, sidebar, content, sessions);

    // Dispatch an event
    (client as any).handleMessage(makeEvent("agent.text", "main/default", { text: "hello" }));

    // Should have exactly one text message, not two
    const log = content.querySelector("#chat-log");
    const textMessages = log?.querySelectorAll("[data-message-type='agent']") ?? [];
    expect(textMessages.length).toBe(1);
  });

  it("without destroyChat, re-init causes duplicate messages", () => {
    const { sidebar, content } = makeMinimalDOM();
    const sessions = [{ id: "main/default", name: "default", agent: "main" }];

    // First init
    initChat(client, sidebar, content, sessions);

    // Re-init WITHOUT destroy (the bug scenario)
    initChat(client, sidebar, content, sessions);

    // Dispatch an event
    (client as any).handleMessage(makeEvent("agent.text", "main/default", { text: "hello" }));

    // Bug: multiple handlers push to messages, so we get duplicates
    const log = content.querySelector("#chat-log");
    const textMessages = log?.querySelectorAll("[data-message-type='agent']") ?? [];
    expect(textMessages.length).toBeGreaterThan(1);
  });
});

describe("historyEntryToMessage", () => {
  it("maps user entry", () => {
    const msg = historyEntryToMessage({ type: "user", text: "hi", source: "web", ts: "" });
    expect(msg).toEqual({ type: "user", text: "hi", source: "web" });
  });

  it("maps agent entry", () => {
    const msg = historyEntryToMessage({ type: "agent", text: "reply", turn_source: "telegram", ts: "" });
    expect(msg).toEqual({ type: "text", text: "reply" });
  });

  it("maps tool_use entry", () => {
    const msg = historyEntryToMessage({ type: "tool_use", name: "Bash", input: { command: "ls" }, id: "t1", ts: "" });
    expect(msg).toEqual({ type: "tool_use", name: "Bash", input: { command: "ls" } });
  });

  it("maps tool_result entry", () => {
    const msg = historyEntryToMessage({ type: "tool_result", content: "ok", is_error: false, tool_use_id: "t1", ts: "" });
    expect(msg).toEqual({ type: "tool_result", content: "ok", is_error: false });
  });

  it("maps done entry", () => {
    const msg = historyEntryToMessage({ type: "done", ts: "" });
    expect(msg).toEqual({ type: "done" });
  });

  it("maps error entry", () => {
    const msg = historyEntryToMessage({ type: "error", error: "oops", ts: "" });
    expect(msg).toEqual({ type: "error", error: "oops" });
  });
});

describe("agent message HTML rendering", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("renders agent.text html field as rich HTML when present", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    (client as any).handleMessage(
      makeEvent("agent.text", "main/default", {
        text: "**bold**",
        html: "<p><strong>bold</strong></p>",
      })
    );

    const log = content.querySelector("#chat-log");
    const agentMsg = log?.querySelector("[data-message-type='agent']");
    expect(agentMsg?.innerHTML).toContain("<strong>bold</strong>");
    expect(agentMsg?.innerHTML).not.toContain("&lt;strong&gt;");
  });

  it("falls back to escaped text when html field is absent", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    (client as any).handleMessage(
      makeEvent("agent.text", "main/default", { text: "**bold**" })
    );

    const log = content.querySelector("#chat-log");
    const agentMsg = log?.querySelector("[data-message-type='agent']");
    expect(agentMsg?.innerHTML).toContain("**bold**");
  });

  it("renders history entries with html field as rich HTML", async () => {
    vi.spyOn(client, "request").mockResolvedValue({
      messages: [
        { type: "agent", text: "**bold**", html: "<p><strong>bold</strong></p>", ts: "" },
      ],
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);
    await new Promise((r) => setTimeout(r, 0));

    const log = content.querySelector("#chat-log");
    expect(log?.innerHTML).toContain("<strong>bold</strong>");
  });
});

describe("bubble message layout", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("user messages are right-aligned", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    (client as any).handleMessage(makeEvent("user.message", "main/default", { text: "hello", source: "web" }));

    const log = content.querySelector("#chat-log");
    const userMsg = log?.querySelector("[data-message-type='user']");
    expect(userMsg).not.toBeNull();
    expect(userMsg?.parentElement?.classList.contains("justify-end")).toBe(true);
  });

  it("agent messages are left-aligned", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    (client as any).handleMessage(makeEvent("agent.text", "main/default", { text: "hi" }));

    const log = content.querySelector("#chat-log");
    const agentMsg = log?.querySelector("[data-message-type='agent']");
    expect(agentMsg).not.toBeNull();
    expect(agentMsg?.parentElement?.classList.contains("justify-start")).toBe(true);
  });
});

describe("session list grouping", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("renders agent name as a group header above its sessions", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main" },
      { id: "forge/work", name: "work", agent: "forge" },
    ]);

    const headers = Array.from(sidebar.querySelectorAll("[data-agent-header]")).map(el => el.textContent);
    expect(headers).toContain("main");
    expect(headers).toContain("forge");
  });

  it("sessions appear under their agent's header", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main" },
      { id: "forge/work", name: "work", agent: "forge" },
    ]);

    const mainHeader = sidebar.querySelector("[data-agent-header='main']")!;
    const forgeHeader = sidebar.querySelector("[data-agent-header='forge']")!;

    // The session item after the main header should be "default"
    const afterMain = mainHeader.nextElementSibling;
    expect(afterMain?.textContent).toContain("default");

    // The session item after the forge header should be "work"
    const afterForge = forgeHeader.nextElementSibling;
    expect(afterForge?.textContent).toContain("work");
  });

  it("main agent header appears before alphabetically-earlier agents", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "aardvark/s", name: "s", agent: "aardvark" },
      { id: "main/default", name: "default", agent: "main" },
      { id: "zebra/s", name: "s", agent: "zebra" },
    ]);

    const headers = Array.from(sidebar.querySelectorAll("[data-agent-header]"))
      .map(el => el.getAttribute("data-agent-header"));
    expect(headers[0]).toBe("main");
    expect(headers).toEqual(["main", "aardvark", "zebra"]);
  });
});

describe("chat bubble timestamps", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("live user message has a timestamp inside the bubble", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    (client as any).handleMessage(makeEvent("user.message", "main/default", { text: "hello", source: "web" }));

    const userMsg = content.querySelector("[data-message-type='user']");
    expect(userMsg?.querySelector("[data-message-ts]")).not.toBeNull();
  });

  it("live agent text message has a timestamp inside the bubble", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    (client as any).handleMessage(makeEvent("agent.text", "main/default", { text: "hi" }));

    const agentMsg = content.querySelector("[data-message-type='agent']");
    expect(agentMsg?.querySelector("[data-message-ts]")).not.toBeNull();
  });

  it("history user message with ts has a timestamp inside the bubble", async () => {
    vi.spyOn(client, "request").mockResolvedValue({
      messages: [{ type: "user", text: "hi", source: "web", ts: "2026-02-22T14:32:00.000Z" }],
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);
    await new Promise(r => setTimeout(r, 0));

    const userMsg = content.querySelector("[data-message-type='user']");
    expect(userMsg?.querySelector("[data-message-ts]")).not.toBeNull();
  });

  it("history agent message with ts has a timestamp inside the bubble", async () => {
    vi.spyOn(client, "request").mockResolvedValue({
      messages: [{ type: "agent", text: "hi", ts: "2026-02-22T14:33:00.000Z" }],
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);
    await new Promise(r => setTimeout(r, 0));

    const agentMsg = content.querySelector("[data-message-type='agent']");
    expect(agentMsg?.querySelector("[data-message-ts]")).not.toBeNull();
  });

  it("history message without ts has no timestamp element", async () => {
    vi.spyOn(client, "request").mockResolvedValue({
      messages: [{ type: "user", text: "hi", source: "web", ts: "" }],
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);
    await new Promise(r => setTimeout(r, 0));

    const userMsg = content.querySelector("[data-message-type='user']");
    expect(userMsg?.querySelector("[data-message-ts]")).toBeNull();
  });
});

describe("user bubble border", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("user bubble has orange border instead of orange background", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    (client as any).handleMessage(makeEvent("user.message", "main/default", { text: "hello", source: "web" }));

    const userMsg = content.querySelector("[data-message-type='user']") as HTMLElement;
    expect(userMsg).not.toBeNull();
    expect(userMsg.className).toContain("border-orange-500");
    expect(userMsg.className).not.toContain("bg-orange-");
  });
});

describe("new session modal", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("new session button opens a modal with agent select", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [], ["main", "forge"]);

    const newBtn = sidebar.querySelector("button");
    newBtn?.click();

    const modal = document.getElementById("new-session-modal");
    expect(modal).not.toBeNull();

    const select = modal?.querySelector("select[name='agent']") as HTMLSelectElement;
    expect(select).not.toBeNull();
    const options = Array.from(select.options).map(o => o.value);
    expect(options).toContain("main");
    expect(options).toContain("forge");
  });

  it("submitting new session form calls session.create and closes modal", async () => {
    const { sidebar, content } = makeMinimalDOM();
    const requestSpy = vi.spyOn(client, "request").mockResolvedValue({
      messages: [],
      id: "forge/work", name: "work", agent: "forge",
    });
    initChat(client, sidebar, content, [], ["main", "forge"]);

    const newBtn = sidebar.querySelector("button");
    newBtn?.click();

    const modal = document.getElementById("new-session-modal")!;
    const nameInput = modal.querySelector("input[name='session-name']") as HTMLInputElement;
    const agentSelect = modal.querySelector("select[name='agent']") as HTMLSelectElement;
    const form = modal.querySelector("form")!;

    nameInput.value = "work";
    agentSelect.value = "forge";
    form.dispatchEvent(new Event("submit", { bubbles: true }));
    await new Promise(r => setTimeout(r, 0));

    expect(requestSpy).toHaveBeenCalledWith("session.create", { name: "work", agent: "forge" });
    expect(document.getElementById("new-session-modal")).toBeNull();
  });
});

describe("chat view loads session history on init", () => {
  let client: GatewayClient = new GatewayClient("ws://localhost:0");

  beforeEach(() => {
    document.body.innerHTML = "";
    // Clear module-level historyLoaded set from any previous tests
    destroyChat(client);
    client = new GatewayClient("ws://localhost:0");
  });

  it("fetches history for initial session", async () => {
    const requestSpy = vi.spyOn(client, "request").mockResolvedValue({
      messages: [{ type: "user", text: "old message", source: "web", ts: "" }],
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);

    await new Promise((r) => setTimeout(r, 0));

    expect(requestSpy).toHaveBeenCalledWith("session.history", { session: "main/default" });

    const log = content.querySelector("#chat-log");
    expect(log?.innerHTML).toContain("old message");
  });

  it("does not duplicate history when view is destroyed and re-initialized", async () => {
    vi.spyOn(client, "request").mockResolvedValue({
      messages: [{ type: "user", text: "history msg", source: "web", ts: "" }],
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);
    await new Promise((r) => setTimeout(r, 0));

    // navigate away and back
    destroyChat(client);
    initChat(client, sidebar, content, [{ id: "main/default", name: "default", agent: "main" }]);
    await new Promise((r) => setTimeout(r, 0));

    const log = content.querySelector("#chat-log");
    const items = log?.querySelectorAll("[data-message-type='user']") ?? [];
    expect(items.length).toBe(1); // history msg appears exactly once
  });

  it("does not re-fetch history when switching back to an already-loaded session", async () => {
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [{ id: "main/a", name: "a", agent: "main" }, { id: "main/b", name: "b", agent: "main" }]);
    await new Promise((r) => setTimeout(r, 0));

    // switch to b (triggers history fetch for b)
    // find the "b" session item in the sidebar and click it
    const bItem = sidebar.querySelector("[data-session-id='main/b']") as HTMLElement;
    bItem?.click();
    await new Promise((r) => setTimeout(r, 0));

    // switch back to a (should NOT re-fetch for a)
    const aItem = sidebar.querySelector("[data-session-id='main/a']") as HTMLElement;
    aItem?.click();
    await new Promise((r) => setTimeout(r, 0));

    const historyCalls = (client.request as ReturnType<typeof vi.spyOn>).mock.calls.filter(
      ([m]: [string, ...unknown[]]) => m === "session.history"
    );
    expect(historyCalls.length).toBe(2); // once for "a" on init, once for "b" on first switch
  });
});

describe("session hover actions", () => {
  let client: GatewayClient;

  beforeEach(() => {
    document.body.innerHTML = "";
    client = new GatewayClient("ws://localhost:0");
    vi.spyOn(client, "request").mockResolvedValue({ messages: [] });
    destroyChat(client);
  });

  it("session row has deactivate and delete buttons", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main", pooled: true },
    ]);

    const deactivateBtn = sidebar.querySelector("[data-action='deactivate']");
    const deleteBtn = sidebar.querySelector("[data-action='delete']");
    expect(deactivateBtn).not.toBeNull();
    expect(deleteBtn).not.toBeNull();
  });

  it("deactivated session row is dimmed", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main", pooled: false },
    ]);

    const row = sidebar.querySelector("[data-session-id='main/default']") as HTMLElement;
    expect(row).not.toBeNull();
    expect(row.className).toContain("text-zinc-600");
  });

  it("active session row is not dimmed", () => {
    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main", pooled: true },
    ]);

    const row = sidebar.querySelector("[data-session-id='main/default']") as HTMLElement;
    expect(row).not.toBeNull();
    expect(row.className).not.toContain("text-zinc-600");
  });

  it("clicking deactivate calls session.deactivate and re-renders", async () => {
    const requestSpy = vi.spyOn(client, "request").mockImplementation(async (method) => {
      if (method === "session.deactivate") return {};
      if (method === "session.list") return { sessions: [{ id: "main/default", name: "default", agent: "main", pooled: false, stats: {} }] };
      return { messages: [] };
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main", pooled: true },
    ]);

    const btn = sidebar.querySelector("[data-action='deactivate']") as HTMLElement;
    btn.click();
    await new Promise(r => setTimeout(r, 0));

    expect(requestSpy).toHaveBeenCalledWith("session.deactivate", { session: "main/default" });
  });

  it("clicking delete calls session.delete and removes from list", async () => {
    // Mock window.confirm to auto-approve
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const requestSpy = vi.spyOn(client, "request").mockImplementation(async (method) => {
      if (method === "session.delete") return {};
      return { messages: [] };
    });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main", pooled: true },
    ]);

    const btn = sidebar.querySelector("[data-action='delete']") as HTMLElement;
    btn.click();
    await new Promise(r => setTimeout(r, 0));

    expect(requestSpy).toHaveBeenCalledWith("session.delete", { session: "main/default" });
  });

  it("clicking delete with confirm cancelled does nothing", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);

    const requestSpy = vi.spyOn(client, "request").mockResolvedValue({ messages: [] });

    const { sidebar, content } = makeMinimalDOM();
    initChat(client, sidebar, content, [
      { id: "main/default", name: "default", agent: "main", pooled: true },
    ]);

    const btn = sidebar.querySelector("[data-action='delete']") as HTMLElement;
    btn.click();
    await new Promise(r => setTimeout(r, 0));

    const deleteCalls = requestSpy.mock.calls.filter(([m]) => m === "session.delete");
    expect(deleteCalls.length).toBe(0);
  });
});
