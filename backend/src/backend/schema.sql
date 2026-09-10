-- Breadcrumbs schema
-- One SQLite file per library. All user-owned rows carry owner_id so a future
-- multi-user/sync mode is not blocked; single-user mode always uses owner_id = 1.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- papers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS papers (
    id           INTEGER PRIMARY KEY,
    owner_id     INTEGER NOT NULL DEFAULT 1,

    -- bibliographic
    title        TEXT    NOT NULL,
    year         INTEGER,                  -- nullable: preprints w/o a year
    month        INTEGER,                  -- 1-12, for finer timeline placement
    venue        TEXT,                     -- "NeurIPS", "Nature", "arXiv"
    volume       TEXT,
    pages        TEXT,
    publisher    TEXT,
    abstract     TEXT,

    -- external identifiers (all nullable, all unique when present)
    doi          TEXT UNIQUE,
    arxiv_id     TEXT UNIQUE,
    s2_id        TEXT UNIQUE,              -- Semantic Scholar paperId
    openalex_id  TEXT UNIQUE,
    pmid         TEXT UNIQUE,
    url          TEXT,                     -- landing page

    -- local artefacts
    pdf_path     TEXT,                     -- relative to library root
    note_path    TEXT,                     -- markdown file, relative to root

    -- your own study metadata
    status       TEXT    NOT NULL DEFAULT 'unread'
                 CHECK (status IN ('unread','queued','reading','skimmed','read','archived')),
    rating       INTEGER CHECK (rating BETWEEN 0 AND 5),
    importance   INTEGER CHECK (importance BETWEEN 0 AND 5), -- your own weighting

    -- Vertical position on the timeline, set by dragging a card. Continuous
    -- rather than a row index: 0 sits on the axis, 1.5 halfway to the next
    -- row, negative below the axis. NULL means "pack me automatically".
    -- Stored so an arrangement you build by hand survives reloading.
    lane         REAL,
    summary      TEXT,                     -- one-line "what this paper does"

    -- upstream counts, refreshed on sync, never authoritative
    citation_count  INTEGER,
    reference_count INTEGER,
    influential_citation_count INTEGER,     -- S2 only

    -- open access, from Unpaywall / OpenAlex / S2
    is_oa        INTEGER NOT NULL DEFAULT 0,
    oa_status    TEXT,                      -- 'gold' | 'green' | 'closed' ...
    oa_pdf_url   TEXT,
    license      TEXT,

    -- which sources have been merged into this row, and when
    sources      TEXT,                      -- JSON: ["openalex","s2","crossref"]
    synced_at    TEXT,

    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_papers_year   ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
CREATE INDEX IF NOT EXISTS idx_papers_owner  ON papers(owner_id);

-- full-text search over title + abstract + your summary
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, summary,
    content='papers', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS papers_fts_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, summary)
    VALUES (new.id, new.title, new.abstract, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS papers_fts_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, summary)
    VALUES ('delete', old.id, old.title, old.abstract, old.summary);
END;
CREATE TRIGGER IF NOT EXISTS papers_fts_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, summary)
    VALUES ('delete', old.id, old.title, old.abstract, old.summary);
    INSERT INTO papers_fts(rowid, title, abstract, summary)
    VALUES (new.id, new.title, new.abstract, new.summary);
END;

-- ---------------------------------------------------------------------------
-- authors
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS authors (
    id             INTEGER PRIMARY KEY,
    owner_id       INTEGER NOT NULL DEFAULT 1,

    name           TEXT NOT NULL,
    normalized_name TEXT,                  -- lowercased, for dedupe/matching
    s2_author_id   TEXT UNIQUE,
    openalex_id    TEXT UNIQUE,
    orcid          TEXT UNIQUE,
    homepage       TEXT,

    -- "know them" fields you asked for
    bio            TEXT,                   -- free text you write yourself
    affiliation    TEXT,                   -- current/last known institution
    city           TEXT,
    country        TEXT,
    lab            TEXT,                   -- group name, e.g. "Bengio Lab"

    h_index        INTEGER,
    paper_count    INTEGER,

    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_authors_norm ON authors(normalized_name);

-- Every external id an author has ever answered to, including ids inherited
-- from a duplicate that was merged away.
--
-- The id columns on `authors` are single-valued and UNIQUE, which is fine for
-- one record but wrong for a person: OpenAlex routinely splits a prolific
-- author across several entities. Geoffrey Hinton has two, holding 36 and 378
-- works, and neither carries an ORCID to tie them together. Without this table
-- a merge would have to discard one of those ids, and the next paper arriving
-- under it would recreate the duplicate.
--
-- Keyed on (kind, value) so one id can never point at two authors.
CREATE TABLE IF NOT EXISTS author_ids (
    author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('orcid','openalex_id','s2_author_id')),
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (kind, value)
);
CREATE INDEX IF NOT EXISTS idx_author_ids_author ON author_ids(author_id);

