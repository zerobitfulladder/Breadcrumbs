/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  /** "1" when built by `./breadcrumbs pages`: read a frozen export, no backend. */
  readonly VITE_STATIC?: string;
  /** Where the frozen export lives, relative to the page. */
  readonly VITE_STATIC_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
