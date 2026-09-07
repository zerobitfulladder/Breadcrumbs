import { useState } from "react";
import type { PaperRecord, Preview, SourcePanel, WorkVersion } from "../api";
import type { EdgeState } from "../hooks/usePreviewStream";
import "./AddPaper.css";
import Abstract from "./Abstract";

const SOURCE_LABEL: Record<string, string> = {
  openalex: "OpenAlex",
  s2: "Semantic Scholar",
  crossref: "Crossref",
  unpaywall: "Unpaywall",
};

interface Props {
  order: string[];
  panels: Record<string, SourcePanel>;
  merged: PaperRecord | null;
  versions: WorkVersion[];
  versionsError?: string | null;
  refs: EdgeState;
  preview: Preview | null;
  streaming: boolean;
  /** Re-query one source, or "references". */
  onRetry?: (source: string) => void;
  /** Load a different record for the same work. */
  onUseVersion?: (identifier: string, title: string) => void;
  /** Rendered at the top right: the Add button, or a Close button in a card. */
  actions?: React.ReactNode;
  disabled?: boolean;
}

/**
 * Everything a lookup produced: source panels, the merged record, other
 * versions of the same work, and the reference list.
 *
 * Shared by the Add page and the preview card opened from an author's
 * bibliography so the two can never drift apart.
 */
