/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base URL of the SPA itself. Standard Vite injection — used by Vue Router's
   * `createWebHistory(import.meta.env.BASE_URL)`.
   */
  readonly BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
