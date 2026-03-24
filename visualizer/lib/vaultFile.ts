// lib/vaultFile.ts
// Shared type used by both the API route and client hook.
// Keep this file import-free so it is safe to use in both environments.

export interface VaultFile {
  /** Filename without extension — e.g. "foo" for "Patterns/foo.md" */
  stem: string
  /** Path relative to vault root — e.g. "Patterns/foo.md" */
  path: string
  /** Frontmatter `type` field, if present */
  noteType?: string
}
