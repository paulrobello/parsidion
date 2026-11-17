/**
 * Frontmatter/body derivations for the reading pane, extracted from
 * ReadingPane so the component function stays shallow: the regex walks
 * (date, confidence, related stems), the linked-only filter, and the
 * display-content transformation are pure functions here instead of
 * inline closures in the render body.
 */

export interface NoteView {
  /** `date:` frontmatter value, or null. */
  noteDate: string | null
  /** `confidence:` frontmatter value, or null. */
  confidence: string | null
  /** Stems wiki-linked from this note's `related:` frontmatter, deduped, in order. */
  relatedStems: string[]
  /** Graph-linked stems not already surfaced via the related frontmatter. */
  linkedOnly: string[]
  /** Body markdown with frontmatter stripped and wikilinks converted to markdown links. */
  displayContent: string
}

export function deriveNoteView(content: string, linkedStems: string[], nodeId: string): NoteView {
  const fm = content.match(/^---\n([\s\S]*?)\n---/)
  const fmBody = fm?.[1] ?? ''
  const noteDate = fmBody.match(/^date:\s*(.+)$/m)?.[1]?.trim() ?? null
  const confidence = fmBody.match(/^confidence:\s*(.+)$/m)?.[1]?.trim() ?? null
  const relatedStems: string[] = (() => {
    const relatedLine = fmBody.match(/^related:\s*(.+)$/m)
    if (!relatedLine) return []
    const stems: string[] = []
    const re = /\[\[([^\]]+)\]\]/g
    let m: RegExpExecArray | null
    while ((m = re.exec(relatedLine[1])) !== null) stems.push(m[1])
    return [...new Set(stems)]
  })()
  const relatedSet = new Set(relatedStems)
  const linkedOnly = linkedStems.filter(s => !relatedSet.has(s) && s !== nodeId)
  const displayContent = content
    .replace(/^---[\s\S]*?---\n/, '')
    .replace(/\[\[([^\]]+)\]\]/g, (_, stem) => `[${stem}](wikilink:${encodeURIComponent(stem)})`)
  return { noteDate, confidence, relatedStems, linkedOnly, displayContent }
}
