// Subscription to the backend's server-driven event stream. One long-lived
// EventSource for the whole app; triggers are ordinary POSTs whose bodies carry
// nothing but an id.
//
// The connection lives at module scope rather than in a hook. app_shell
// re-renders many times per turn (one log line per event), and a module-level
// source is teardown-immune by construction. stream_status is *computed* from
// readyState rather than mirrored into state, the same reasoning that made the
// hash route a subscription instead of useState.
//
// Everything here is under /api on purpose: that prefix is already proxied, so
// no new entry is needed in vite.config.ts *and* its committed .js twin, which
// resolves first and has silently shadowed edits before.

export type turn_source = "user" | "agent" | "external";
export type turn_end_reason = "completed" | "cancelled" | "failed";
export type notice_kind = "tool_use" | "progress";

// Field names are duplicated by hand from backend/app/events.py. Deliberately:
// generated types are erased at runtime, so a renamed field would put `undefined`
// in a subtitle either way. What actually guards this boundary is
// parse_server_event throwing on the first bad event, plus a backend test that
// pins the wire keys. `rg turn_started_event` shows both halves.
export type turn_snapshot = {
  turn_id: string;
  source: turn_source;
  trigger_text: string | null;
  started_at: number;
};

export type stream_hello_event = {
  kind: "stream_hello";
  protocol_version: number;
  active_turn: turn_snapshot | null;
};

export type turn_started_event = {
  kind: "turn_started";
  turn_id: string;
  source: turn_source;
  trigger_text: string | null;
  started_at: number;
};

// One thing to say out loud.
export type utterance_event = {
  kind: "utterance";
  turn_id: string;
  seq: number;
  text: string;
};

// Shown, never spoken.
export type notice_event = {
  kind: "notice";
  turn_id: string;
  notice_kind: notice_kind;
  text: string;
};

export type turn_cancelling_event = {
  kind: "turn_cancelling";
  turn_id: string;
};

export type turn_ended_event = {
  kind: "turn_ended";
  turn_id: string;
  reason: turn_end_reason;
};

export type error_event = {
  kind: "error";
  turn_id: string | null;
  message: string;
};

export type server_event =
  | stream_hello_event
  | turn_started_event
  | utterance_event
  | notice_event
  | turn_cancelling_event
  | turn_ended_event
  | error_event;

export type stream_status = "connecting" | "open" | "closed";

export type event_stream_handlers = {
  on_event: (event: server_event) => void;
  on_error: (message: string) => void;
};

// Owned here, and imported by the trigger calls, so the stream and the POSTs
// cannot end up resolving against different origins.
export const api_base_url = import.meta.env.VITE_API_BASE_URL || "";

const events_url = `${api_base_url}/api/events`;
const reconnect_delay_ms = 2000;

const handlers_set = new Set<event_stream_handlers>();
const status_listeners = new Set<() => void>();

let source: EventSource | null = null;
let reopen_timer = 0;
let last_seq = 0;

export function parse_server_event(payload: string): server_event {
  const data = JSON.parse(payload) as { kind?: unknown };

  switch (data.kind) {
    case "stream_hello":
    case "turn_started":
    case "utterance":
    case "notice":
    case "turn_cancelling":
    case "turn_ended":
    case "error":
      return data as server_event;
    default:
      throw new Error(`알 수 없는 서버 이벤트: ${String(data.kind)}`);
  }
}

export function read_stream_status(): stream_status {
  if (source === null) {
    return "closed";
  }

  if (source.readyState === EventSource.OPEN) {
    return "open";
  }

  return source.readyState === EventSource.CONNECTING ? "connecting" : "closed";
}

export function subscribe_stream_status(on_change: () => void): () => void {
  status_listeners.add(on_change);

  return () => status_listeners.delete(on_change);
}

function notify_status(): void {
  status_listeners.forEach((listener) => listener());
}

function report_error(message: string): void {
  handlers_set.forEach((handlers) => handlers.on_error(message));
}

function handle_message(message: MessageEvent<string>): void {
  // The hello frame deliberately carries no id, so lastEventId is "" on a fresh
  // connection. Everything else is numbered, and anything at or below what we
  // have already seen is a replay we must not act on twice - otherwise a
  // reconnect makes the device say the same sentence again.
  const seq = message.lastEventId === "" ? 0 : Number(message.lastEventId);

  if (seq > 0) {
    if (seq <= last_seq) {
      return;
    }

    last_seq = seq;
  }

  let event: server_event;

  try {
    event = parse_server_event(message.data);
  } catch (error) {
    // A deliberate departure from "crash early": a white-screened Chromium on a
    // keyboard-less Pi is unrecoverable, and unattended boot is the whole point
    // of #/face. Loud, not fatal.
    report_error(error instanceof Error ? error.message : "이벤트 해석 실패");
    return;
  }

  if (event.kind === "stream_hello") {
    // A hello means the server could not resume us, which includes the case
    // where it restarted and its numbering went back to 1. Keeping the old
    // cursor would silently discard every event until it climbed past it.
    last_seq = 0;
  }

  handlers_set.forEach((handlers) => handlers.on_event(event));
}

function close_stream(): void {
  window.clearTimeout(reopen_timer);
  reopen_timer = 0;

  if (source !== null) {
    source.close();
    source = null;
  }
}

function open_stream(): void {
  if (source !== null) {
    return;
  }

  const opened = new EventSource(events_url);
  source = opened;

  opened.onopen = notify_status;
  opened.onmessage = handle_message;
  opened.onerror = () => {
    notify_status();

    // A dropped connection is retried by EventSource itself, and the events
    // missed in the gap come back through Last-Event-ID replay - so a blip must
    // not be treated as "the turn is over". Only a stream_hello, which the
    // server sends exactly when it cannot resume us, resets turn state.
    //
    // EventSource does give up for good on an HTTP error; that case is ours.
    if (opened.readyState === EventSource.CLOSED) {
      close_stream();
      reopen_timer = window.setTimeout(open_stream, reconnect_delay_ms);
    }
  };

  notify_status();
}

// iOS suspends a backgrounded tab and the socket can come back half-open without
// EventSource noticing, so foregrounding forces a reopen. Registered once,
// because the module body runs once.
document.addEventListener("visibilitychange", () => {
  if (
    document.visibilityState === "visible" &&
    handlers_set.size > 0 &&
    read_stream_status() === "closed"
  ) {
    close_stream();
    open_stream();
  }
});

export function subscribe_events(handlers: event_stream_handlers): () => void {
  handlers_set.add(handlers);
  open_stream();

  return () => {
    handlers_set.delete(handlers);

    if (handlers_set.size === 0) {
      close_stream();
      notify_status();
    }
  };
}
