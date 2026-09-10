/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  /** "1" when built by `./breadcrumbs pages`: read a frozen export, no backend. */
  readonly VITE_STATIC?: string;
  /** Where the frozen export lives, relative to the page. */
  readonly VITE_STATIC_BASE?: string;
  /** Build stamp, appended to the export's URLs so a new build is not read
      from a cache holding the last one. Set by `./breadcrumbs pages`. */
  readonly VITE_STATIC_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
