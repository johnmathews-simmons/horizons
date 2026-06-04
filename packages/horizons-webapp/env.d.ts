/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base URL of the Horizons public REST API (no trailing slash).
   * Example: "http://localhost:8000" in dev, "https://api.horizons.example" in prod.
   * The webapp talks to the API exactly the same way external customers do — there
   * is no internal back-channel. See docs/4. services.md.
   */
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