-- an author's affiliation changes over time; keep the history for context
CREATE TABLE IF NOT EXISTS author_affiliations (
    id          INTEGER PRIMARY KEY,
    author_id   INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    institution TEXT NOT NULL,
    city        TEXT,
    country     TEXT,
    start_year  INTEGER,
    end_year    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_affil_author ON author_affiliations(author_id);

-- institutions, keyed by ROR id. OpenAlex returns these per authorship, which
-- is where the "where are these people" answer comes from.
CREATE TABLE IF NOT EXISTS institutions (
    id           INTEGER PRIMARY KEY,
    ror          TEXT UNIQUE,
    openalex_id  TEXT UNIQUE,
    name         TEXT NOT NULL,
    country_code TEXT,                     -- ISO-2, e.g. 'GB'
    type         TEXT,                     -- 'education' | 'company' | 'facility' ...
    lat          REAL,
    lon          REAL
);

-- an authorship can carry several institutions, so this hangs off paper_authors
CREATE TABLE IF NOT EXISTS authorship_institutions (
    paper_id       INTEGER NOT NULL,
    author_id      INTEGER NOT NULL,
    institution_id INTEGER NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, author_id, institution_id),
    FOREIGN KEY (paper_id, author_id)
        REFERENCES paper_authors(paper_id, author_id) ON DELETE CASCADE
);

-- many-to-many, ordered: paper 12 has author 5 in position 0 (first author)
CREATE TABLE IF NOT EXISTS paper_authors (
    paper_id  INTEGER NOT NULL REFERENCES papers(id)  ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,            -- 0-based author order
    is_corresponding INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (paper_id, author_id)
);

CREATE INDEX IF NOT EXISTS idx_pa_author ON paper_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_pa_paper  ON paper_authors(paper_id);

