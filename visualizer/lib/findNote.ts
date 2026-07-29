// lib/findNote.ts
// QA-006: Shared async note resolver. The previous triplicated copies in
// note/route.ts (async), note/history/route.ts (sync), and note/diff/route.ts
// (sync) had already diverged once — the first conversion to async fs/promises
// only updated one site and the others kept blocking the Node event loop with
// readdirSync on every history/diff request. Extracting the helper to lib/
// (next to guardPath, which the same SEC-012 comment in vaultResolver.ts
// already deduplicated) follows the existing pattern for this exact problem.
//
// Resolves QA-012 for note/history and note/diff (the sync-fs cleanup is a
// side effect of importing one async helper).
import fs from 'fs/promises'
import path from 'path'

/**
 * Recursively walk `dir` and return the first `.md` file whose stem (filename
 * without the `.md` extension) equals `stemToFind`, or `null` if no match.
 *
 * Skips dotfiles (entries whose name starts with `.`). Returns `null` for
 * unreadable directories rather than throwing — the caller's vault may have
 * permission-restricted subtrees.
 *
 * @param dir - Absolute directory to walk.
 * @param stemToFind - Note stem (filename without `.md`) to locate.
 * @returns Absolute path to the first match, or `null`.
 */
export async function findNote(
  dir: string,
  stemToFind: string,
): Promise<string | null> {
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true })
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        const found = await findNote(full, stemToFind)
        if (found) return found
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        const fileStem = entry.name.replace(/\.md$/, '')
        if (fileStem === stemToFind) return full
      }
    }
  } catch {
    /* skip unreadable dirs */
  }
  return null
}
