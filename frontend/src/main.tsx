import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import Reader from "./components/Reader.tsx";

/**
 * Two entry points, chosen by the query string. `?paper=12` is the reader,
 * opened in its own tab by double-clicking a card; everything else is the app.
 * A hash or query check is enough here — a router would be one dependency for
 * one branch.
 */
const params = new URLSearchParams(window.location.search);
const paperId = Number(params.get("paper"));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {Number.isFinite(paperId) && paperId > 0 ? <Reader paperId={paperId} /> : <App />}
  </StrictMode>,
);
