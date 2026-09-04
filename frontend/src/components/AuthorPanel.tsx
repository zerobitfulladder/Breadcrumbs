import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { AuthorDetail, AuthorLink, AuthorProfile, AuthorStats, AuthorWork } from "../api";
import PaperPreviewCard from "./PaperPreviewCard";
import "./AuthorPanel.css";
import StarButton from "./StarButton";

interface Props {
  authorId: number;
  /** Bumped when anything is written, so this panel refetches. */
  refreshKey?: number;
  onSelectPaper?: (id: number) => void;
  /** Pre-fill the assistant with a request, without sending it. */
  onAsk?: (draft: string) => void;
  /** Shown on the back button when this panel was opened from a paper. */
  backLabel?: string;
  onBack?: () => void;
  width?: number;
}

export default function AuthorPanel({
  authorId,
  refreshKey = 0,
  onSelectPaper,
  onAsk,
  backLabel,
  onBack,
  width,
}: Props) {
  const [author, setAuthor] = useState<AuthorDetail | null>(null);
  const [profile, setProfile] = useState<AuthorProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const [works, setWorks] = useState<AuthorWork[] | null>(null);
  const [stats, setStats] = useState<AuthorStats | null>(null);
  const [worksNote, setWorksNote] = useState<string | null>(null);
  const [loadingWorks, setLoadingWorks] = useState(false);
  const [previewing, setPreviewing] = useState<AuthorWork | null>(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoUrl, setPhotoUrl] = useState("");
  const [showPhotoInput, setShowPhotoInput] = useState(false);
  /** Bumped after a photo change so the <img> refetches rather than caching. */
  const [photoVersion, setPhotoVersion] = useState(0);
  const [facts, setFacts] = useState<Awaited<ReturnType<typeof api.authorFacts>> | null>(null);
  const [links, setLinks] = useState<Record<string, AuthorLink>>({});

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setAuthor(null);
    setProfile(null);
    setError(null);

    // The record comes back at once; the biography may need a network round
    // trip on first view, so it is fetched separately and fills in after.
    api
      .author(authorId)
      .then((a) => {
        if (cancelled) return;
        setAuthor(a);
        setLoading(false);
        return api.authorProfile(authorId);
      })
      .then((p) => !cancelled && p && setProfile(p))
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : String(e));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [authorId, refreshKey]);

  // A different author invalidates the portrait, the bibliography and any
  // preview opened from it.
  useEffect(() => {
    setZoomed(false);
    setWorks(null);
    setStats(null);
    setWorksNote(null);
    setPreviewing(null);
    api.authorLinks(authorId).then((d) => setLinks(d.links)).catch(() => setLinks({}));
    // Composed from ORCID, OpenAlex and Wikidata; each contributes what it is
    // good for, so a researcher with no Wikipedia article still gets a profile.
    api.authorFacts(authorId).then(setFacts).catch(() => setFacts(null));
  }, [authorId, refreshKey]);

  // Clear the loaded bibliography only when the author changes: a write should
  // refresh what is on screen, not throw away a list the user asked for.
  useEffect(() => {
    setWorks(null);
    setStats(null);
    setWorksNote(null);
    setLinks({});
  }, [authorId]);



  async function loadWorks() {
    setLoadingWorks(true);
    try {
      const d = await api.authorWorks(authorId);
      setWorks(d.works);
      setStats(d.profile);
      setWorksNote(d.detail);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoadingWorks(false);
    }
  }

  useEffect(() => {
    if (!zoomed) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setZoomed(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomed]);

  async function refresh() {
    setRefreshing(true);
    try {
      setProfile(await api.authorProfile(authorId, true));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }

  if (loading) {
    return (
      <aside className="ap-root" style={width ? { flexBasis: width } : undefined}>
        <div className="ap-skel ap-skel-title" />
        <div className="ap-skel ap-skel-line" />
        <div className="ap-skel ap-skel-line" />
      </aside>
    );
  }

  if (error || !author) {
    return <aside className="ap-root"><div className="ap-error">{error ?? "Not found"}</div></aside>;
  }

  const stored = Boolean(profile?.custom_image);
  const thumb = stored
    ? `${api.authorPhotoUrl(authorId)}?v=${photoVersion}`
    : (profile?.portrait_url ?? profile?.thumbnail_url ?? profile?.image_url);
  const full = stored
    ? `${api.authorPhotoUrl(authorId)}?v=${photoVersion}`
    : (profile?.portrait_full_url ?? profile?.image_url ?? profile?.thumbnail_url);

  async function afterPhotoChange() {
    setPhotoVersion((v) => v + 1);
    setPhotoUrl("");
    setShowPhotoInput(false);
    try {
      setProfile(await api.authorProfile(authorId));
    } catch {
      /* the picture is already updated; a stale caption is not worth an error */
    }
  }

  async function uploadPhoto(file: File) {
    setPhotoBusy(true);
    setError(null);
    try {
      await api.uploadAuthorPhoto(authorId, file);
      await afterPhotoChange();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPhotoBusy(false);
    }
  }

  async function usePhotoUrl() {
    if (!photoUrl.trim()) return;
    setPhotoBusy(true);
    setError(null);
    try {
      await api.setAuthorPhotoUrl(authorId, photoUrl.trim());
      await afterPhotoChange();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPhotoBusy(false);
    }
  }

  return (
    <aside className="ap-root" style={width ? { flexBasis: width } : undefined}>
      {showPhotoInput && (
        <div className="ap-photo-edit">
          <input
            value={photoUrl}
            onChange={(e) => setPhotoUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && usePhotoUrl()}
            placeholder="Paste an image URL"
            spellCheck={false}
          />
          <div className="ap-photo-actions">
            <label className="ap-photo-file">
              {photoBusy ? "Working…" : "Upload a file"}
              <input
                type="file"
                accept="image/*"
                disabled={photoBusy}
                onChange={(e) => e.target.files?.[0] && uploadPhoto(e.target.files[0])}
              />
            </label>
            <button
              onClick={() =>
                onAsk?.(
                  `Find a photograph of ${author.name}` +
                    (author.affiliation ? ` (${author.affiliation})` : "") +
                    `, author id ${author.id}. Only use one you are confident shows that ` +
                    `person — their own faculty or lab page, or a Wikipedia article about ` +
                    `them — and save it.`,
                )
              }
            >
              Ask the assistant
            </button>
            {stored && (
              <button
                onClick={async () => {
                  await api.deleteAuthorPhoto(authorId);
                  await afterPhotoChange();
                }}
              >
                Remove
              </button>
            )}
            <button className="primary" onClick={usePhotoUrl} disabled={photoBusy || !photoUrl.trim()}>
              Use URL
            </button>
            <button onClick={() => setShowPhotoInput(false)}>Close</button>
          </div>
        </div>
      )}

      {onBack && (
        // Opened from a paper's byline, so there is somewhere to go back to.
        <button className="ap-back" onClick={onBack}>
          <span aria-hidden>‹</span>
          {backLabel ? `Back to “${backLabel.slice(0, 34)}${backLabel.length > 34 ? "…" : ""}”` : "Back"}
        </button>
      )}
      <div className="ap-head">
        {thumb ? (
          <button
            className="ap-portrait-button"
            onClick={() => setZoomed(true)}
            title="Show a larger portrait"
            aria-label={`Enlarge the portrait of ${author.name}`}
          >
            <img className="ap-portrait" src={thumb} alt={author.name} loading="lazy" />
          </button>
        ) : (
          <div className="ap-portrait ap-portrait-none" aria-hidden>
            {author.name
              .split(/\s+/)
              .map((w) => w[0])
              .filter((c) => /[A-Za-z]/.test(c ?? ""))
              .slice(0, 2)
              .join("")}
          </div>
        )}
        <button
          className="ap-photo-edit-btn"
          onClick={() => setShowPhotoInput((v) => !v)}
          title="Change the portrait"
        >
          {stored ? "Change photo" : "Add photo"}
        </button>
        <div className="ap-ident">
          <h2>
            <a
              className="ap-name-link"
              href={`https://duckduckgo.com/?q=${encodeURIComponent(author.name)}`}
              target="_blank"
              rel="noreferrer"
              title="Search this name on DuckDuckGo"
            >
              {author.name}
            </a>
            <StarButton kind="author" id={author.id} starred={!!author.favorite} />
          </h2>
          {profile?.description && <p className="ap-desc">{profile.description}</p>}
          {author.affiliation && (
            <p className="ap-affil">
              {author.affiliation}
              {author.country ? ` · ${author.country}` : ""}
            </p>
          )}
        </div>
      </div>

      {(profile?.facts || facts?.orcid || facts?.openalex) && (
        <section>
          <h4>Facts</h4>
          <dl className="ap-facts">
            {profile?.facts?.occupations?.length ? (
              <div>
                <dt>Field</dt>
                <dd>{profile.facts.occupations.slice(0, 4).join(", ")}</dd>
              </div>
            ) : null}
            {profile?.facts?.employers?.length ? (
              <div>
                <dt>Worked at</dt>
                <dd>{profile.facts.employers.join(", ")}</dd>
              </div>
            ) : null}
            {profile?.facts?.education?.length ? (
              <div>
                <dt>Educated</dt>
                <dd>{profile.facts.education.join(", ")}</dd>
              </div>
            ) : null}
            {profile?.facts?.born ? (
              <div>
                <dt>Born</dt>
                <dd>{profile.facts.born}</dd>
              </div>
            ) : null}
            {profile?.facts?.awards?.length ? (
              <div>
                <dt>Awards</dt>
                <dd>{profile.facts.awards.slice(0, 6).join(", ")}</dd>
              </div>
            ) : null}
            {facts?.openalex?.h_index != null ? (
              <div>
                <dt>Output</dt>
                <dd>
                  {facts.openalex.works_count} papers · h-index {facts.openalex.h_index} ·{" "}
                  {(facts.openalex.cited_by_count ?? 0).toLocaleString()} citations
                </dd>
              </div>
            ) : null}
            {facts?.orcid?.employments?.length ? (
              <div>
                <dt>ORCID posts</dt>
                <dd>
                  {facts.orcid.employments
                    .slice(0, 4)
                    .map((e) =>
                      [e.role, e.organisation, e.start_year ? `from ${e.start_year}` : null]
                        .filter(Boolean)
                        .join(" · "),
                    )
                    .join("; ")}
                </dd>
              </div>
            ) : null}
          </dl>
          {!!facts?.sources.length && (
            <p className="ap-source">
              From {facts.sources.join(", ")}
              {profile?.facts?.wikidata_id ? ", Wikidata" : ""}.
            </p>
          )}
        </section>
      )}

      <section>
        <h4>
          Bio
          {profile?.bio_source && (
            <span className="ap-source-tag">
              {profile.bio_source === "wikipedia" ? "from Wikipedia" : "written by the assistant"}
            </span>
          )}
        </h4>
        {!profile ? (
          <div className="ap-skel ap-skel-block" />
        ) : profile.bio ? (
          <p className="ap-extract">{profile.bio}</p>
        ) : (
          <p className="ap-none-text">
            No bio yet. {profile.detail || "Wikipedia has no page for this person, which is normal."}
          </p>
        )}
        <div className="ap-links">
          {profile?.url && (
            <a href={profile.url} target="_blank" rel="noreferrer">
              Wikipedia
            </a>
          )}
          {author.orcid && (
            <a href={`https://orcid.org/${author.orcid}`} target="_blank" rel="noreferrer">
              ORCID
            </a>
          )}
          <button className="ap-refresh" onClick={refresh} disabled={refreshing}>
            {refreshing ? "Checking…" : "Recheck Wikipedia"}
          </button>
        </div>
        {profile && !profile.bio && (
          <button
            className="ap-load"
            onClick={() =>
              onAsk?.(
                `Write a short bio for ${author.name}` +
                  (author.affiliation ? ` (${author.affiliation})` : "") +
                  `, author id ${author.id}. Search for their faculty or lab page, read it, ` +
                  `and base the bio only on what you actually find. Then save it.`,
              )
            }
          >
            Ask the assistant to write one
          </button>
        )}
      </section>

      <section>
        <h4>Their own page</h4>
        {links.homepage?.status === "found" ? (
          <div className="ap-homepage">
            <a href={links.homepage.url ?? "#"} target="_blank" rel="noreferrer">
              {links.homepage.title || links.homepage.url}
            </a>
            <span className="ap-verified" title="The page was fetched and names this author">
              verified
            </span>
            {links.homepage.found_by && (
              <span className="ap-found-by">via {links.homepage.found_by}</span>
            )}
          </div>
        ) : (
          <p className="ap-none-text">
            {links.homepage?.detail ?? "Not looked up yet."}
          </p>
        )}
        <button
          className="ap-load"
          onClick={() =>
            onAsk?.(
              `Find the personal or university homepage for ${author.name}` +
                (author.affiliation ? ` (${author.affiliation})` : "") +
                `, verify the page really is about them, and record it. Their author id is ${author.id}.`,
            )
          }
        >
          {links.homepage ? "Ask the assistant to look again" : "Ask the assistant to find it"}
        </button>
      </section>

      {!!author.institutions.length && (
        <section>
          <h4>Affiliations</h4>
          <ul className="ap-institutions">
            {author.institutions.map((i) => (
              <li key={i.id ?? i.name}>
                {i.name}
                {i.country_code ? <span className="dim"> · {i.country_code}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h4>
          In your library
          <span className="ap-badge">{author.papers.length}</span>
        </h4>
        <ul className="ap-papers">
          {author.papers.map((p) => (
            <li key={p.id}>
              <button onClick={() => onSelectPaper?.(p.id)}>
                <span className="ap-paper-year">{p.year ?? "—"}</span>
                <span className="ap-paper-title">
                  {p.title}
                  {p.is_corresponding ? <span className="ap-tag">corresponding</span> : null}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h4>
          Everything they published
          {stats?.works_count != null && <span className="ap-badge">{stats.works_count}</span>}
        </h4>

        {stats && (
          <div className="ap-stats">
            <span><strong>{stats.h_index ?? "—"}</strong> h-index</span>
            <span><strong>{(stats.cited_by_count ?? 0).toLocaleString()}</strong> citations</span>
            {!!stats.topics.length && <span className="ap-topics">{stats.topics[0]}</span>}
          </div>
        )}

        {works === null ? (
          <button className="ap-load" onClick={loadWorks} disabled={loadingWorks}>
            {loadingWorks ? "Loading from OpenAlex…" : "Load their full bibliography"}
          </button>
        ) : worksNote ? (
          <p className="ap-none-text">{worksNote}</p>
        ) : (
          <ul className="ap-works">
            {works.map((w) => (
              <li key={w.openalex_id} className={w.in_library ? "is-held" : ""}>
                <span className="ap-work-year">{w.year ?? "—"}</span>
                <span className="ap-work-body">
                  <span className="ap-work-title">{w.title}</span>
                  <span className="ap-work-meta">
                    {[w.venue, w.citation_count != null ? `${w.citation_count} cited` : null]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                {w.in_library ? (
                  <button
                    className="ap-work-action is-held"
                    onClick={() => w.paper_id && onSelectPaper?.(w.paper_id)}
                    title="Already in your library"
                  >
                    In library
                  </button>
                ) : (
                  <button
                    className="ap-work-action"
                    onClick={() => setPreviewing(w)}
                    disabled={!w.doi && !w.openalex_id}
                  >
                    Preview
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>



      {previewing && (
        <PaperPreviewCard
          identifier={previewing.doi ?? previewing.openalex_id}
          titleHint={previewing.title}
          onClose={() => setPreviewing(null)}
        />
      )}

      {zoomed && full && (
        // Fixed to the viewport, so it centres over the whole app rather than
        // inside this panel. A click on the backdrop dismisses it; a click on
        // the picture itself does not.
        <div
          className="ap-lightbox"
          onPointerDown={() => setZoomed(false)}
          role="dialog"
          aria-modal="true"
          aria-label={`Portrait of ${author.name}`}
        >
          <figure onPointerDown={(e) => e.stopPropagation()}>
            <img src={full} alt={author.name} />
            <figcaption>
              <strong>{author.name}</strong>
              {profile?.description && <span>{profile.description}</span>}
            </figcaption>
          </figure>
        </div>
      )}
    </aside>
  );
}
