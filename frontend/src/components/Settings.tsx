import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Settings as SettingsData, SourceProbe } from "../api";
import SettingsAi from "./SettingsAi";
import "./Settings.css";

type Tab = "sources" | "fetching" | "ai" | "library";

const SOURCE_LABEL: Record<string, string> = {
  openalex: "OpenAlex",
  semantic_scholar: "Semantic Scholar",
  crossref: "Crossref",
  unpaywall: "Unpaywall",
};

export default function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [probe, setProbe] = useState<SourceProbe | null>(null);
  const [busy, setBusy] = useState<"load" | "save" | "test" | null>("load");
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("sources");
  const [wipePhrase, setWipePhrase] = useState("");
  const [wipeOpen, setWipeOpen] = useState(false);
  const [wiped, setWiped] = useState<string | null>(null);

  useEffect(() => {
    api
      .settings()
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setBusy(null));
  }, []);

  function field<K extends keyof SettingsData>(key: K, value: SettingsData[K]) {
    setData((d) => (d ? { ...d, [key]: value } : d));
  }

  async function save() {
    if (!data) return;
    setBusy("save");
    setError(null);
    try {
      const body: Record<string, unknown> = {
        contact_email: data.contact_email,
        institution_proxy: data.institution_proxy,
        citation_page_limit: Number(data.citation_page_limit) || 500,
        fetch_citations: data.fetch_citations === "1",
        fetch_references: data.fetch_references === "1",
        auto_download_pdf: data.auto_download_pdf === "1",
        search_provider: data.search_provider,
        assistant_provider: data.assistant_provider,
        assistant_model: data.assistant_model,
        assistant_max_rounds: Number(data.assistant_max_rounds) || 12,
        assistant_thinking: data.assistant_thinking,
        assistant_route: data.assistant_route ?? "",
      };
      // An empty key field means "keep what is stored", never "erase it".
      for (const k of [
        "openalex_key", "semantic_scholar_key", "search_api_key",
        "ai_openrouter_key", "ai_gemini_key", "ai_deepseek_key",
      ] as const) {
        if (data[k]) body[k] = data[k];
      }
      setData(await api.saveSettings(body));
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    setBusy("test");
    setError(null);
    try {
      setProbe(await api.testSources());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!data) {
    return (
      <div className="set-root">
        <div className="set-inner">
          {error ? <div className="set-error">{error}</div> : "Loading…"}
        </div>
      </div>
    );
  }

  return (
    <div className="set-root">
      <div className="set-inner">
      <h2>Settings</h2>

      <div className="set-tabs">
        {([
          ["sources", "Sources"],
          ["fetching", "Fetching"],
          ["ai", "AI"],
          ["library", "Library"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            className={tab === key ? "is-active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {error && <div className="set-error">{error}</div>}

      {tab === "library" && (
        <section>
          <h3>Start over</h3>
          <p className="set-note">
            Deletes every paper, author, link, note and highlight, and the PDFs and portraits
            stored alongside them. Your settings, API keys and conversations are kept — they
            are not the library, and losing them would make starting again harder rather than
            cleaner. There is no undo.
          </p>

          {wiped ? (
            <div className="set-wiped">{wiped}</div>
          ) : !wipeOpen ? (
            <button className="set-danger" onClick={() => setWipeOpen(true)}>
              Wipe the library…
            </button>
          ) : (
            <div className="set-confirm">
              <label>
                <span>
                  Type <code>delete my library</code> to confirm
                </span>
                <input
                  value={wipePhrase}
                  onChange={(e) => setWipePhrase(e.target.value)}
                  placeholder="delete my library"
                  autoFocus
                />
              </label>
              <div className="set-actions">
                <button
                  onClick={() => {
                    setWipeOpen(false);
                    setWipePhrase("");
                  }}
                >
                  Cancel
                </button>
                <button
                  className="set-danger"
                  disabled={wipePhrase.trim().toLowerCase() !== "delete my library"}
                  onClick={async () => {
                    try {
                      const r = await api.wipeLibrary(wipePhrase);
                      const gone = Object.entries(r.deleted)
                        .filter(([, n]) => n > 0)
                        .map(([k, n]) => `${n} ${k.replace("_", " ")}`)
                        .join(", ");
                      setWiped(
                        `Removed ${gone || "nothing"}${
                          r.files_removed ? ` and ${r.files_removed} file(s)` : ""
                        }.`,
                      );
                      setWipeOpen(false);
                    } catch (e) {
                      setError(e instanceof ApiError ? e.message : String(e));
                    }
                  }}
                >
                  Delete everything
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "ai" && (
        <SettingsAi
          settings={data}
          onField={field}
          onSave={save}
          saving={busy === "save"}
        />
      )}

      {tab === "sources" && (
      <>
      <section>
        <h3>Identification</h3>
        <label>
          <span>Contact email</span>
          <input
            type="email"
            value={data.contact_email}
            onChange={(e) => field("contact_email", e.target.value)}
            placeholder="you@example.com"
          />
          <small>
            Sent to OpenAlex, Crossref and Unpaywall. It raises your rate limits and is required
            by Unpaywall. Nothing else receives it.
          </small>
        </label>

        <label>
          <span>
            OpenAlex API key
            {data.openalex_key_set && <span className="set-ok-dot" title="A key is stored" />}
          </span>
          <input
            type="password"
            value={data.openalex_key}
            onChange={(e) => field("openalex_key", e.target.value)}
            placeholder={data.openalex_key_set ? "•••••••• stored" : "strongly recommended"}
            autoComplete="off"
          />
          <small>
            OpenAlex now meters requests against a daily budget. Without a key you share one
            small anonymous allowance with everything else on your address, and it runs out
            after about a hundred requests — adding papers then fails until midnight UTC. A free
            key at{" "}
            <a href="https://openalex.org/pricing" target="_blank" rel="noreferrer">
              openalex.org/pricing
            </a>{" "}
            gets its own $1/day. Reading one record is free; a title search costs 10 credits and
            a filtered list 1, out of 10,000 a day.
          </small>
        </label>

        <label>
          <span>Semantic Scholar API key</span>
          <input
            type="password"
            value={data.semantic_scholar_key}
            onChange={(e) => field("semantic_scholar_key", e.target.value)}
            placeholder={data.semantic_scholar_key_set ? "•••••••• stored" : "optional"}
            autoComplete="off"
          />
          <small>
            Rarely needed. Semantic Scholar now only powers the search suggestions and supplies
            one citation metric; OpenAlex provides the record, the references and the citing
            papers. Without a key this source gets a short retry budget and is skipped when it
            throttles, which costs you nothing structural. Leave blank to keep any stored key.
          </small>
        </label>

        <label>
          <span>Institution proxy prefix</span>
          <input
            value={data.institution_proxy}
            onChange={(e) => field("institution_proxy", e.target.value)}
            placeholder="https://login.ezproxy.your-uni.edu/login?url="
          />
          <small>
            Used to open a publisher page through your own library subscription when no
            open-access copy exists.
          </small>
        </label>
      </section>

      <div className="set-actions">
        <button className="primary" onClick={save} disabled={busy !== null}>
          {busy === "save" ? "Saving…" : "Save settings"}
        </button>
        <button onClick={test} disabled={busy !== null}>
          {busy === "test" ? "Testing…" : "Test sources"}
        </button>
        {savedAt && <span className="set-saved">Saved</span>}
      </div>

      {probe && (
        <section>
          <h3>Source status</h3>
          <ul className="set-probe">
            {Object.entries(probe).map(([name, r]) => (
              <li key={name}>
                <span className={r.ok ? "dot ok" : "dot bad"} />
                <strong>{SOURCE_LABEL[name] ?? name}</strong>
                <span className="dim">{r.detail}</span>
                {name === "semantic_scholar" && (
                  <span className="chip small">
                    {r.keyed ? "using your key" : "shared pool · optional"}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      </>
      )}

      {tab === "fetching" && (
      <>
      <section>
        <h3>Fetching</h3>
        <label className="row">
          <input
            type="checkbox"
            checked={data.fetch_references === "1"}
            onChange={(e) => field("fetch_references", e.target.checked ? "1" : "0")}
          />
          <span>Fetch the reference list when adding a paper</span>
        </label>
        <label className="row">
          <input
            type="checkbox"
            checked={data.fetch_citations === "1"}
            onChange={(e) => field("fetch_citations", e.target.checked ? "1" : "0")}
          />
          <span>Fetch citing papers when adding a paper</span>
        </label>
        <label className="row">
          <input
            type="checkbox"
            checked={data.auto_download_pdf === "1"}
            onChange={(e) => field("auto_download_pdf", e.target.checked ? "1" : "0")}
          />
          <span>Download the PDF when an open-access copy exists</span>
        </label>
        <label>
          <span>Maximum references or citations stored per paper</span>
          <input
            type="number"
            min={10}
            max={5000}
            value={data.citation_page_limit}
            onChange={(e) => field("citation_page_limit", e.target.value)}
          />
          <small>
            Only identifiers are stored, never whole papers. A higher number means slower adds
            for heavily cited work.
          </small>
        </label>
      </section>

      <div className="set-actions">
        <button className="primary" onClick={save} disabled={busy !== null}>
          {busy === "save" ? "Saving…" : "Save settings"}
        </button>
        {savedAt && <span className="set-saved">Saved</span>}
      </div>
      </>
      )}
      </div>
    </div>
  );
}
