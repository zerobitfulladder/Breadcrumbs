import { useCallback, useEffect, useRef, useState } from "react";
import { api, chatStream } from "../api";
import type { ChatContext, ChatSession, ToolActivity } from "../api";
import { kindForTool, notifyChange } from "../live";
import AutoTextarea from "./AutoTextarea";
import Markdown from "./Markdown";
import PaperPreviewCard from "./PaperPreviewCard";
import "./Assistant.css";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: ChatContext;
  /** Set by other panels to pre-fill the box without sending it. */
  draft?: string | null;
  onDraftUsed?: () => void;
  onChanged?: () => void;
  /** A passage attached from the document, shown as a badge under the box. */
  attachment?: { page: number; text: string } | null;
  onClearAttachment?: () => void;
  /** The assistant asking to scroll the reader somewhere. */
  onReveal?: (r: { page: number; rects: { x: number; y: number; w: number; h: number }[] }) => void;
  /** Rendered inline instead of as a floating window. */
  docked?: boolean;
}

interface Turn {
  role: "user" | "assistant";
  text: string;
  tools?: ToolActivity[];
  /** Set on user turns, so an edit knows where to truncate the thread. */
  messageId?: number;
  /** The turn was cut off, either by Stop or by the next message. */
  interrupted?: boolean;
}

interface Frame {
  x: number;
  y: number;
  w: number;
  h: number;
}

const MIN_W = 320;
const MIN_H = 280;
const STORAGE_KEY = "breadcrumbs.assistant.frame";
const THREAD_KEY = "breadcrumbs.assistant.thread";

/** Bottom-right by default, where a support panel is expected to sit. */
function defaultFrame(): Frame {
  const w = Math.min(420, window.innerWidth - 36);
  const h = Math.min(620, window.innerHeight - 90);
  return { x: window.innerWidth - w - 18, y: window.innerHeight - h - 18, w, h };
}

function clampToViewport(f: Frame): Frame {
  const w = Math.min(Math.max(f.w, MIN_W), window.innerWidth);
  const h = Math.min(Math.max(f.h, MIN_H), window.innerHeight);
  return {
    w,
    h,
    // Keep at least a strip of the header reachable after a window resize.
    x: Math.min(Math.max(f.x, -w + 120), window.innerWidth - 80),
    y: Math.min(Math.max(f.y, 0), window.innerHeight - 40),
  };
}

/** The argument worth showing on a collapsed row: the URL, the query, the id. */
function describeArgs(args: Record<string, unknown> | undefined): string | null {
  if (!args) return null;
  for (const key of ["url", "query", "identifier", "name", "sql"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) {
      return value.length > 64 ? `${value.slice(0, 64)}…` : value;
    }
  }
  const first = Object.entries(args)[0];
  return first ? `${first[0]}: ${String(first[1]).slice(0, 40)}` : null;
}

