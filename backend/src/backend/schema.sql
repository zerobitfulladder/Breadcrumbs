-- Breadcrumbs schema
--
-- One SQLite file per library, and this file is the whole of its definition:
-- a library is created from it and nothing alters it afterwards. A change to
-- the schema means building a new library from the new file and copying the
-- data across, not patching an old one in place.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- papers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS papers (
    id           INTEGER PRIMARY KEY,

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

    pdf_path     TEXT,                     -- relative to the library root

    -- your own marks
    status       TEXT    NOT NULL DEFAULT 'unread'
                 CHECK (status IN ('unread','queued','reading','skimmed','read','archived')),
    -- Starred by hand. On both papers and authors because the two lists are
    -- browsed the same way and "the ones I care about" means the same in each.
    favorite     INTEGER NOT NULL DEFAULT 0,

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

-- full-text search over title and abstract
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract,
    content='papers', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS papers_fts_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.id, new.title, new.abstract);
END;
CREATE TRIGGER IF NOT EXISTS papers_fts_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.id, old.title, old.abstract);
END;
-- Only when the indexed text changes: starring a paper or setting its status
-- leaves the search index alone.
CREATE TRIGGER IF NOT EXISTS papers_fts_au AFTER UPDATE OF title, abstract ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.id, old.title, old.abstract);
    INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.id, new.title, new.abstract);
END;

