/** Typed client for the local Breadcrumbs API. */

const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

/**
 * Read a frozen copy of the library instead of talking to a backend.
 *
 * Set at build time by `./breadcrumbs pages`, which exports every GET response this
 * client makes into a tree of JSON files. There is no server behind the
 * published site, so anything that would change the library is refused here
 * rather than failing later with a network error nobody can act on.
 */
export const STATIC_MODE = import.meta.env.VITE_STATIC === "1";
/** Where those files live, relative to the page. */
const STATIC_BASE = import.meta.env.VITE_STATIC_BASE ?? "./data";

/** A file in the frozen export, by name. */
export function staticUrl(name: string): string {
  return `${STATIC_BASE}/${name}`;
}

/** The published copy's own settings, from pages.json at export time. */
export interface SiteConfig {
  repo_url: string;
  owner: string;
  title: string;
  intro: string;
}

/**
 * Read the frozen library out of its bundles.
 *
 * The export is three files, not one per endpoint: a library this size would
 * otherwise be over a thousand of them, and opening a paper would cost a round
 * trip. Bundled, a static host compresses and caches the lot, and everything
 * after the first click comes from memory.
 *
 * They are fetched on demand and only once. Someone who looks at the timeline
 * and leaves never downloads the papers or the authors.
 */
const bundles = new Map<string, Promise<Record<string, unknown>>>();

function bundle(name: string): Promise<Record<string, unknown>> {
  let pending = bundles.get(name);
  if (!pending) {
    pending = fetch(`${STATIC_BASE}/${name}.json`).then((r) => {
      if (!r.ok) throw new ApiError(`Missing ${name} in this copy of the library`, r.status);
      return r.json() as Promise<Record<string, unknown>>;
    });
    // Not cached on failure: a bundle that failed once because the network
    // blinked should be retried, not remembered as missing for the session.
    pending.catch(() => bundles.delete(name));
    bundles.set(name, pending);
  }
  return pending;
}

