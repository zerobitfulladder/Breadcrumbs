---
name: database-queries
description: Query the library directly with SQL, for counts, groupings, comparisons and anything the ordinary tools cannot express.
---

# Querying the library with SQL

Use `run_sql` when the question is about shape rather than about one record:
counts, groupings, "which authors appear on more than one paper", "how many
papers per year", "which shelves are empty". The ordinary tools are better for
fetching a specific paper or author; SQL is for questions across the whole set.

**Reads only.** The connection is opened read-only, so `INSERT`, `UPDATE`,
`DELETE` and `PRAGMA` writes fail. To change something, use the dedicated tool
for it — `add_note`, `create_link`, `set_paper_status`, `set_author_homepage`.
That is deliberate: those tools carry checks that raw SQL would bypass.

## Schema

{{SCHEMA}}

## Notes that will save you a wasted query

- `papers.lane` is the timeline row the user dragged a card to; `NULL` means
  automatic placement. It is layout, not meaning.
- `links` holds only citations **between papers both in the library**.
  `origin` is `auto` when it was resolved from citation data, `manual` when the
  user asserted it, `assistant` when you did. `confirmed` is whether the user
  has endorsed it.
- `pending_links` is the important one for "what am I missing": every reference
  and citation recorded by identifier whose other side is not in the library.
  `resolved_paper_id IS NULL` means still missing. A paper cited by many held
  papers is a strong candidate to suggest.
- `paper_authors` carries author order in `position`; position 0 is first
  author. `is_corresponding` marks who handles correspondence.
- `authors.normalized_name` is casefolded and stripped of punctuation, used for
  deduplication. Match on it rather than `name` when comparing.
- `author_profiles` caches Wikipedia lookups including misses: `status` is
  `found` or `none`. `author_links` holds homepages the same way.
- Full-text search lives in `papers_fts`; join with
  `papers.id IN (SELECT rowid FROM papers_fts WHERE papers_fts MATCH ?)`.

## Worked examples

Papers per year, most recent first:

```sql
SELECT year, COUNT(*) AS n FROM papers
WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC
```

Authors appearing on more than one paper:

```sql
SELECT a.name, COUNT(*) AS papers FROM authors a
JOIN paper_authors pa ON pa.author_id = a.id
GROUP BY a.id HAVING papers > 1 ORDER BY papers DESC
```

The most-cited papers the user does *not* hold, ranked by how many of their
held papers reference them — the best answer to "what should I add next":

```sql
SELECT pl.title, pl.doi, pl.year, COUNT(DISTINCT pl.from_paper_id) AS cited_by_mine,
       MAX(pl.citation_count) AS citations
FROM pending_links pl
WHERE pl.resolved_paper_id IS NULL AND pl.direction = 'reference' AND pl.title IS NOT NULL
GROUP BY COALESCE(pl.doi, pl.openalex_id)
ORDER BY cited_by_mine DESC, citations DESC
LIMIT 15
```

Papers with no links at all, which are the isolated ones on the timeline:

```sql
SELECT p.id, p.title, p.year FROM papers p
WHERE NOT EXISTS (
  SELECT 1 FROM links l WHERE l.src_paper_id = p.id OR l.dst_paper_id = p.id
)
ORDER BY p.year
```

Reading progress by shelf:

```sql
SELECT s.name, p.status, COUNT(*) AS n
FROM shelves s
JOIN paper_shelves ps ON ps.shelf_id = s.id
JOIN papers p ON p.id = ps.paper_id
GROUP BY s.id, p.status ORDER BY s.name
```

## Reporting results

Say what the query found, not that you ran a query. Show a small table when
there are several rows, and give the paper or author id alongside a title so
the user can act on it. If a result is empty, say so and suggest why — an empty
`links` result usually means the other side of the citation is not in the
library yet, not that the papers are unrelated.
