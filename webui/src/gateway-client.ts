export type Frame = {
  type: string;
  [key: string]: unknown;
};

export type EventFrame = {
  type: "event";
  event: string;
  session: string;
  data: Record<string, unknown>;
};

export type EventHandler = (event: EventFrame) => void;

export class GatewayClient {
  private ws: WebSocket | null = null;
  private pending = new Map<
    string,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  private eventHandlers: EventHandler[] = [];
  private url: string;
  private nextId = 0;

  constructor(url: string) {
    this.url = url;
  }

  connect(params: Record<string, unknown> = {}): Promise<unknown> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);
      this.ws.onopen = () => {
        this.request("connect", params).then(resolve).catch(reject);
      };
      this.ws.onmessage = (e) => this.handleMessage(JSON.parse(e.data));
      this.ws.onerror = () => reject(new Error("WebSocket error"));
      this.ws.onclose = () => {
        this.ws = null;
      };
    });
  }

  request(
    method: string,
    params: Record<string, unknown> = {}
  ): Promise<unknown> {
    const id = String(this.nextId++);
    const frame = { type: "req", id, method, params };
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws!.send(JSON.stringify(frame));
    });
  }

  onEvent(handler: EventHandler): void {
    this.eventHandlers.push(handler);
  }

  offEvent(handler: EventHandler): void {
    const idx = this.eventHandlers.indexOf(handler);
    if (idx !== -1) this.eventHandlers.splice(idx, 1);
  }

  private handleMessage(frame: Frame): void {
    if (frame.type === "res") {
      const p = this.pending.get(frame.id as string);
      if (p) {
        this.pending.delete(frame.id as string);
        if (frame.ok) p.resolve(frame.payload);
        else p.reject(new Error(frame.error as string));
      }
    } else if (frame.type === "event") {
      for (const handler of this.eventHandlers) {
        handler(frame as EventFrame);
      }
    }
  }

  disconnect(): void {
    this.ws?.close();
  }
}
