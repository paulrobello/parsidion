// lib/types.ts
// Cross-cutting shared types. Lives outside any module that imports server
// infrastructure (child_process, fs, server-only) so client components can
// import from here without dragging server code into their bundle.
//
// ARC-041: previously `CommitEntry` was exported from
// app/api/note/history/route.ts, a module that imports child_process.
// `'use client'` components `import type { CommitEntry }` from there — the
// `type` keyword erases the runtime import, so the live bundle is fine,
// but the structural hazard is real: dropping one keyword pulls
// child_process into the client graph. Moving the type here breaks the
// edge entirely.

/** One row of `git log --follow --format=%H|%ai|%s` for a note. */
export interface CommitEntry {
  hash: string
  shortHash: string
  date: string
  message: string
}