/** Where in the bundles an API path lives, or null if it is not published. */
function staticLookup(path: string): { file: string; keys: string[] } | null {
  const route = path.split("?")[0].replace(/^\/api\//, "").replace(/\/$/, "");
  const parts = route.split("/");

  if (parts[0] === "papers" && parts[1]) {
    const which = { references: "references", "cited-by": "cited_by", highlights: "highlights" };
    const leaf = parts[2] ? which[parts[2] as keyof typeof which] : "paper";
    return leaf ? { file: "papers", keys: [parts[1], leaf] } : null;
  }
  if (parts[0] === "authors" && parts[1]) {
    const allowed = ["links", "works", "profile", "facts"];
    const leaf = parts[2] ? (allowed.includes(parts[2]) ? parts[2] : null) : "author";
    return leaf ? { file: "authors", keys: [parts[1], leaf] } : null;
  }

  // Everything the first screen needs, in one file loaded up front.
  const wide: Record<string, string> = {
    timeline: "timeline",
    shelves: "shelves",
    authors: "authorList",
    stats: "stats",
    map: "map",
    index: "index",
  };
  return wide[route] ? { file: "library", keys: [wide[route]] } : null;
}

async function staticRead<T>(path: string): Promise<T> {
  const found = staticLookup(path);
  if (!found) throw new ApiError("Not part of this copy of the library", 404);
  let doc: unknown = await bundle(found.file);
  for (const key of found.keys) {
    doc = (doc as Record<string, unknown> | null)?.[key];
    if (doc === undefined || doc === null) {
      throw new ApiError("Not in this copy of the library", 404);
    }
  }
  return doc as T;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  const method = (init?.method ?? "GET").toUpperCase();

  if (STATIC_MODE) {
    if (method !== "GET") {
      throw new ApiError(
        "This is a published copy of a library, so nothing here can be changed.",
        405,
      );
    }
    return staticRead<T>(path);
  }

  try {
    resp = await fetch(`${BASE}${path}`, {
      ...init,
      headers:
        init?.body instanceof FormData
          ? init?.headers
          : { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      "Cannot reach the backend. Start it with: uv run python -m backend",
      0,
    );
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new ApiError(detail, resp.status);
  }
  return resp.status === 204 ? (undefined as T) : ((await resp.json()) as T);
}

// --- types -----------------------------------------------------------------
export interface Institution {
  id?: number;
  name: string;
  ror?: string | null;
  country_code?: string | null;
  type?: string | null;
}

export interface Author {
  id?: number;
  name: string;
  orcid?: string | null;
  s2_author_id?: string | null;
  openalex_id?: string | null;
  position?: number;
  is_corresponding?: boolean | number;
  affiliation?: string | null;
  country?: string | null;
  institutions?: Institution[];
  thumbnail_url?: string | null;
  profile_description?: string | null;
  profile_status?: "found" | "none" | "error" | null;
}

export interface Topic {
  id?: number;
  name: string;
  subfield?: string | null;
  field?: string | null;
  domain?: string | null;
  score?: number | null;
}

export interface PaperRecord {
  title: string;
  year: number | null;
  month?: number | null;
  venue?: string | null;
  publisher?: string | null;
  abstract?: string | null;
  doi?: string | null;
  arxiv_id?: string | null;
  s2_id?: string | null;
  openalex_id?: string | null;
  pmid?: string | null;
  url?: string | null;
  citation_count?: number | null;
  reference_count?: number | null;
  influential_citation_count?: number | null;
  is_oa?: boolean | number;
  oa_status?: string | null;
  oa_pdf_url?: string | null;
  license?: string | null;
  authors?: Author[];
  topics?: Topic[];
  locations?: {
    is_oa: boolean;
    kind?: string | null;
    name?: string | null;
    pdf_url?: string | null;
    landing_page_url?: string | null;
    version?: string | null;
  }[];
}

export interface Paper extends PaperRecord {
  id: number;
  status: string;
  rating?: number | null;
  importance?: number | null;
  summary?: string | null;
  favorite?: number;
  pdf_path?: string | null;
  note?: string;
  kind?: string | null;
  links?: LinkRow[];
  shelves?: Shelf[];
  unresolved_references?: number;
}

export interface TimelinePaper {
  id: number;
  title: string;
  year: number;
  month?: number | null;
  venue?: string | null;
  status?: string;
  citation_count?: number | null;
  authors: string[];
  kind?: string | null;
  /** 1 when a PDF is stored locally. */
  has_pdf?: number;
  /** 1 when starred. */
  favorite?: number;
}

export interface AuthorRow {
  id: number;
  name: string;
  orcid?: string | null;
  affiliation?: string | null;
  country?: string | null;
  /** 1 when starred. */
  favorite?: number;
  paper_count: number;
  paper_ids: number[];
  first_year: number | null;
  last_year: number | null;
  profile_status?: "found" | "none" | "error" | null;
}

export interface AuthorProfile {
  author_id: number;
  status: "found" | "none" | "error";
  /** The bio shown in the panel, whatever source filled it. */
  bio?: string | null;
  bio_source?: string | null;
  portrait_url?: string | null;
  portrait_full_url?: string | null;
  portrait_source?: string | null;
  custom_image?: string | null;
  /** Structured claims from Wikidata, checkable one by one. */
  facts?: {
    wikidata_id?: string;
    occupations?: string[];
    employers?: string[];
    education?: string[];
    awards?: string[];
    born?: string;
  } | null;
  title?: string | null;
  url?: string | null;
  description?: string | null;
  extract?: string | null;
  thumbnail_url?: string | null;
  image_url?: string | null;
  detail?: string | null;
  fetched_at?: string;
  cached?: boolean;
}

export interface AuthorDetail {
  id: number;
  name: string;
  favorite?: number;
  orcid?: string | null;
  affiliation?: string | null;
  country?: string | null;
  papers: {
    id: number;
    title: string;
    year: number | null;
    venue?: string | null;
    citation_count?: number | null;
    is_corresponding?: number;
  }[];
  institutions: Institution[];
  profile: AuthorProfile | null;
}

export interface AuthorWork {
  openalex_id: string;
  doi: string | null;
  title: string;
  year: number | null;
  type: string | null;
  venue: string | null;
  citation_count: number | null;
  authors_blob: string | null;
  in_library: boolean;
  paper_id: number | null;
}

export interface AuthorStats {
  name: string;
  works_count: number | null;
  cited_by_count: number | null;
  h_index: number | null;
  i10_index: number | null;
  affiliation: string | null;
  topics: string[];
}

export interface LinkRow {
  id: number;
  src_paper_id: number;
  dst_paper_id: number;
  type: string;
  note?: string | null;
  origin?: string;
  confirmed?: number;
  src_title?: string;
  dst_title?: string;
}

export interface Shelf {
  id: number;
  name: string;
  kind: string;
  color?: string | null;
  count?: number;
}

export interface EdgeRecord {
  doi?: string | null;
  s2_id?: string | null;
  openalex_id?: string | null;
  title?: string | null;
  year?: number | null;
  authors_blob?: string | null;
  citation_count?: number | null;
  context?: string | null;
}

export interface Suggestion {
  s2_id: string;
  title: string;
  authors_year?: string | null;
  in_library?: boolean;
}

export interface AutocompleteResponse {
  identifier: { kind: string; value: string } | null;
  results: Suggestion[];
  cached?: boolean;
  degraded?: boolean;
}

export type SourceStatus = "ok" | "empty" | "error" | "skipped" | "pending";

export interface SourcePanel {
  name: string;
  status: SourceStatus;
  detail: string;
  ms: number;
  fields: string[];
  title?: string | null;
  year?: number | null;
  venue?: string | null;
  author_count: number;
  has_abstract: boolean;
  record: Record<string, unknown> | null;
}

export interface WorkVersion {
  openalex_id: string;
  doi: string | null;
  title: string;
  year: number | null;
  type: string | null;
  venue: string | null;
  citation_count: number | null;
}

export interface Preview {
  token: string;
  identifier: { kind: string; value: string };
  already_in_library: boolean;
  existing_paper_id: number | null;
  merged: PaperRecord;
  sources: Record<string, SourcePanel>;
  source_order: string[];
  source_names: string[];
  counts: {
    references: number;
    authors: number;
    would_link_now: number;
    would_link_outgoing: number;
    would_link_incoming: number;
  };
  versions: WorkVersion[];
  earlier_version_year: number | null;
  edge_notes?: Record<string, string>;
  refetchable?: string[];
  sample_references: EdgeRecord[];
  warnings: string[];
}

export interface SaveResult {
  paper_id: number;
  created: boolean;
  title: string;
  sources_used: string[];
  references_stored: number;
  links_created: number;
  pdf_path: string | null;
  warnings: string[];
}

export interface AiProvider {
  key: string;
  label: string;
  setting: string;
  docs: string;
  has_key: boolean;
}

export interface AiModel {
  id: string;
  name: string;
  context: number | null;
  /** Dollars per token, as strings. Shown per million. */
  prompt_price?: string | null;
  completion_price?: string | null;
  tools_known?: boolean;
  /** Whether the provider says this model can reason. null when unpublished. */
  reasoning?: boolean | null;
}

/** One upstream provider serving an OpenRouter model. */
export interface AiEndpoint {
  provider_name: string;
  context: number | null;
  prompt_price?: string | null;
  completion_price?: string | null;
  quantization?: string | null;
  uptime?: number | null;
  /** null when the provider does not publish its parameter list. */
  tools?: boolean | null;
}

export interface ReferenceItem {
  id: number;
  doi: string | null;
  openalex_id: string | null;
  arxiv_id: string | null;
  title: string | null;
  year: number | null;
  authors_blob: string | null;
  citation_count: number | null;
  resolved_paper_id: number | null;
  in_library: boolean;
}

/** A library paper that cites the one being viewed. Read off the far side of
 *  the reference rows, so it is always a paper you hold. */
export interface CitedByItem {
  id: number;
  title: string | null;
  year: number | null;
  venue: string | null;
  citation_count: number | null;
  authors_blob: string | null;
  in_library: true;
}

/** What deleting a paper took with it. */
export interface RemovedPaper {
  title: string | null;
  references: number;
  links: number;
  highlights: number;
  /** Reference rows in other papers that pointed here and are now unresolved. */
  unresolved: number;
  /** Authors removed because no other paper credited them. */
  authors: string[];
  institutions: number;
  files: string[];
}

export interface HighlightRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Highlight {
  id: number;
  paper_id: number;
  page: number;
  /** Fractions of the page, so they land correctly at any zoom. */
  rects: HighlightRect[];
  quoted: string | null;
  comment: string | null;
  color: string | null;
  source?: string | null;
  page_width?: number | null;
  page_height?: number | null;
  created_at: string;
}

export interface PdfOptions {
  paper_id: number;
  is_oa: boolean;
  has_stored_pdf: boolean;
  options: { kind: string; label: string; url: string; auto: boolean }[];
  note: string | null;
}

export interface AiCapabilities {
  tools: { name: string; description: string; is_write: boolean; params: string[] }[];
  skills: { name: string; description: string; size: number }[];
}

export interface MapPoint {
  institution_id: number;
  name: string;
  city: string | null;
  country_code: string | null;
  lat: number;
  lon: number;
  authors: { id: number; name: string }[];
  author_count: number;
  papers: number;
  /** True for the institutions on this author's most recent paper. */
  current: boolean;
}

export interface ChatContext {
  tab?: string;
  paper?: { id: number; title: string; year: number | null } | null;
  author?: { id: number; name: string } | null;
  visible_papers?: { id: number; title: string }[];
  /** In the reader: the page being read, already extracted. */
  reading?: {
    page: number;
    page_count: number;
    text: string;
    /** The paper's section headings, so its shape is known without reading. */
    outline?: { level: number; title: string; page: number }[];
  } | null;
  /** A passage the user attached from the document. */
  selection?: { page: number; text: string } | null;
}

export interface ChatSession {
  id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
  turns: number;
}

export interface ChatMessage {
  id: number | null;
  role: "user" | "assistant";
  content: string;
  tools?: ToolActivity[];
  interrupted?: boolean;
  created_at?: string;
}

export interface ToolActivity {
  name: string;
  args?: Record<string, unknown>;
  status: "running" | "done" | "failed";
  summary?: string;
  isWrite?: boolean;
  /** A trimmed view of what came back, shown when the row is expanded. */
  preview?: string;
}

export interface ChatHandlers {
  onSession?: (id: number) => void;
  onStart?: (d: { user_message_id: number; model: string }) => void;
  onText?: (chunk: string) => void;
  onToolCall?: (t: { id: string; name: string; args: Record<string, unknown> }) => void;
  onToolResult?: (r: {
    id: string; name: string; summary: string; is_write: boolean;
    error?: string; preview?: string;
  }) => void;
  onProposal?: (p: { identifier: string; reason?: string }) => void;
  /** The assistant asking the reader to scroll somewhere. */
  onReveal?: (r: {
    paper_id: number;
    page: number;
    rects: HighlightRect[];
    quote?: string;
  }) => void;
  onMessage?: (m: { text: string; rounds: number }) => void;
  onError?: (message: string) => void;
  onDone?: (d: { rounds: number }) => void;
}

/**
 * One assistant turn, streamed. Returns a function that aborts it.
 *
 * POST rather than EventSource, because the turn carries the message and the
 * current UI context; the response is still an SSE stream, parsed here.
 */
export function chatStream(
  body: {
    session_id: number | null;
    message: string;
    context: ChatContext;
    truncate_from_id?: number | null;
  },
  h: ChatHandlers,
): () => void {
  const controller = new AbortController();

  (async () => {
    let resp: Response;
    try {
      resp = await fetch(`${BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        h.onError?.("Cannot reach the backend.");
      }
      return;
    }
    if (!resp.ok || !resp.body) {
      h.onError?.(`The assistant failed to start (HTTP ${resp.status}).`);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let event = "";

    const handle = (name: string, raw: string) => {
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(raw);
      } catch {
        return;
      }
      switch (name) {
        case "session": h.onSession?.(data.session_id as number); break;
        case "start": h.onStart?.(data as never); break;
        case "text": h.onText?.(data.text as string); break;
        case "tool_call": h.onToolCall?.(data as never); break;
        case "tool_result": h.onToolResult?.(data as never); break;
        case "proposal": h.onProposal?.(data as never); break;
        case "reveal": h.onReveal?.(data as never); break;
        case "message": h.onMessage?.(data as never); break;
        case "error": h.onError?.(data.message as string); break;
        case "done": h.onDone?.(data as never); break;
      }
    };

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE frames are separated by a blank line; a frame may span reads.
        let split: number;
        while ((split = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          event = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7);
            else if (line.startsWith("data: ")) handle(event, line.slice(6));
          }
        }
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        h.onError?.(String(err));
      }
    }
  })();

  return () => controller.abort();
}

export interface AuthorLink {
  kind: string;
  status: "found" | "none" | "error";
  url: string | null;
  title: string | null;
  snippet: string | null;
  verified: number;
  found_by: string | null;
  detail: string | null;
  fetched_at: string;
}

export interface Settings {
  contact_email: string;
  openalex_key: string;
  openalex_key_set?: boolean;
  semantic_scholar_key: string;
  semantic_scholar_key_set?: boolean;
  fetch_references: string;
  institution_proxy: string;
  ai_openrouter_key: string;
  ai_openrouter_key_set?: boolean;
  ai_gemini_key: string;
  ai_gemini_key_set?: boolean;
  ai_deepseek_key: string;
  ai_deepseek_key_set?: boolean;
  search_provider: string;
  search_api_key: string;
  search_api_key_set?: boolean;
  assistant_provider: string;
  assistant_model: string;
  assistant_max_rounds: string;
  assistant_thinking: string;
  /** OpenRouter only: pinned upstream provider, blank for automatic. */
  assistant_route: string;
}

export type SourceProbe = Record<string, { ok: boolean; detail: string; keyed?: boolean }>;

// --- calls -----------------------------------------------------------------
export interface StreamHandlers {
  onStart?: (d: {
    identifier: { kind: string; value: string };
    sources: SourcePanel[];
    source_order: string[];
    warnings: string[];
  }) => void;
  onSource?: (panel: SourcePanel) => void;
  onMerged?: (d: { merged: PaperRecord }) => void;
  onVersions?: (d: {
    versions: WorkVersion[]; current_year: number | null; error?: string | null;
  }) => void;
  onEdgesPending?: (which: "references") => void;
  onEdges?: (d: {
    which: "references";
    count: number;
    note: string;
    sample: EdgeRecord[];
  }) => void;
  onDone?: (preview: Preview) => void;
  onError?: (message: string) => void;
}

/**
 * Opens a server-sent-events lookup. Returns a function that closes it.
 * Every handler is optional; unknown events are ignored.
 */
export function previewStream(
  identifier: string,
  title: string | undefined,
  h: StreamHandlers,
): () => void {
  const params = new URLSearchParams({ identifier });
  if (title) params.set("title", title);
  const es = new EventSource(`${BASE}/api/preview/stream?${params}`);
  let finished = false;

  const on = (name: string, fn: (data: unknown) => void) =>
    es.addEventListener(name, (e) => {
      try {
        fn(JSON.parse((e as MessageEvent).data));
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    });

  on("start", (d) => h.onStart?.(d as Parameters<NonNullable<StreamHandlers["onStart"]>>[0]));
  on("source", (d) => h.onSource?.(d as SourcePanel));
  on("merged", (d) => h.onMerged?.(d as { merged: PaperRecord }));
  on("versions", (d) => h.onVersions?.(d as never));
  on("edges_pending", (d) =>
    h.onEdgesPending?.((d as { which: "references" }).which),
  );
  on("edges", (d) => h.onEdges?.(d as Parameters<NonNullable<StreamHandlers["onEdges"]>>[0]));
  on("done", (d) => {
    finished = true;
    h.onDone?.(d as Preview);
    es.close();
  });
  on("error", (d) => {
    finished = true;
    h.onError?.((d as { message: string }).message);
    es.close();
  });

  // Fires on network failure too, so only report it if no terminal event came.
  es.onerror = () => {
    if (!finished) {
      finished = true;
      h.onError?.("Lost the connection to the backend during the lookup.");
      es.close();
    }
  };

  return () => {
    finished = true;
    es.close();
  };
}

/** One route to a file, either known up front or discovered mid-search. */
export interface PdfCandidate {
  url: string;
  origin: string;
  kind: string;
  label: string;
  note: string | null;
}

/** A source being consulted, and how it went. */
export interface PdfStage {
  name: string;
  label: string;
  status: "running" | "ok" | "error" | "skipped";
  detail: string;
}

export interface PdfSearchHandlers {
  onStart?: (d: {
    paper_id: number;
    title: string;
    has_pdf: boolean;
    identifiers: Record<string, string | null>;
  }) => void;
  onStage?: (d: PdfStage) => void;
  onCandidate?: (d: PdfCandidate) => void;
  onTrying?: (d: { count: number }) => void;
  onAttempt?: (d: {
    index: number;
    total: number;
    url: string;
    label: string;
    note: string | null;
    origin: string;
  }) => void;
  onAttemptResult?: (d: {
    index: number;
    ok: boolean;
    detail: string;
    followed?: number;
    /** True when a bot check answered instead of the file. */
    blocked?: boolean;
    url?: string;
  }) => void;
  onDone?: (d: {
    ok: boolean;
    paper_id: number;
    pdf_path?: string;
    url?: string;
    tried: number;
    bytes?: number;
    message: string;
    /** Links that a bot check refused. They work in a browser. */
    blocked?: PdfCandidate[];
    /** Every link the search tried, in the order it tried them. */
    attempted?: PdfCandidate[];
  }) => void;
  onError?: (message: string) => void;
}

/**
 * Watch a PDF hunt for one paper. Returns a function that closes the stream.
 *
 * The search is deliberate — nothing looks for a file until this is called —
 * so the caller is expected to be showing the steps to someone watching.
 */
export function pdfSearchStream(paperId: number, h: PdfSearchHandlers): () => void {
  const es = new EventSource(`${BASE}/api/papers/${paperId}/pdf-search/stream`);
  let finished = false;

  const on = (name: string, fn: (data: unknown) => void) =>
    es.addEventListener(name, (e) => {
      try {
        fn(JSON.parse((e as MessageEvent).data));
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    });

  on("start", (d) => h.onStart?.(d as Parameters<NonNullable<PdfSearchHandlers["onStart"]>>[0]));
  on("stage", (d) => h.onStage?.(d as PdfStage));
  on("candidate", (d) => h.onCandidate?.(d as PdfCandidate));
  on("trying", (d) => h.onTrying?.(d as { count: number }));
  on("attempt", (d) =>
    h.onAttempt?.(d as Parameters<NonNullable<PdfSearchHandlers["onAttempt"]>>[0]),
  );
  on("attempt_result", (d) =>
    h.onAttemptResult?.(d as Parameters<NonNullable<PdfSearchHandlers["onAttemptResult"]>>[0]),
  );
  on("done", (d) => {
    finished = true;
    h.onDone?.(d as Parameters<NonNullable<PdfSearchHandlers["onDone"]>>[0]);
    es.close();
  });
  on("error", (d) => {
    finished = true;
    h.onError?.((d as { message: string }).message);
    es.close();
  });

  // Fires on network failure too, so only report it if no terminal event came.
  es.onerror = () => {
    if (!finished) {
      finished = true;
      h.onError?.("Lost the connection to the backend during the search.");
      es.close();
    }
  };

  return () => {
    finished = true;
    es.close();
  };
}

export const api = {
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (body: Record<string, unknown>) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  testSources: () => request<SourceProbe>("/api/settings/test", { method: "POST" }),

  preview: (identifier: string, title?: string) =>
    request<Preview>("/api/preview", {
      method: "POST",
      body: JSON.stringify({ identifier, title }),
    }),
  refetch: (token: string, source: string) =>
    request<Preview>("/api/preview/refetch", {
      method: "POST",
      body: JSON.stringify({ token, source }),
    }),
  save: (token: string, shelfIds: number[] = []) =>
    request<SaveResult>("/api/papers", {
      method: "POST",
      body: JSON.stringify({ token, shelf_ids: shelfIds }),
    }),
  autocomplete: (q: string, signal?: AbortSignal) =>
    request<AutocompleteResponse>(`/api/autocomplete?q=${encodeURIComponent(q)}`, { signal }),
  search: (q: string) =>
    request<{ results: (PaperRecord & { in_library: boolean })[] }>(
      `/api/search?q=${encodeURIComponent(q)}`,
    ),

  papers: () => request<{ papers: Paper[] }>("/api/papers"),
  paper: (id: number) => request<Paper>(`/api/papers/${id}`),
  patchPaper: (id: number, body: Record<string, unknown>) =>
    request<Paper>(`/api/papers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** Removes the paper and anything that existed only for it: its references,
   *  links, highlights, the stored PDF, and any author or institution no other
   *  paper still uses. `removed` says what went. */
  deletePaper: (id: number) =>
    request<{ status: string; removed: RemovedPaper }>(`/api/papers/${id}`, { method: "DELETE" }),
  /** Drops one row from a paper's reference list. The referenced paper itself,
   *  if you hold it, is untouched. */
  deleteReference: (paperId: number, referenceId: number) =>
    request<{ status: string; title: string | null; links_removed: number }>(
      `/api/papers/${paperId}/references/${referenceId}`,
      { method: "DELETE" },
    ),

  timeline: () => request<{ papers: TimelinePaper[]; links: LinkRow[] }>("/api/timeline"),
  authors: () => request<{ authors: AuthorRow[] }>("/api/authors"),
  author: (id: number) => request<AuthorDetail>(`/api/authors/${id}`),
  patchAuthor: (id: number, body: { favorite?: boolean }) =>
    request<AuthorDetail>(`/api/authors/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  aiProviders: () =>
    request<{ providers: AiProvider[]; search: { provider: string; has_key: boolean } }>(
      "/api/ai/providers",
    ),
  aiCapabilities: () => request<AiCapabilities>("/api/ai/capabilities"),
  aiModels: (provider: string) =>
    request<{ models: AiModel[] }>(`/api/ai/models?provider=${encodeURIComponent(provider)}`),
  aiModelEndpoints: (provider: string, model: string) =>
    request<{ endpoints: AiEndpoint[] }>(
      `/api/ai/model-endpoints?provider=${encodeURIComponent(provider)}` +
        `&model=${encodeURIComponent(model)}`,
    ),
  chatSessions: () => request<{ sessions: ChatSession[] }>("/api/chat/sessions"),
  renameChatSession: (id: number, title: string) =>
    request<ChatSession>(`/api/chat/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteChatSession: (id: number) =>
    request<{ status: string }>(`/api/chat/sessions/${id}`, { method: "DELETE" }),
  newChatSession: () =>
    request<{ session_id: number }>("/api/chat/sessions", { method: "POST" }),
  chatHistory: (id: number) =>
    request<{ messages: ChatMessage[] }>(`/api/chat/sessions/${id}`),
  authorLinks: (id: number) =>
    request<{ links: Record<string, AuthorLink> }>(`/api/authors/${id}/links`),
  affiliationMap: (scope: string, id?: number | null) =>
    request<{ scope: string; points: MapPoint[]; unplaced: number }>(
      `/api/map?scope=${scope}${id != null ? `&id=${id}` : ""}`,
    ),
  authorFacts: (id: number) =>
    request<{
      author_id: number;
      name: string;
      sources: string[];
      orcid?: {
        id: string;
        biography?: string | null;
        urls?: { name: string | null; url: string }[];
        keywords?: string[];
        employments?: {
          organisation: string | null;
          role: string | null;
          city: string | null;
          country: string | null;
          start_year: number | null;
          end_year: number | null;
        }[];
      };
      openalex?: AuthorStats;
    }>(`/api/authors/${id}/facts`),
  authorWorks: (id: number, limit = 100) =>
    request<{ works: AuthorWork[]; profile: AuthorStats | null; detail: string | null }>(
      `/api/authors/${id}/works?limit=${limit}`,
    ),
  authorPhotoUrl: (id: number) => `${BASE}/api/authors/${id}/photo`,
  uploadAuthorPhoto: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ ok: boolean; path: string }>(`/api/authors/${id}/photo`, {
      method: "POST",
      body: form,
    });
  },
  setAuthorPhotoUrl: (id: number, url: string) =>
    request<{ ok: boolean; path: string }>(`/api/authors/${id}/photo-url`, {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  deleteAuthorPhoto: (id: number) =>
    request<{ ok: boolean }>(`/api/authors/${id}/photo`, { method: "DELETE" }),
  authorProfile: (id: number, refresh = false) =>
    request<AuthorProfile>(`/api/authors/${id}/profile${refresh ? "?refresh=true" : ""}`),
  shelves: () => request<{ shelves: Shelf[] }>("/api/shelves"),
  stats: () => request<Record<string, unknown>>("/api/stats"),
  wipeLibrary: (confirm: string) =>
    request<{ status: string; deleted: Record<string, number>; files_removed: number }>(
      "/api/library/wipe",
      { method: "POST", body: JSON.stringify({ confirm }) },
    ),
  pdfUrl: (id: number) => `${BASE}/api/papers/${id}/pdf`,
  pdfOptions: (id: number) => request<PdfOptions>(`/api/papers/${id}/pdf-options`),
  pdfOutline: (paperId: number) =>
    request<{ outline: { level: number; title: string; page: number }[]; note: string | null }>(
      `/api/papers/${paperId}/outline`,
    ),
  pdfPage: (paperId: number, page: number) =>
    request<{ pages: { page: number; text: string }[]; page_count: number }>(
      `/api/papers/${paperId}/pdf-text?page=${page}`,
    ),
  references: (paperId: number) =>
    request<{ items: ReferenceItem[]; total: number; in_library: number }>(
      `/api/papers/${paperId}/references`,
    ),
  /** Papers in the library whose bibliography names this one. Library-only by
   *  design: the full citing list is unbounded and is never fetched. */
  citedBy: (paperId: number) =>
    request<{ items: CitedByItem[]; total: number }>(`/api/papers/${paperId}/cited-by`),
  highlights: (paperId: number) =>
    request<{ highlights: Highlight[] }>(`/api/papers/${paperId}/highlights`),
  createHighlight: (paperId: number, body: Record<string, unknown>) =>
    request<Highlight>(`/api/papers/${paperId}/highlights`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateHighlight: (id: number, body: Record<string, unknown>) =>
    request<Highlight>(`/api/highlights/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteHighlight: (id: number) =>
    request<{ status: string }>(`/api/highlights/${id}`, { method: "DELETE" }),
  uploadPdf: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ paper_id: number; pdf_path: string }>(`/api/papers/${id}/pdf`, {
      method: "POST",
      body: form,
    });
  },
};
