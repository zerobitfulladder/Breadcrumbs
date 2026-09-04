import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { AiCapabilities, AiEndpoint, AiModel, AiProvider, Settings } from "../api";
import ModelPicker, { perMillion } from "./ModelPicker";

interface Props {
  settings: Settings;
  onField: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
  onSave: () => void;
  saving: boolean;
}

export default function SettingsAi({ settings, onField, onSave, saving }: Props) {
  const [providers, setProviders] = useState<AiProvider[]>([]);
  const [models, setModels] = useState<Record<string, AiModel[]>>({});
  const [loading, setLoading] = useState<string | null>(null);
  const [caps, setCaps] = useState<AiCapabilities | null>(null);
  const [endpoints, setEndpoints] = useState<AiEndpoint[] | null>(null);
  const [routing, setRouting] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([api.aiProviders(), api.aiCapabilities()]);
      setProviders(p.providers);
      setCaps(c);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function fetchModels(provider: string) {
    setLoading(provider);
    setError(null);
    try {
      const { models: got } = await api.aiModels(provider);
      setModels((m) => ({ ...m, [provider]: got }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(null);
    }
  }

  const chosen = settings.assistant_provider;
  const available = chosen ? models[chosen] : undefined;
  const picked = available?.find((m) => m.id === settings.assistant_model);
  const model = settings.assistant_model;

  // Which upstream providers serve this model, and for how much. OpenRouter is
  // the only one that routes across other people's hardware, so the request is
  // skipped entirely elsewhere.
  useEffect(() => {
    if (chosen !== "openrouter" || !model) {
      setEndpoints(null);
      return;
    }
    let live = true;
    setRouting(true);
    api
      .aiModelEndpoints(chosen, model)
      .then((r) => live && setEndpoints(r.endpoints))
      .catch(() => live && setEndpoints([]))
      .finally(() => live && setRouting(false));
    return () => {
      live = false;
    };
  }, [chosen, model]);

  return (
    <>
      {error && <div className="set-error">{error}</div>}

      <section>
        <h3>Provider keys</h3>
        {providers.map((p) => {
          const field = p.setting as keyof Settings;
          const isSet = (settings as unknown as Record<string, boolean>)[`${p.setting}_set`];
          return (
            <label key={p.key}>
              <span>
                {p.label}
                {isSet && <span className="set-ok-dot" title="A key is stored" />}
              </span>
              <input
                type="password"
                value={String(settings[field] ?? "")}
                onChange={(e) => onField(field, e.target.value as Settings[typeof field])}
                placeholder={isSet ? "•••••••• stored" : "not configured"}
                autoComplete="off"
              />
              <small>
                Keys at{" "}
                <a href={p.docs} target="_blank" rel="noreferrer">
                  {p.docs}
                </a>
                . Leave blank to keep the stored key.
              </small>
            </label>
          );
        })}
      </section>

      <section>
        <h3>Web search</h3>
        <p className="set-note">
          A separate service rather than a provider's own. Search availability differs sharply
          between providers — Gemini grounds on Google, DeepSeek has none — so tying tasks to
          provider search would make most model choices unusable. Keeping it here means any
          model can search.
        </p>
        <label>
          <span>Search provider</span>
          <select
            value={settings.search_provider}
            onChange={(e) => onField("search_provider", e.target.value)}
          >
            <option value="brave">Brave Search</option>
            <option value="tavily">Tavily</option>
          </select>
        </label>
        <label>
          <span>
            Search API key
            {settings.search_api_key_set && <span className="set-ok-dot" />}
          </span>
          <input
            type="password"
            value={settings.search_api_key}
            onChange={(e) => onField("search_api_key", e.target.value)}
            placeholder={settings.search_api_key_set ? "•••••••• stored" : "needed for web search"}
            autoComplete="off"
          />
        </label>
      </section>

      <section>
        <h3>Assistant model</h3>
        <p className="set-note">
          One model runs the assistant, and it must support tool calling. Models that do not
          are filtered out where the provider says so — picking one would fail silently at the
          first tool call.
        </p>

        <label>
          <span>Provider</span>
          <select
            value={settings.assistant_provider}
            onChange={(e) => {
              onField("assistant_provider", e.target.value);
              onField("assistant_model", "");
              onField("assistant_route", "");
            }}
          >
            <option value="">choose a provider</option>
            {providers.map((p) => (
              <option key={p.key} value={p.key} disabled={!p.has_key}>
                {p.label}
                {p.has_key ? "" : " (save a key first)"}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>Model</span>
          <div className="set-inline">
            <ModelPicker
              models={available}
              value={settings.assistant_model}
              onChange={(id) => {
                onField("assistant_model", id);
                // A pin names a provider of the old model; it rarely serves
                // the new one, and a stale pin fails every turn.
                onField("assistant_route", "");
              }}
              disabled={!chosen}
            />
            <button onClick={() => chosen && void fetchModels(chosen)} disabled={!chosen || loading !== null}>
              {loading === chosen ? "Loading…" : "Load models"}
            </button>
          </div>
          {available && (
            <small>
              {available.length} tool-capable model{available.length === 1 ? "" : "s"} available.
              Type to filter.
            </small>
          )}
        </label>

        {chosen === "openrouter" && model && (
          <label>
            <span>Upstream provider</span>
            <select
              value={settings.assistant_route}
              onChange={(e) => onField("assistant_route", e.target.value)}
              disabled={routing || !endpoints?.length}
            >
              <option value="">
                {routing ? "Loading…" : "Automatic — OpenRouter picks"}
              </option>
              {(endpoints ?? []).map((e, i) => (
                <option key={`${e.provider_name}-${i}`} value={e.provider_name} disabled={e.tools === false}>
                  {e.provider_name}
                  {e.context ? ` · ${Math.round(e.context / 1000)}k` : ""}
                  {perMillion(e.prompt_price) ? ` · ${perMillion(e.prompt_price)}/M in` : ""}
                  {perMillion(e.completion_price) ? ` · ${perMillion(e.completion_price)}/M out` : ""}
                  {e.tools === false ? " · no tools" : ""}
                </option>
              ))}
            </select>
            <small>
              {endpoints === null || routing
                ? "Asking OpenRouter which providers serve this model…"
                : endpoints.length
                  ? `${endpoints.length} provider${endpoints.length === 1 ? "" : "s"} serve this model, cheapest first. ` +
                    "Automatic lets OpenRouter choose and fall back when one is down; pinning one fixes the price and the context limit, and the turn fails rather than moving elsewhere."
                  : "OpenRouter did not list any providers for this model."}
            </small>
            {settings.assistant_route &&
              !(endpoints ?? []).some((e) => e.provider_name === settings.assistant_route) && (
                <small className="set-warn">
                  “{settings.assistant_route}” is pinned but does not serve this model. Turns will
                  fail until you change it back to automatic.
                </small>
              )}
          </label>
        )}

        <label>
          <span>Thinking</span>
          <select
            value={settings.assistant_thinking}
            onChange={(e) => onField("assistant_thinking", e.target.value)}
          >
            <option value="off">Off</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
          <small>
            {picked?.reasoning === false
              ? "This model does not reason, so the setting is ignored for it."
              : picked?.reasoning
                ? "This model supports reasoning. Higher settings think longer before answering, which helps on questions that need several steps and costs more."
                : "Sent as reasoning effort. Models that cannot reason have it dropped automatically, so it is safe to leave on."}
          </small>
        </label>

        <label>
          <span>Maximum tool rounds per turn</span>
          <input
            type="number"
            min={2}
            max={40}
            value={settings.assistant_max_rounds}
            onChange={(e) => onField("assistant_max_rounds", e.target.value)}
          />
          <small>
            A stop so a confused turn cannot loop indefinitely. Twelve is enough for a search,
            a fetch and a write with room to recover from a mistake.
          </small>
        </label>

        <div className="set-actions">
          <button className="primary" onClick={onSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button onClick={() => void load()}>Refresh status</button>
        </div>
      </section>

      <section>
        <h3>Skills</h3>
        <p className="set-note">
          Only a skill's name and description sit in the system prompt. The assistant loads
          the full instructions itself when it decides one applies, which keeps the standing
          prompt short — the SQL skill alone would otherwise add its whole schema to every
          message. Skills are Markdown files under <code>backend/src/backend/ai/skills/</code>.
        </p>
        {caps?.skills.length ? (
          <ul className="set-caps">
            {caps.skills.map((sk) => (
              <li key={sk.name}>
                <div className="set-cap-head">
                  <code>{sk.name}</code>
                  <span className="set-cap-size">{(sk.size / 1024).toFixed(1)} KB loaded on demand</span>
                </div>
                <p>{sk.description}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="set-note">No skills found.</p>
        )}
      </section>

      <section>
        <h3>
          Tools
          {caps && <span className="set-count">{caps.tools.length}</span>}
        </h3>
        <p className="set-note">
          Everything the assistant can do. It reads your library, searches the web and writes
          annotations — but it cannot add papers: it offers them and the preview opens for you
          to decide. Writes are marked, and each one is shown in the conversation as it
          happens.
        </p>
        <button className="link" onClick={() => setShowTools((v) => !v)}>
          {showTools ? "Hide the list" : "Show the list"}
        </button>
        {showTools && caps && (
          <ul className="set-caps">
            {caps.tools.map((t) => (
              <li key={t.name} className={t.is_write ? "is-write" : ""}>
                <div className="set-cap-head">
                  <code>{t.name}</code>
                  {t.is_write && <span className="set-cap-write">writes</span>}
                  {!!t.params.length && (
                    <span className="set-cap-params">({t.params.join(", ")})</span>
                  )}
                </div>
                <p>{t.description}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