export default function Assistant({
  open,
  onOpenChange,
  context,
  draft,
  onDraftUsed,
  onChanged,
  attachment,
  onClearAttachment,
  onReveal,
  docked = false,
}: Props) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loadingThread, setLoadingThread] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState<ToolActivity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<{ identifier: string; reason?: string } | null>(null);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const [frame, setFrame] = useState<Frame>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) return clampToViewport(JSON.parse(saved) as Frame);
    } catch {
      /* a corrupt or blocked store just means the default position */
    }
    return defaultFrame();
  });

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const closeRef = useRef<(() => void) | null>(null);
  const gesture = useRef<{ mode: "move" | "resize"; x: number; y: number; start: Frame } | null>(
    null,
  );

  useEffect(() => () => closeRef.current?.(), []);

  // A reload must not leave a turn running against a page that no longer
  // exists. Aborting on unload tells the server at once rather than leaving it
  // to notice the dead connection later.
  useEffect(() => {
    const abort = () => closeRef.current?.();
    window.addEventListener("pagehide", abort);
    window.addEventListener("beforeunload", abort);
    return () => {
      window.removeEventListener("pagehide", abort);
      window.removeEventListener("beforeunload", abort);
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(frame));
    } catch {
      /* not being able to remember the position is not worth an error */
    }
  }, [frame]);

  useEffect(() => {
    const onResize = () => setFrame((f) => clampToViewport(f));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // --- move and resize -------------------------------------------------------
  const startGesture = (e: React.PointerEvent, mode: "move" | "resize") => {
    if (e.button !== 0) return;
    e.preventDefault();
    gesture.current = { mode, x: e.clientX, y: e.clientY, start: frame };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onGestureMove = (e: React.PointerEvent) => {
    const g = gesture.current;
    if (!g) return;
    const dx = e.clientX - g.x;
    const dy = e.clientY - g.y;
    setFrame(
      clampToViewport(
        g.mode === "move"
          ? { ...g.start, x: g.start.x + dx, y: g.start.y + dy }
          : { ...g.start, w: g.start.w + dx, h: g.start.h + dy },
      ),
    );
  };

  const endGesture = (e: React.PointerEvent) => {
    if (gesture.current) (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    gesture.current = null;
  };

  // A pre-filled request from elsewhere opens the panel but is never sent:
  // the user reviews it and presses enter themselves.
  useEffect(() => {
    if (draft == null) return;
    onOpenChange(true);
    setInput(draft);
    onDraftUsed?.();
    window.setTimeout(() => {
      inputRef.current?.focus();
      const end = inputRef.current?.value.length ?? 0;
      inputRef.current?.setSelectionRange(end, end);
    }, 60);
  }, [draft, onDraftUsed, onOpenChange]);

  // Threads are listed once the panel opens, and the last one used is
  // reopened, so closing the panel does not lose the conversation.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .chatSessions()
      .then(({ sessions: found }) => {
        if (cancelled) return;
        setSessions(found);
        if (sessionId == null && found.length) {
          const remembered = Number(localStorage.getItem(THREAD_KEY));
          const pick = found.find((f) => f.id === remembered) ?? found[0];
          void openThread(pick.id);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open || configured !== null) return;
    api
      .settings()
      .then((s) => setConfigured(Boolean(s.assistant_provider && s.assistant_model)))
      .catch(() => setConfigured(false));
  }, [open, configured]);

  useEffect(() => {
    if (open) window.setTimeout(() => inputRef.current?.focus(), 60);
  }, [open]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, activity, busy]);

  const send = useCallback(
    (override?: { text: string; truncateFromId: number; keepTurns: number }) => {
    const text = (override?.text ?? input).trim();
    if (!text) return;

    // Sending while it is working interrupts what it is doing. Waiting for a
    // tool to finish before you can correct it is the wrong way round.
    if (busy) {
      closeRef.current?.();
      closeRef.current = null;
      // Keep what it had already done, so the record shows where it was cut off.
      setTurns((t) => [...t, { role: "assistant", text: "", tools: activity, interrupted: true }]);
      setActivity([]);
    }

    if (override) {
      // Drop the old wording and everything it produced; the server is told
      // to do the same so the two transcripts stay in step.
      setTurns((t) => [...t.slice(0, override.keepTurns), { role: "user", text }]);
    } else {
      setTurns((t) => [...t, { role: "user", text }]);
    }
    setInput("");
    setBusy(true);
    setError(null);
    setActivity([]);

    let streamed = "";
    const seen: ToolActivity[] = [];
    let wrote = false;

    closeRef.current = chatStream(
      {
        session_id: sessionId,
        message: text,
        context: attachment ? { ...context, selection: attachment } : context,
        truncate_from_id: override?.truncateFromId ?? null,
      },
      {
        onSession: (id) => {
          setSessionId(id);
          localStorage.setItem(THREAD_KEY, String(id));
          setSessions((all) =>
            all.some((t) => t.id === id)
              ? all.map((t) =>
                  t.id === id && !t.title ? { ...t, title: text.slice(0, 60) } : t,
                )
              : [
                  { id, title: text.slice(0, 60), created_at: "", updated_at: "", turns: 1 },
                  ...all,
                ],
          );
        },
        onStart: (d) =>
          setTurns((t) => {
            // Tag the user turn just sent, so it can be edited later.
            const next = [...t];
            for (let i = next.length - 1; i >= 0; i -= 1) {
              if (next[i].role === "user") {
                next[i] = { ...next[i], messageId: d.user_message_id };
                break;
              }
            }
            return next;
          }),
        onText: (chunk) => {
          streamed += chunk;
        },
        onToolCall: (t) => {
          seen.push({ name: t.name, args: t.args, status: "running" });
          setActivity([...seen]);
        },
        onToolResult: (r) => {
          const hit = seen.find((s) => s.name === r.name && s.status === "running");
          if (hit) {
            hit.status = r.error ? "failed" : "done";
            hit.summary = r.summary;
            hit.isWrite = r.is_write;
            hit.preview = r.preview;
          }
          // Announce the moment it happens rather than when the turn ends, so
          // a panel showing that record updates while you watch.
          if (r.is_write && !r.error) {
            wrote = true;
            notifyChange({ kind: kindForTool(r.name), source: r.name });
          }
          setActivity([...seen]);
        },
        onProposal: (p) => setProposal(p),
        onReveal: (r) =>
          onReveal?.({ page: r.page, rects: Array.isArray(r.rects) ? r.rects : [] }),
        onMessage: (m) => {
          setTurns((t) => [
            ...t,
            { role: "assistant", text: m.text || streamed, tools: [...seen] },
          ]);
          setActivity([]);
        },
        onError: (message) => {
          setError(message);
          setBusy(false);
          setActivity([]);
        },
        onDone: () => {
          setBusy(false);
          // A final sweep, in case a tool changed something it did not name.
          if (wrote) onChanged?.();
        },
      },
    );
    },
    [input, busy, sessionId, context, onChanged],
  );

  function stop() {
    closeRef.current?.();
    closeRef.current = null;
    setBusy(false);
    setTurns((t) => [...t, { role: "assistant", text: "", tools: activity, interrupted: true }]);
    setActivity([]);
  }

  /** Load an existing thread into the panel, replacing what is on screen. */
  async function openThread(id: number) {
    closeRef.current?.();
    closeRef.current = null;
    setBusy(false);
    setActivity([]);
    setError(null);
    setEditing(null);
    setSessionId(id);
    localStorage.setItem(THREAD_KEY, String(id));
    setLoadingThread(true);
    try {
      const { messages } = await api.chatHistory(id);
      setTurns(
        messages.map((m) => ({
          role: m.role,
          text: m.content ?? "",
          tools: m.tools,
          messageId: m.role === "user" ? (m.id ?? undefined) : undefined,
          interrupted: m.interrupted,
        })),
      );
    } catch {
      setTurns([]);
    } finally {
      setLoadingThread(false);
    }
  }

  async function newThread() {
    closeRef.current?.();
    closeRef.current = null;
    setTurns([]);
    setActivity([]);
    setError(null);
    setBusy(false);
    setEditing(null);
    try {
      const { session_id } = await api.newChatSession();
      setSessionId(session_id);
      localStorage.setItem(THREAD_KEY, String(session_id));
      setSessions((all) => [
        { id: session_id, title: null, created_at: "", updated_at: "", turns: 0 },
        ...all,
      ]);
    } catch {
      setSessionId(null);
    }
  }

  async function closeThread(id: number) {
    try {
      await api.deleteChatSession(id);
    } catch {
      /* it may already be gone; drop it from the bar either way */
    }
    const left = sessions.filter((t) => t.id !== id);
    setSessions(left);
    if (id === sessionId) {
      if (left.length) void openThread(left[0].id);
      else void newThread();
    }
  }

  if (!open) return null;

  const toolList = (tools: ToolActivity[], turnKey: string) => (
    <ul className="as-tools">
      {tools.map((tool, j) => {
        const key = `${turnKey}:${j}`;
        const isOpen = expanded === key;
        const detail = describeArgs(tool.args);
        const canExpand = Boolean(detail || tool.preview);
        return (
          <li key={j} className={`is-${tool.status}${isOpen ? " is-open" : ""}`}>
            <div className="as-tool-row">
              <button
                className="as-tool-toggle"
                onClick={() => canExpand && setExpanded(isOpen ? null : key)}
                disabled={!canExpand}
                title={canExpand ? "Show what it sent and what came back" : undefined}
              >
                <span className="as-caret" aria-hidden>
                  {canExpand ? (isOpen ? "▾" : "▸") : "·"}
                </span>
                <code>{tool.name}</code>
              </button>
              {tool.isWrite && <span className="as-write">wrote</span>}
              {detail && !isOpen && <span className="as-tool-arg">{detail}</span>}
              {tool.summary && <span className="as-summary">{tool.summary}</span>}
            </div>
            {isOpen && (
              <div className="as-tool-detail">
                {tool.args && Object.keys(tool.args).length > 0 && (
                  <>
                    <span className="as-detail-label">Sent</span>
                    <pre>{JSON.stringify(tool.args, null, 1)}</pre>
                  </>
                )}
                {tool.preview && (
                  <>
                    <span className="as-detail-label">Returned</span>
                    <pre>{tool.preview}</pre>
                  </>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );

  return (
    <>
      <div
        className={`as-root${docked ? " is-docked" : ""}`}
        style={
          docked ? undefined : { left: frame.x, top: frame.y, width: frame.w, height: frame.h }
        }
      >
        <div
          className="as-bar"
          onPointerDown={docked ? undefined : (e) => startGesture(e, "move")}
          onPointerMove={docked ? undefined : onGestureMove}
          onPointerUp={docked ? undefined : endGesture}
          onPointerCancel={docked ? undefined : endGesture}
        >
          <strong>Bread</strong>
          {context.paper && (
            <span className="as-ctx" title={context.paper.title}>
              on “{context.paper.title}”
            </span>
          )}
          {!context.paper && context.author && (
            <span className="as-ctx" title={context.author.name}>
              on {context.author.name}
            </span>
          )}
          {!docked && (
            <button
              className="as-icon"
              onClick={() => onOpenChange(false)}
              onPointerDown={(e) => e.stopPropagation()}
              title="Close — or tap Shift twice"
            >
              ×
            </button>
          )}
        </div>

        <div className="as-tabs" onPointerDown={(e) => e.stopPropagation()}>
          <div className="as-tabs-strip">
            {sessions.map((thread) => (
              <div
                key={thread.id}
                className={`as-tab${thread.id === sessionId ? " is-active" : ""}`}
              >
                <button
                  className="as-tab-label"
                  onClick={() => thread.id !== sessionId && openThread(thread.id)}
                  title={thread.title ?? "New conversation"}
                >
                  {thread.title?.trim() || "New conversation"}
                </button>
                <button
                  className="as-tab-close"
                  onClick={() => closeThread(thread.id)}
                  title="Delete this conversation"
                  aria-label="Delete this conversation"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <button className="as-tab-new" onClick={newThread} title="Start a new conversation">
            +
          </button>
        </div>

        <div className="as-log" ref={scrollRef}>
          {loadingThread && <div className="as-hint">Loading…</div>}
          {configured === false && (
            <div className="as-hint">
              No model is configured yet. Open Settings → AI, add a provider key, and choose a
              model that supports tools.
            </div>
          )}
          {!turns.length && configured !== false && (
            <div className="as-hint">
              Ask about your library. It can read your papers, authors and links, search the
              web, and write notes and homepages. It cannot add papers — it will offer them
              and you decide.
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i} className={`as-turn is-${t.role}`}>
              {t.role === "user" && editing === i && (
                <div className="as-edit">
                  <AutoTextarea
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") setEditing(null);
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        if (t.messageId && editText.trim()) {
                          setEditing(null);
                          send({ text: editText, truncateFromId: t.messageId, keepTurns: i });
                        }
                      }
                    }}
                    minRows={2}
                    maxRows={12}
                    autoFocus
                  />
                  <div className="as-edit-actions">
                    <button onClick={() => setEditing(null)}>Cancel</button>
                    <button
                      className="primary"
                      disabled={!t.messageId || !editText.trim()}
                      onClick={() => {
                        setEditing(null);
                        send({ text: editText, truncateFromId: t.messageId!, keepTurns: i });
                      }}
                    >
                      Send again
                    </button>
                  </div>
                </div>
              )}
              {!!t.tools?.length && toolList(t.tools, `t${i}`)}
              {t.interrupted ? (
                <div className="as-interrupted">Stopped</div>
              ) : t.role === "assistant" ? (
                <Markdown onPaperLink={(identifier) => setProposal({ identifier })}>
                  {t.text}
                </Markdown>
              ) : (
                editing !== i && (
                  <div className="as-text">
                    {t.text}
                    {t.messageId && !busy && (
                      <button
                        className="as-edit-btn"
                        title="Edit and send again — later replies are discarded"
                        onClick={() => {
                          setEditing(i);
                          setEditText(t.text);
                        }}
                      >
                        Edit
                      </button>
                    )}
                  </div>
                )
              )}
            </div>
          ))}

          {busy && (
            <div className="as-turn is-assistant">
              {toolList(activity, "live")}
              <div className="as-thinking">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}

          {error && <div className="as-error">{error}</div>}
        </div>

        {attachment && (
          <div className="as-attach">
            <span className="as-attach-badge" title={attachment.text}>
              Selected text · p{attachment.page}
            </span>
            <span className="as-attach-peek">{attachment.text}</span>
            <button onClick={onClearAttachment} title="Remove the attached passage">
              ×
            </button>
          </div>
        )}

        <div className="as-compose">
          <AutoTextarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={
              busy
                ? "Send to interrupt and ask this instead…"
                : attachment
                  ? "Ask about the selected passage…"
                  : "Ask about your library…"
            }
            minRows={1}
            maxRows={10}
          />
          {busy && !input.trim() ? (
            <button className="as-stop" onClick={stop} title="Stop the assistant">
              Stop
            </button>
          ) : (
            <button
              onClick={() => send()}
              disabled={!input.trim()}
              title={busy ? "Interrupt what it is doing and send this" : undefined}
            >
              {busy ? "Interrupt" : "Send"}
            </button>
          )}
        </div>

        {!docked && (
          <div
            className="as-grip"
            title="Drag to resize"
            onPointerDown={(e) => startGesture(e, "resize")}
            onPointerMove={onGestureMove}
            onPointerUp={endGesture}
            onPointerCancel={endGesture}
          />
        )}
      </div>

      {proposal && (
        <PaperPreviewCard
          identifier={proposal.identifier}
          reason={proposal.reason}
          onClose={() => setProposal(null)}
          onSaved={() => onChanged?.()}
        />
      )}
    </>
  );
}
