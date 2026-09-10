import { useEffect, useRef, useState } from "react";
import { staticUrl } from "../api";
import type { SiteConfig } from "../api";
import "./About.css";

/** Used until site.json arrives, and for anything it leaves blank. */
const FALLBACK: SiteConfig = {
  repo_url: "https://github.com/YOUR-USERNAME/Breadcrumbs",
  owner: "",
  title: "",
  intro: "",
};

/**
 * What this page is, for someone who arrived without context.
 *
 * A published library looks like an app but answers to nothing: no adding, no
 * notes, no assistant. Saying so plainly is kinder than letting someone hunt
 * for buttons that were removed, and it is the natural place to point at the
 * repository for anyone who wants their own.
 *
 * It sits in the top bar, where the app puts everything else that is about the
 * page rather than in it. As a badge in a corner it was missed: a mark floating
 * over a grid of cards reads as part of the grid, and the one question a first
 * visitor has deserves to be asked in words.
 *
 * The wording comes from pages.json by way of the export, not from here, so
 * changing it is editing a config file and re-running `./breadcrumbs pages`
 * rather than editing a component.
 */
export default function About() {
  const [open, setOpen] = useState(false);
  const [site, setSite] = useState<SiteConfig>(FALLBACK);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(staticUrl("site.json"))
      .then((r) => (r.ok ? r.json() : null))
      .then((d: SiteConfig | null) => {
        if (!cancelled && d) setSite({ ...FALLBACK, ...d });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  /*
   * Three ways out, since the panel has no close button: the key that closes
   * everything, the button that opened it, and anywhere else on the page. The
   * last is what a panel with no visible dismissal needs, or the only way out
   * is the one the reader has to guess.
   */
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const whose = site.owner ? `${site.owner}'s` : "my";

  return (
    <div className="ab-root" ref={root}>
      <button
        className={`ab-open${open ? " is-open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        What is this?
      </button>
      {open && (
        <div className="ab-panel" role="dialog" aria-label="About this page">
          {site.title && <h3 className="ab-title">{site.title}</h3>}
          {site.intro ? (
            <p>{site.intro}</p>
          ) : (
            <p>
              This is a read-only copy of {whose} personal literature library, built with{" "}
              <strong>Breadcrumbs</strong>, a local-first app for reading papers and keeping
              track of how they connect.
            </p>
          )}
          <p>
            You are seeing the front end only. The papers, authors, references and notes are
            frozen into a static export. There is no backend behind this page, and the PDFs are
            not published, since those belong to their publishers.
          </p>
          <p>
            Breadcrumbs runs on your own machine, with your own library.{" "}
            <a href={site.repo_url} target="_blank" rel="noreferrer">
              Build one for yourself →
            </a>
          </p>
        </div>
      )}
    </div>
  );
}
