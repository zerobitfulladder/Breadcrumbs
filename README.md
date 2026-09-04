# Breadcrumbs

A local literature study tool. Papers are added deliberately, one at a time, and
the graph between them fills in as the library grows.

![The grid, with one paper pinned and its references drawn across the columns](docs/grid.png)

## What makes it different

Existing tools derive one relation automatically — citation or embedding
similarity — and draw a single graph from it. Breadcrumbs stores *typed* links
with your own note about why each one exists, and lays papers out in time so
development is legible rather than tangled.

Nothing is auto-imported. Adding a paper records the identifiers of everything
it cites and everything citing it, but creates no other paper rows. When you
later add one of those papers yourself, the link appears on its own. A library
of 131 papers carries about 15,000 of these pending edges, waiting.

## Running it

Two processes, both local.

```bash
# backend, on http://127.0.0.1:8000
cd backend && uv run uvicorn backend.api:app --reload

# frontend, on http://localhost:5173
cd frontend && npm run dev
```

Then open Settings and fill in two things:

- **A contact email.** It goes to OpenAlex, Crossref and Unpaywall, raises your
  rate limits, and is required by Unpaywall.
- **An OpenAlex API key.** Free, from [openalex.org/pricing](https://openalex.org/pricing).
  OpenAlex now meters requests against a daily budget, and without a key you
  share one small anonymous allowance with everything else on your address —
  about a hundred requests before adding papers fails until midnight UTC. A key
  gets its own $1/day, which is 10,000 credits: reading a single record is free,
  a filtered list costs 1, a title search costs 10. Importing 130 papers cost
  about 900.

A Semantic Scholar key is optional and most people will not have one, since it
needs an institutional address and an application. The app runs without it:
Semantic Scholar gets a short retry budget, is skipped the moment it throttles,
and OpenAlex supplies references and citing papers instead. What you lose is the
sentence quoting each citation and its intent label.

Your library lives in `~/Breadcrumbs` (`BREADCRUMBS_HOME` overrides it):
`library.db` plus `pdfs/` and `authors/` as plain files. Notes and highlights are
rows in the database, not files — a highlight stores its rectangles as fractions
of the page, so it survives re-downloading the PDF at any zoom.

## The grid

Every paper on one screen, in fixed columns of N years. A card's column is
decided by its year alone — not by the viewport, the column count, or how many
papers precede it — so nothing moves when a panel opens or a paper is selected.

That constraint is the whole design. A card has a minimum readable width, so
once a library spans more years than the canvas has card-widths, a proportional
time axis draws neighbouring years on top of each other and no amount of zooming
escapes it. Binning gives up resolution instead of position.

- **`− 5 years +`** sets the interval, from 1 to 50. Wider bins mean fewer,
  larger cards.
- **Empty intervals keep their column**, so width measures elapsed time and a
  long silence reads as a gap. A 1840–2022 library at five-year bins is 37
  columns, 21 of them empty, their labels dimmed.
- **Newest at the top** within a column.
- **Scroll to zoom**, anchored under the cursor. Drag anywhere to pan.
- Cards hold a **16:9 shape** at every zoom and interval, and the type is sized
  to the card — both dimensions constrain it, and the title is clamped to lines
  that actually fit rather than overflowing hidden.

Hovering or pinning a paper draws its links, in two colours: **orange for
references** (work this paper drew on) and **violet for cited by** (work that
came back to it). Nothing is drawn at rest — at this density the whole graph is
noise.

Middle-click any card, on the grid or in the list, to open it in its own tab.

## The panels

**Left** — papers or authors, filterable, sortable by year, title or citation
count. Click a column to sort; click again to reverse. Numbers open on the
largest, names on A. Below it, a map of the author affiliations for whatever is
in focus.

**Right** — the selected paper: status, authors with affiliations, a DOI you
click to copy, its note and highlights, and its references and citing papers
split into two tabs with a filter for the ones you do not hold. Papers you do
not have can be previewed in place — the same lookup the Add page runs, shown as
a card, saving nothing.

Papers and authors both take a **star**, in the panel and in the list rows.

## Adding papers

Paste a DOI, arXiv id, OpenAlex id or S2 hash, or search by title with
autocomplete. Every lookup queries all sources concurrently and streams results
over server-sent events, so the Add screen shows a panel per source and fills
each one the moment it answers. Panels report failures rather than hiding them,
and each has a Retry button that re-queries only that source. Nothing is saved
until you press save.

### Where the data comes from

| Source | Supplies | Key |
|---|---|---|
| OpenAlex | identity, authors, institutions, topics, references, citing papers | free key, strongly advised |
| Crossref | publisher metadata, licence, title search | none |
| Semantic Scholar | search suggestions, influential-citation count, fallback | rarely needed |
| Unpaywall | legal open-access PDF links | email required |
| arXiv | preprint PDFs, title search for author manuscripts | none |

OpenAlex is the spine. Semantic Scholar has a narrow role, powering the search
suggestions, because its autocomplete is fast and tolerates bursts where
OpenAlex's does not.

Semantic Scholar's citation contexts and intent labels are deliberately not
imported. Typing a link is a judgement you make, so every automatic link is a
plain citation until you say otherwise.

Titles arrive with the publisher's typesetting still attached — IEEE sends
LaTeX, Crossref sends XML fragments — and it is stripped, since a title is a
label and nothing draws it typeset. Abstracts keep their mathematics and render
it with KaTeX, because there the notation is the content.

### Duplicate records

The same work is often indexed several times. Kohonen's self-organising map
paper is a 1982 journal article, a 1988 book chapter, and a reissue other
sources date to 2004. Placing it at the wrong year would misrepresent when the
idea appeared, so the Add screen lists every record it finds for a title, marks
when an earlier one exists, and lets you switch before saving.

## Full text

Open-access PDFs download automatically, and every legal location is tried — not
just the one Unpaywall ranks best, since that is often a landing page serving
HTML while a repository copy further down the list serves the file. arXiv is
searched by title too, because a paper whose DOI points at a paywalled journal
frequently has the author's own manuscript there under no identifier the record
carries.

For paywalled work the tool offers the publisher page, a PubMed record where one
exists, and — if you set a proxy prefix in Settings — the publisher page through
your own institutional subscription. **Add PDF** attaches a file you already
have. Cards and the detail panel show a **PDF badge** when one is stored.

There is no pirated-source integration and there will not be one.

## Reading

![The reader: the assistant marking passages, with its reasoning beside them](docs/reader.png)

The reader opens in its own tab: PDF on the left, a resizable panel on the right
holding notes and the assistant. Select text to highlight it, attach a note to
the passage, or send it to the assistant as context. Highlights are stored as
page fractions with the page size alongside, so they render correctly at any
zoom and can be exported back to absolute units.

## Authors

Each author has a panel: a biography and portrait, their affiliations, their
papers in your library, and their full output from OpenAlex with citation counts.

Identity is resolved through Wikidata, not by guessing. A Wikipedia search for
"David Field" returns a baseball park whose name matches perfectly; the page's
Wikidata item says it is a sports venue rather than a human, so it is rejected.
Biographies are cached, misses included, because most researchers have no page
and the failing search would otherwise repeat forever.

## The assistant

One assistant, holding every capability as a tool rather than a set of
configured tasks. It reads your papers, authors, links and notes; searches the
web and fetches pages; reads the PDF you have open; and writes notes,
highlights, links, reading statuses and author homepages.

It cannot add papers. That is the point of the tool, so `propose_paper` opens
the preview card and you decide. It never blocks waiting for you — it proposes
and carries on.

Tool calls stream into the conversation as they happen. That is not decoration:
the assistant writes to your database, and watching which tool it reached for is
how a wrong action gets caught while it is happening.

**Skills** keep the prompt small. Only a name and one line per skill sit in the
system prompt; the assistant calls `read_skill` when it decides one applies. The
SQL skill is 3.7 KB of guidance plus the whole schema, which would otherwise
ride on every message. Skills are Markdown files under `ai/skills/`, editable without
touching code, and the schema is injected live so it can never describe a stale
one.

SQL is read-only, through a separate read-only connection rather than a pattern
check alone. `WITH x AS (SELECT 1) DELETE FROM papers` passes any reasonable
regex; the database itself refuses it.

There is no agent framework. The loop is hand-written and about 150 lines —
send messages plus tool schemas, run what comes back, append results, repeat —
and everything that decides quality lives in the tool descriptions and the
system prompt.

Configure it under Settings → AI: a provider key (OpenRouter, Gemini or
DeepSeek), a search key (Brave or Tavily), and a model. The model picker is a
combobox you type into, matching tokens independently so `gpt 4o` and `4o gpt`
both find `openai/gpt-4o` — OpenRouter alone lists hundreds. Models without tool
support are filtered out where the provider says so.

For OpenRouter you can also **pin the upstream provider**. The same model is
served by Anthropic, Google, Azure and Bedrock at different prices and context
limits, so the endpoints are listed cheapest-first with both token prices, and
pinning one turns fallbacks off — a turn fails rather than being served
somewhere else without telling you.

Web search is a separate service rather than a provider's own, because search
availability differs sharply between providers and binding tasks to it would
make most model choices unusable.

## Layout

```
backend/src/backend/
  schema.sql    tables, indexes, seeded link types and shelves
  db.py         connections, settings, migrations
  ids.py        identifier normalisation, the basis of all link matching
  sources/      one adapter per API, plus a rate-limited HTTP client
  store.py      upserts and link resolution
  ingest.py     fetch, merge, preview, save
  pdftext.py    page text, outline, quote to rectangles
  ai/           agent loop, tools, skills
  api.py        HTTP routes
frontend/src/
  api.ts                    typed client
  components/Timeline       the binned grid
  components/timelineLayout binning, card sizing, type metrics
  components/AddPaper       search, per-source preview, then save
  components/Reader         PDF, highlights, notes, assistant
  components/Settings       keys, fetch behaviour, source health check
```

Every request goes through one client with a per-host token bucket and
exponential backoff, so a source that throttles slows only itself.
