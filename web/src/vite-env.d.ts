/// <reference types="vite/client" />

/**
 * The one build-time variable this app reads.
 *
 * `vite/client` types `import.meta.env` with an index signature, so any name at
 * all compiles and a typo would be `undefined` at runtime rather than an error
 * here. Declaring it narrows the one name that matters.
 *
 * Still optional, deliberately: it is absent in development, where same-origin
 * plus the dev proxy is correct. Production absence is caught in vite.config.ts,
 * which can fail the build -- something a type cannot do.
 */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