export default function PaperPreviewBody({
  order,
  panels,
  merged,
  versions,
  versionsError,
  refs,
  preview,
  streaming,
  onRetry,
  onUseVersion,
  actions,
  disabled,
}: Props) {
  const [openSource, setOpenSource] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const edges = refs;

  async function retry(source: string) {
    setRetrying(source);
    try {
      await onRetry?.(source);
    } finally {
      setRetrying(null);
    }
  }

  return (
    <div className="add-preview">
      <div className="add-head">
        <div className="add-head-title">
          {merged ? (
            <>
              <h2>{merged.title}</h2>
              <p className="add-sub">
                {[merged.year, merged.venue, merged.publisher].filter(Boolean).join(" · ")}
              </p>
            </>
          ) : (
            <>
              <div className="skel skel-title" />
              <div className="skel skel-line" />
            </>
          )}
        </div>
        <div className="add-actions">
          {preview?.already_in_library && (
            <span className="add-badge warn">Already in library</span>
          )}
          {actions}
        </div>
      </div>

      <div className="add-stats">
        <Stat label="Authors" value={merged?.authors?.length} pending={!merged} />
        <Stat label="References" value={refs.count} pending={refs.loading} />
        <Stat
          label="Links on save"
          value={preview?.counts.would_link_now}
          pending={!preview}
          hint={
            preview
              ? `${preview.counts.would_link_outgoing} out · ${preview.counts.would_link_incoming} in`
              : undefined
          }
          highlight={(preview?.counts.would_link_now ?? 0) > 0}
        />
      </div>

      {!!preview?.warnings.length && (
        <ul className="add-warnings">
          {preview.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      {versionsError && !versions.length && (
        <div className="add-versions is-warning">
          <div className="add-versions-head">
            <strong>Could not check for other versions of this work.</strong>
          </div>
          <p className="add-note">
            {versionsError}. The same paper is often indexed more than once under different
            years, so the year shown here may not be the original.
          </p>
        </div>
      )}

      {versions.length > 0 && (
        <div className="add-versions">
          <div className="add-versions-head">
            <strong>
              This work is indexed {versions.length + 1} times, under different years.
            </strong>
            {preview?.earlier_version_year && (
              <span className="add-badge warn">
                An earlier version exists ({preview.earlier_version_year})
              </span>
            )}
          </div>
          <p className="add-note">
            Reprints, preprints and book chapters each get their own record. Pick the one
            whose year you want on the timeline; the original is usually the earliest and
            the most cited.
          </p>
          <ul>
            <li className="is-current">
              <span className="add-version-year">{merged?.year ?? "—"}</span>
              <span className="add-version-what">
                {merged?.venue ?? "unknown venue"}
                <span className="dim"> · showing this one</span>
              </span>
              <span className="add-version-cites">
                {merged?.citation_count != null ? `${merged.citation_count} citations` : ""}
              </span>
            </li>
            {versions.map((v) => (
              <li key={v.openalex_id}>
                <span className="add-version-year">{v.year ?? "—"}</span>
                <span className="add-version-what">
                  {v.venue ?? "unknown venue"}
                  {v.type ? <span className="dim"> · {v.type}</span> : null}
                </span>
                <span className="add-version-cites">
                  {v.citation_count != null ? `${v.citation_count} citations` : ""}
                </span>
                <button
                  onClick={() => onUseVersion?.(v.doi ?? v.openalex_id, v.title)}
                  disabled={streaming || disabled}
                >
                  Use this one
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <section>
        <h3>Sources</h3>
        <p className="add-note">
          All queried at once. Each panel fills in when its source answers, and any one can be
          retried on its own without redoing the rest.
        </p>
        <div className="add-sources">
          {order.map((name) => {
            const src = panels[name];
            if (!src) return null;
            const pending = src.status === "pending";
            return (
              <div key={name} className={`add-source status-${src.status}`}>
                <div className="add-source-head">
                  <strong>
                    <span className={`sdot ${src.status}`} aria-hidden />
                    {SOURCE_LABEL[name] ?? name}
                  </strong>
                  <span>
                    {pending
                      ? "…"
                      : src.status === "ok"
                        ? `${src.fields.length} fields · ${src.ms} ms`
                        : src.status}
                  </span>
                </div>

                {pending ? (
                  <div className="skel-stack">
                    <div className="skel skel-line" />
                    <div className="skel skel-line short" />
                    <div className="skel skel-line" />
                  </div>
                ) : src.status === "ok" ? (
                  <>
                    <dl>
                      <div><dt>Title</dt><dd>{src.title ?? "—"}</dd></div>
                      <div><dt>Year</dt><dd>{src.year ?? "—"}</dd></div>
                      <div><dt>Venue</dt><dd>{src.venue ?? "—"}</dd></div>
                      <div><dt>Authors</dt><dd>{src.author_count}</dd></div>
                      <div><dt>Abstract</dt><dd>{src.has_abstract ? "yes" : "no"}</dd></div>
                    </dl>
                    {src.detail && <p className="add-source-note">{src.detail}</p>}
                  </>
                ) : (
                  <p className="add-source-detail">{src.detail || "no detail"}</p>
                )}

                <div className="add-source-foot">
                  {src.status === "ok" && src.record && (
                    <button
                      className="link"
                      onClick={() => setOpenSource(openSource === name ? null : name)}
                    >
                      {openSource === name ? "Hide raw" : "Show raw"}
                    </button>
                  )}
                  <button
                    className="retry"
                    onClick={() => void retry(name)}
                    disabled={!preview || retrying !== null || streaming || disabled}
                    title={
                      preview
                        ? `Query ${SOURCE_LABEL[name] ?? name} again`
                        : "Available once the lookup finishes"
                    }
                  >
                    {retrying === name ? "Retrying…" : "Retry"}
                  </button>
                </div>
                {openSource === name && (
                  <pre className="add-raw">{JSON.stringify(src.record, null, 2)}</pre>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {merged && (
        <section className="add-two-col">
          <div>
            <h3>Merged record</h3>
            <dl className="add-merged">
              <Field label="DOI" value={merged.doi} />
              <Field label="arXiv" value={merged.arxiv_id} />
              <Field label="Semantic Scholar" value={merged.s2_id} />
              <Field label="OpenAlex" value={merged.openalex_id} />
              <Field label="Citations" value={merged.citation_count} />
              <Field label="Influential" value={merged.influential_citation_count} />
              <Field
                label="Open access"
                value={merged.is_oa ? merged.oa_status || "yes" : "no"}
              />
              <Field label="Licence" value={merged.license} />
            </dl>
            {merged.abstract && (
              <>
                <h4>Abstract</h4>
                <Abstract className="add-abstract" text={merged.abstract} />
              </>
            )}
            {!!merged.topics?.length && (
              <>
                <h4>Topics</h4>
                <div className="add-chips">
                  {merged.topics.map((t) => (
                    <span key={t.name} className="chip">{t.name}</span>
                  ))}
                </div>
              </>
            )}
          </div>

          <div>
            <h3>Authors</h3>
            <ol className="add-authors">
              {(merged.authors ?? []).map((a, i) => (
                <li key={`${a.name}-${i}`}>
                  <div className="add-author-name">
                    {a.name}
                    {a.is_corresponding ? (
                      <span className="chip small">corresponding</span>
                    ) : null}
                  </div>
                  <div className="add-author-meta">
                    {(a.institutions ?? []).map((inst) => (
                      <span key={inst.name}>
                        {inst.name}
                        {inst.country_code ? ` (${inst.country_code})` : ""}
                      </span>
                    ))}
                    {a.orcid && <span className="mono">ORCID {a.orcid}</span>}
                  </div>
                </li>
              ))}
            </ol>

            {!!merged.locations?.length && (
              <>
                <h4>Known copies</h4>
                <ul className="add-locations">
                  {merged.locations.map((loc, i) => (
                    <li key={i}>
                      <span className={loc.is_oa ? "dot open" : "dot closed"} />
                      {loc.name ?? "unknown"} <span className="dim">{loc.kind}</span>
                      {loc.version ? <span className="dim"> · {loc.version}</span> : null}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </section>
      )}

      <section>
        <div className="add-tabs">
          <button className="is-active">
            References {refs.loading ? "…" : `(${refs.count})`}
          </button>
          <button
            className="retry"
            onClick={() => void retry("references")}
            disabled={!preview || retrying !== null || streaming || disabled}
          >
            {retrying === "references" ? "Retrying…" : "Retry this list"}
          </button>
        </div>
        {edges.note && <p className="add-note">{edges.note}</p>}
        <p className="add-note">
          Recorded as identifiers only. No paper is added on your behalf. A link appears when
          you add the other paper yourself.
        </p>
        {edges.loading ? (
          <div className="skel-stack">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skel skel-row" />
            ))}
          </div>
        ) : (
          <ul className="add-edges">
            {edges.sample.map((e, i) => (
              <li key={i}>
                <div className="add-edge-title">
                  {e.title ?? e.doi ?? e.openalex_id ?? "untitled"}
                  {e.year ? <span className="dim"> · {e.year}</span> : null}
                </div>
                {e.authors_blob && <div className="add-edge-authors">{e.authors_blob}</div>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  highlight,
  pending,
}: {
  label: string;
  value?: number;
  hint?: string;
  highlight?: boolean;
  pending?: boolean;
}) {
  return (
    <div className={`add-stat${highlight ? " is-hot" : ""}`}>
      {pending ? (
        <span className="skel skel-num" />
      ) : (
        <span className="add-stat-value">{value ?? 0}</span>
      )}
      <span className="add-stat-label">{label}</span>
      {hint && <span className="add-stat-hint">{hint}</span>}
    </div>
  );
}

function Field({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd className="mono">{String(value)}</dd>
    </div>
  );
}