-- ---------------------------------------------------------------------------
-- links between papers  (this is the "citations table with paper FKs" you asked
-- about, generalised: a citation is just one edge kind among several)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS link_types (
    id       TEXT PRIMARY KEY,             -- 'cites', 'extends', ...
    label    TEXT NOT NULL,
    color    TEXT,
    directed INTEGER NOT NULL DEFAULT 1,
    builtin  INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO link_types (id, label, color, directed, builtin) VALUES
    ('cites',       'cites',                 '#94a3b8', 1, 1),
    ('extends',     'extends',               '#3b82f6', 1, 0),
    ('uses_method', 'uses method of',        '#8b5cf6', 1, 0),
    ('uses_data',   'uses data/benchmark of','#f59e0b', 1, 0),
    ('contradicts', 'contradicts',           '#ef4444', 1, 0),
    ('replicates',  'replicates',            '#10b981', 1, 0),
    ('background',  'background for',        '#6b7280', 1, 0),
    ('related',     'related to',            '#64748b', 0, 0);

CREATE TABLE IF NOT EXISTS links (
    id            INTEGER PRIMARY KEY,
    owner_id      INTEGER NOT NULL DEFAULT 1,

    src_paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    dst_paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    type          TEXT    NOT NULL REFERENCES link_types(id),

    note          TEXT,                    -- *why* this link exists, in your words

    -- Left for your own use. Nothing populates these: third-party citation
    -- classification is deliberately not imported, so link typing stays a
    -- judgement you make. Available if you build your own extraction later.
    context       TEXT,
    intent        TEXT,

    -- 'auto' = pulled from an API, 'manual' = you asserted it. Auto links are
    -- suggestions; manual links are the curated graph.
    origin        TEXT NOT NULL DEFAULT 'manual'
                  CHECK (origin IN ('auto','manual')),
    confirmed     INTEGER NOT NULL DEFAULT 0,  -- you accepted an auto link

    created_at    TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE (src_paper_id, dst_paper_id, type),
    CHECK (src_paper_id <> dst_paper_id)
);

CREATE INDEX IF NOT EXISTS idx_links_src  ON links(src_paper_id);
CREATE INDEX IF NOT EXISTS idx_links_dst  ON links(dst_paper_id);
CREATE INDEX IF NOT EXISTS idx_links_type ON links(type);

-- Identifiers of the papers a library paper cites, where the cited side may not
-- be in the library yet. Nothing here creates a paper row: adding papers is
-- always a deliberate manual act. This table exists so that when you later add
-- paper Y, every already-stored mention of Y resolves into a real link without
-- re-fetching anything.
--
-- Only one direction is stored: what a paper cites. A bibliography is finite
-- and printed in the paper itself, so it can be fetched completely. "Who cites
-- this" is unbounded — tens of thousands for a well-known paper — and any cap
-- on it yields an arbitrary slice, so it is not fetched at all. Nothing is lost
-- for papers you hold: if you have both sides, the citing paper's own reference
-- list supplies the edge, and resolve_links checks both sides on every add.
CREATE TABLE IF NOT EXISTS pending_links (
    id            INTEGER PRIMARY KEY,
    from_paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,

    -- identifiers of the other paper; at least one is non-null
    doi           TEXT,
    s2_id         TEXT,
    openalex_id   TEXT,
    arxiv_id      TEXT,

    -- denormalised display data, so the inbox can show something useful
    title          TEXT,
    year           INTEGER,
    authors_blob   TEXT,                   -- "T. Lillicrap, A. Santoro"
    citation_count INTEGER,

    -- set once the other side exists in `papers`
    resolved_paper_id INTEGER REFERENCES papers(id) ON DELETE SET NULL,
    resolved_at       TEXT,

    fetched_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pend_from     ON pending_links(from_paper_id);
CREATE INDEX IF NOT EXISTS idx_pend_doi      ON pending_links(doi)         WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pend_s2       ON pending_links(s2_id)       WHERE s2_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pend_oa       ON pending_links(openalex_id) WHERE openalex_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pend_arxiv    ON pending_links(arxiv_id)    WHERE arxiv_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pend_resolved ON pending_links(resolved_paper_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pend_doi
    ON pending_links(from_paper_id, doi)   WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_pend_s2
    ON pending_links(from_paper_id, s2_id) WHERE s2_id IS NOT NULL AND doi IS NULL;

-- ---------------------------------------------------------------------------
-- cached author biographies
-- ---------------------------------------------------------------------------
-- One row per author, written whether or not a page was found. Caching the
-- misses matters as much as caching the hits: most researchers have no
-- Wikipedia page, and without this every visit would re-run a search that is
-- always going to fail.
CREATE TABLE IF NOT EXISTS author_profiles (
    author_id      INTEGER PRIMARY KEY REFERENCES authors(id) ON DELETE CASCADE,
    status         TEXT NOT NULL CHECK (status IN ('found','none','error')),
    source         TEXT NOT NULL DEFAULT 'wikipedia',
    title          TEXT,
    url            TEXT,
    description    TEXT,               -- one-line gloss, e.g. "Finnish computer scientist"
    extract        TEXT,               -- opening paragraph
    thumbnail_url  TEXT,
    image_url      TEXT,
    detail         TEXT,               -- why nothing was found, when nothing was
    fetched_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- assistant conversations and what it finds
-- ---------------------------------------------------------------------------
-- There are no per-task prompts. One assistant holds every capability as a
-- tool, so "find this author's homepage" is a request in conversation rather
-- than a separate configured job.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         INTEGER PRIMARY KEY,
    title      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content    TEXT,
    tool_calls TEXT,               -- JSON, when the assistant asked for tools
    tool_call_id TEXT,             -- which call a 'tool' row answers
    tool_name  TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_messages ON chat_messages(session_id, id);

-- Links the assistant finds and verifies for an author. Misses are recorded
-- too, so a fruitless search is not repeated on every visit.
CREATE TABLE IF NOT EXISTS author_links (
    id         INTEGER PRIMARY KEY,
    author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,               -- 'homepage' | 'wikipedia' | ...
    status     TEXT NOT NULL CHECK (status IN ('found','none','error')),
    url        TEXT,
    title      TEXT,
    snippet    TEXT,
    verified   INTEGER NOT NULL DEFAULT 0,  -- the page was fetched and names the author
    found_by   TEXT,                        -- 'wikidata' or 'provider/model'
    detail     TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(author_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_author_links ON author_links(author_id);

-- ---------------------------------------------------------------------------
-- application settings (API keys, contact email, behaviour toggles)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('contact_email',        ''),   -- used for API polite pools; required
    ('semantic_scholar_key', ''),   -- optional, lifts the 1 req/s shared limit
    ('fetch_references',     '1'),  -- pull "what this cites" on add
    ('library_path',         ''),
    -- Your institution's EZproxy/OpenAthens prefix, e.g.
    -- 'https://login.ezproxy.your-uni.edu/login?url='. Used to build a link
    -- that opens the publisher copy through your own subscription.
    ('institution_proxy',    ''),

    -- AI providers. Keys are write-only over the API: they are never sent back
    -- to the browser, only a flag saying whether one is stored.
    ('ai_openrouter_key',    ''),
    ('ai_anthropic_key',     ''),
    ('ai_openai_key',        ''),
    ('ai_gemini_key',        ''),
    ('ai_deepseek_key',      ''),

    -- Web search, kept separate from the model provider so any task can run on
    -- any model. Provider-native search would tie the two together.
    ('search_provider',      'brave'),   -- 'brave' | 'tavily'
    ('search_api_key',       ''),

    -- One model runs the assistant. It must support tool calling.
    ('assistant_provider',   ''),
    ('assistant_model',      ''),
    ('assistant_max_rounds', '12'),
    -- off | low | medium | high. Sent as reasoning_effort; models that cannot
    -- reason simply have it dropped on retry.
    ('assistant_thinking',   'off');

-- ---------------------------------------------------------------------------
-- shelves (your ordered bookshelf) and tags
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shelves (
    id          INTEGER PRIMARY KEY,
    owner_id    INTEGER NOT NULL DEFAULT 1,
    name        TEXT NOT NULL,
    description TEXT,
    color       TEXT,
    kind        TEXT NOT NULL DEFAULT 'topic'
                CHECK (kind IN ('topic','supplement','dataset','background','method')),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (owner_id, name)
);

INSERT OR IGNORE INTO shelves (id, name, kind, color, sort_order) VALUES
    (1, 'Core',       'topic',      '#3b82f6', 0),
    (2, 'Supplement', 'supplement', '#f59e0b', 1),
    (3, 'Datasets',   'dataset',    '#10b981', 2),
    (4, 'Background', 'background', '#6b7280', 3);

CREATE TABLE IF NOT EXISTS paper_shelves (
    paper_id   INTEGER NOT NULL REFERENCES papers(id)  ON DELETE CASCADE,
    shelf_id   INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,   -- manual ordering within a shelf
    PRIMARY KEY (paper_id, shelf_id)
);

CREATE INDEX IF NOT EXISTS idx_ps_shelf ON paper_shelves(shelf_id);

CREATE TABLE IF NOT EXISTS tags (
    id       INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL DEFAULT 1,
    name     TEXT NOT NULL,
    color    TEXT,
    UNIQUE (owner_id, name)
);

-- OpenAlex's own topic taxonomy. Kept separate from `tags`, which are yours.
CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    openalex_id TEXT UNIQUE,
    name        TEXT NOT NULL,
    subfield    TEXT,
    field       TEXT,
    domain      TEXT
);

CREATE TABLE IF NOT EXISTS paper_topics (
    paper_id INTEGER NOT NULL REFERENCES papers(id)  ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id)  ON DELETE CASCADE,
    score    REAL,
    PRIMARY KEY (paper_id, topic_id)
);

CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (paper_id, tag_id)
);

-- ---------------------------------------------------------------------------
-- notes and highlights
-- ---------------------------------------------------------------------------
-- A note attaches to exactly one of: paper, author, link, shelf.
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    owner_id   INTEGER NOT NULL DEFAULT 1,
    paper_id   INTEGER REFERENCES papers(id)  ON DELETE CASCADE,
    author_id  INTEGER REFERENCES authors(id) ON DELETE CASCADE,
    link_id    INTEGER REFERENCES links(id)   ON DELETE CASCADE,
    shelf_id   INTEGER REFERENCES shelves(id) ON DELETE CASCADE,
    body       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((paper_id IS NOT NULL) + (author_id IS NOT NULL)
         + (link_id IS NOT NULL) + (shelf_id IS NOT NULL) = 1)
);

CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes(paper_id);

CREATE TABLE IF NOT EXISTS highlights (
    id         INTEGER PRIMARY KEY,
    owner_id   INTEGER NOT NULL DEFAULT 1,
    paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page       INTEGER NOT NULL,
    rects      TEXT NOT NULL,              -- JSON array of {x,y,w,h} in PDF units
    quoted     TEXT,                       -- selected text
    comment    TEXT,
    color      TEXT DEFAULT '#fde047',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hl_paper ON highlights(paper_id);

-- ---------------------------------------------------------------------------
-- reading log: when you actually engaged with a paper
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reading_events (
    id        INTEGER PRIMARY KEY,
    paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL CHECK (kind IN ('opened','skimmed','read','revisited')),
    at        TEXT NOT NULL DEFAULT (datetime('now')),
    minutes   INTEGER
);

-- ---------------------------------------------------------------------------
-- convenience views
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_paper_authors AS
SELECT p.id AS paper_id,
       group_concat(a.name, ', ') AS author_list
FROM papers p
JOIN paper_authors pa ON pa.paper_id = p.id
JOIN authors a        ON a.id = pa.author_id
GROUP BY p.id;