-- ---------------------------------------------------------------------------
-- authors and where they worked
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS authors (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT,                  -- lowercased, for dedupe/matching
    s2_author_id    TEXT UNIQUE,
    openalex_id     TEXT UNIQUE,
    orcid           TEXT UNIQUE,
    favorite        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
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

-- Institutions, keyed by ROR id. OpenAlex returns these per authorship, which
-- is where the "where are these people" answer comes from. The place is filled
-- from OpenAlex on demand rather than at import, because most institutions are
-- never looked at on a map; `geo_checked` records the attempt, so one with no
-- known location is not looked up again.
CREATE TABLE IF NOT EXISTS institutions (
    id           INTEGER PRIMARY KEY,
    ror          TEXT UNIQUE,
    openalex_id  TEXT UNIQUE,
    name         TEXT NOT NULL,
    country_code TEXT,                     -- ISO-2, e.g. 'GB'
    type         TEXT,                     -- 'education' | 'company' | 'facility' ...
    city         TEXT,
    lat          REAL,
    lon          REAL,
    geo_checked  TEXT
);

-- An authorship can carry several institutions, so this hangs off paper_authors.
-- Row order is the order the source listed them in.
CREATE TABLE IF NOT EXISTS authorship_institutions (
    paper_id       INTEGER NOT NULL,
    author_id      INTEGER NOT NULL,
    institution_id INTEGER NOT NULL REFERENCES institutions(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, author_id, institution_id),
    FOREIGN KEY (paper_id, author_id)
        REFERENCES paper_authors(paper_id, author_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ai_author      ON authorship_institutions(author_id);
CREATE INDEX IF NOT EXISTS idx_ai_institution ON authorship_institutions(institution_id);

-- An author's affiliation: the first institution listed on their most recent
-- paper in the library. Derived rather than stored, so it moves on when a
-- newer paper is added instead of keeping whichever one arrived first.
CREATE VIEW IF NOT EXISTS author_affiliation AS
SELECT author_id, affiliation, country
FROM (
    SELECT ai.author_id,
           i.name         AS affiliation,
           i.country_code AS country,
           ROW_NUMBER() OVER (
               PARTITION BY ai.author_id
               ORDER BY p.year DESC, p.month DESC, ai.rowid
           ) AS latest
    FROM authorship_institutions ai
    JOIN papers p       ON p.id = ai.paper_id
    JOIN institutions i ON i.id = ai.institution_id
)
WHERE latest = 1;

-- ---------------------------------------------------------------------------
-- links between papers: a citation is one kind of edge among several
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS link_types (
    id    TEXT PRIMARY KEY,                -- 'cites', 'extends', ...
    label TEXT NOT NULL
);

INSERT OR IGNORE INTO link_types (id, label) VALUES
    ('cites',       'cites'),
    ('extends',     'extends'),
    ('uses_method', 'uses method of'),
    ('uses_data',   'uses data/benchmark of'),
    ('contradicts', 'contradicts'),
    ('replicates',  'replicates'),
    ('background',  'background for'),
    ('related',     'related to');

CREATE TABLE IF NOT EXISTS links (
    id            INTEGER PRIMARY KEY,
    src_paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    dst_paper_id  INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    type          TEXT    NOT NULL REFERENCES link_types(id),
    note          TEXT,                    -- why this link exists

    -- Who drew it. 'auto': from a paper's reference list, a plain citation.
    -- 'manual': you, through the API. 'assistant': Bread, on your request.
    -- Typing a link is a judgement, so only the last two ever type one.
    origin        TEXT NOT NULL DEFAULT 'manual'
                  CHECK (origin IN ('auto','manual','assistant')),
    confirmed     INTEGER NOT NULL DEFAULT 0,  -- you accepted a link you did not draw

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

    -- denormalised display data, so the reference list can show something useful
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
-- author profiles: biography, portrait and links
-- ---------------------------------------------------------------------------
-- One row per author, written whether or not a page was found. Caching the
-- misses matters as much as caching the hits: most researchers have no
-- Wikipedia page, and without this every visit would re-run a search that is
-- always going to fail.
CREATE TABLE IF NOT EXISTS author_profiles (
    author_id      INTEGER PRIMARY KEY REFERENCES authors(id) ON DELETE CASCADE,
    status         TEXT NOT NULL CHECK (status IN ('found','none','error')),

    -- the Wikipedia page, when one was found
    title          TEXT,
    url            TEXT,
    description    TEXT,               -- one-line gloss, e.g. "Finnish computer scientist"
    extract        TEXT,               -- opening paragraph
    thumbnail_url  TEXT,
    image_url      TEXT,
    detail         TEXT,               -- why nothing was found, when nothing was

    -- The biography itself, kept apart from where it came from. Wikipedia is
    -- one source among several, not the field's definition.
    bio            TEXT,
    bio_source     TEXT,
    bio_updated_at TEXT,

    -- A portrait you supplied or the assistant found, cached in the library
    -- folder so it survives the original page moving or blocking hotlinks.
    custom_image        TEXT,
    custom_image_source TEXT,

    -- Wikidata claims about this person, as JSON. Discrete facts that can be
    -- shown and checked one by one, unlike a prose paragraph.
    facts          TEXT,

    fetched_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Links the assistant finds and verifies for an author. Misses are recorded
-- too, so a fruitless search is not repeated on every visit.
CREATE TABLE IF NOT EXISTS author_links (
    id         INTEGER PRIMARY KEY,
    author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,               -- 'homepage' | 'wikipedia' | ...
    status     TEXT NOT NULL CHECK (status IN ('found','none','error')),
    url        TEXT,
    verified   INTEGER NOT NULL DEFAULT 0,  -- the page was fetched and names the author
    found_by   TEXT,                        -- 'wikidata' or 'provider/model'
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (author_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_author_links ON author_links(author_id);

-- ---------------------------------------------------------------------------
-- assistant conversations
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
    -- The model's own words, which is what it is shown again next turn.
    content    TEXT,
    tool_calls TEXT,               -- JSON, when the assistant asked for tools
    tool_call_id TEXT,             -- which call a 'tool' row answers
    tool_name  TEXT,
    rendered   TEXT,               -- as shown: citations rewritten to their checks
    citations  TEXT,               -- JSON: each cited quote and its verdict
    superseded INTEGER,            -- 1: a draft the correction round replaced
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_messages ON chat_messages(session_id, id);

-- One row per exchange: what was sent, to which model, and what that model
-- read. A model cannot be re-run to the same answer, even at temperature zero,
-- so reproducing a turn means being able to see exactly what went in. The
-- system prompt is stored whole because it carries the page the user was
-- reading; the tool definitions are stored as a hash plus the app's commit,
-- which together name them exactly.
CREATE TABLE IF NOT EXISTS chat_turns (
    id                INTEGER PRIMARY KEY,
    session_id        INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    -- Editing a message deletes it and what followed; its record goes too.
    user_message_id   INTEGER REFERENCES chat_messages(id) ON DELETE CASCADE,
    answer_message_id INTEGER REFERENCES chat_messages(id) ON DELETE SET NULL,
    status            TEXT NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running','done','error','stopped')),
    provider          TEXT,
    model             TEXT,          -- as chosen in settings
    route             TEXT,          -- OpenRouter upstream pin, if any
    thinking          TEXT,
    app_version       TEXT,          -- git commit of this checkout, "+dirty" if edited
    system_prompt     TEXT,
    system_sha256     TEXT,
    tools_sha256      TEXT,
    responses         TEXT,          -- JSON: per round, the model and request the provider reports
    tools_used        TEXT,          -- JSON: the tools called, in order
    absences          TEXT,          -- JSON: literal searches of a PDF that found nothing
    documents         TEXT,          -- JSON: each PDF read, with its SHA-256
    usage             TEXT,          -- JSON: tokens summed over the turn
    rounds            INTEGER,
    citations         TEXT,          -- JSON: how the answer's quotes checked out
    repair            TEXT,          -- JSON: the first draft and its failed quotes, if corrected
    error             TEXT,
    started_at        TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_chat_turns        ON chat_turns(session_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_user   ON chat_turns(user_message_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_answer ON chat_turns(answer_message_id);

-- ---------------------------------------------------------------------------
-- settings: API keys, contact email, behaviour toggles
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('contact_email',        ''),   -- used for API polite pools; required
    ('openalex_key',         ''),   -- optional, raises OpenAlex's limits
    ('semantic_scholar_key', ''),   -- optional, lifts the 1 req/s shared limit
    ('fetch_references',     '1'),  -- pull "what this cites" on add
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
    ('assistant_thinking',   'off'),
    -- OpenRouter only: the upstream provider to pin. Blank lets OpenRouter choose.
    ('assistant_route',      '');

-- ---------------------------------------------------------------------------
-- shelves: your ordered bookshelf
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shelves (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    color       TEXT,
    kind        TEXT NOT NULL DEFAULT 'topic'
                CHECK (kind IN ('topic','supplement','dataset','background','method')),
    sort_order  INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO shelves (id, name, kind, color, sort_order) VALUES
    (1, 'Core',       'topic',      '#3b82f6', 0),
    (2, 'Supplement', 'supplement', '#f59e0b', 1),
    (3, 'Datasets',   'dataset',    '#10b981', 2),
    (4, 'Background', 'background', '#6b7280', 3);

CREATE TABLE IF NOT EXISTS paper_shelves (
    paper_id   INTEGER NOT NULL REFERENCES papers(id)  ON DELETE CASCADE,
    shelf_id   INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, shelf_id)
);

CREATE INDEX IF NOT EXISTS idx_ps_shelf ON paper_shelves(shelf_id);

-- ---------------------------------------------------------------------------
-- topics: OpenAlex's own taxonomy
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- notes and highlights
-- ---------------------------------------------------------------------------
-- One note per paper: what you write about it, with anything Bread adds
-- appended as its own marked block.
CREATE TABLE IF NOT EXISTS notes (
    paper_id   INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    body       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS highlights (
    id          INTEGER PRIMARY KEY,
    paper_id    INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page        INTEGER NOT NULL,
    -- JSON array of {x,y,w,h} as fractions of the page (0-1), so a highlight
    -- lands correctly at any zoom. The page size in points sits beside them,
    -- so an export can convert back to absolute PDF units.
    rects       TEXT NOT NULL,
    page_width  REAL,
    page_height REAL,
    quoted      TEXT,                      -- selected text
    comment     TEXT,
    color       TEXT DEFAULT '#fde047',
    source      TEXT CHECK (source IN ('user','assistant')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hl_paper ON highlights(paper_id);

-- ---------------------------------------------------------------------------
-- passages: the full text of stored PDFs, cut up for search
-- ---------------------------------------------------------------------------
-- Derived data, rebuilt from the PDF whenever its hash changes, so nothing
-- here is ever the only copy of anything. A passage never crosses a page, so
-- every hit can name the page it came from and be cited from there.
CREATE TABLE IF NOT EXISTS passages (
    id         INTEGER PRIMARY KEY,
    paper_id   INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page       INTEGER NOT NULL,
    seq        INTEGER NOT NULL,           -- order within the paper
    text       TEXT NOT NULL,
    references_section INTEGER NOT NULL DEFAULT 0,   -- part of the bibliography
    embedding  BLOB,                       -- float32, unit length; NULL if keyword-only
    UNIQUE (paper_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_passages_paper ON passages(paper_id);

-- Which file each paper's passages came from, and how they were made. A
-- different hash, chunker or embedder means the passages are stale.
CREATE TABLE IF NOT EXISTS passage_index (
    paper_id   INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    pdf_sha256 TEXT NOT NULL,
    chunker    TEXT NOT NULL,
    embedder   TEXT,
    passages   INTEGER NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    text, content='passages', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS passages_fts_ai AFTER INSERT ON passages BEGIN
    INSERT INTO passages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS passages_fts_ad AFTER DELETE ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS passages_fts_au AFTER UPDATE OF text ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO passages_fts(rowid, text) VALUES (new.id, new.text);
END;
