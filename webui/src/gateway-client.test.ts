import { describe, it, expect, vi } from "vitest";
import { GatewayClient, EventFrame } from "./gateway-client";

function makeEvent(event: string, session: string): EventFrame {
  return { type: "event", event, session, data: {} };
}

describe("GatewayClient event handlers", () => {
  it("offEvent removes a previously registered handler", () => {
    const client = new GatewayClient("ws://localhost:0");
    const handler = vi.fn();

    client.onEvent(handler);
    client.offEvent(handler);

    // Simulate an incoming event by calling handleMessage directly
    // We need to reach into the private method, so we'll use the
    // onmessage path via a fake WebSocket message instead.
    // Since we can't connect, we test the eventHandlers array directly.
    // After offEvent, the handler should not be in the list.
    // Dispatch an event to confirm it doesn't fire.
    (client as any).handleMessage(makeEvent("agent.text", "default"));

    expect(handler).not.toHaveBeenCalled();
  });

  it("offEvent only removes the specified handler, others still fire", () => {
    const client = new GatewayClient("ws://localhost:0");
    const handlerA = vi.fn();
    const handlerB = vi.fn();

    client.onEvent(handlerA);
    client.onEvent(handlerB);
    client.offEvent(handlerA);

    (client as any).handleMessage(makeEvent("agent.text", "default"));

    expect(handlerA).not.toHaveBeenCalled();
    expect(handlerB).toHaveBeenCalledOnce();
  });

  it("offEvent is a no-op for an unregistered handler", () => {
    const client = new GatewayClient("ws://localhost:0");
    const handler = vi.fn();

    // Should not throw
    client.offEvent(handler);

    client.onEvent(handler);
    (client as any).handleMessage(makeEvent("agent.text", "default"));
    expect(handler).toHaveBeenCalledOnce();
  });
});
