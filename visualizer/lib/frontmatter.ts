export interface FrontmatterFields {
  date: string
  type: string
  tags: string[]
  confidence: string
  project: string
  sources: string[]
  related: string[]   // bare stems, e.g. ["note-one", "note-two"]
}

const DEFAULTS: FrontmatterFields = {
  date: new Date().toISOString().slice(0, 10),
  type: 'pattern',
  tags: [],
  confidence: 'medium',
  project: '',
  sources: [],
  related: [],
}

/** Parse `---\n...\n---` frontmatter + body from a full markdown string. */
export function parseFrontmatter(content: string): { fields: FrontmatterFields; body: string } {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?/)
  if (!match) return { fields: { ...DEFAULTS }, body: content }

  const raw = match[1]
  const body = content.slice(match[0].length)

  const get = (key: string): string | null => {
    const m = raw.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'))
    return m ? m[1].trim() : null
  }

  const parseInlineArray = (val: string | null): string[] => {
    if (!val) return []
    // Handle YAML inline array: [a, b, c]
    const inner = val.match(/^\[(.*)\]$/)
    if (inner) {
      return inner[1].split(',').map(s => s.trim().replace(/^["']|["']$/g, '')).filter(Boolean)
    }
    return val ? [val] : []
  }

  const parseRelated = (val: string | null): string[] => {
    if (!val) return []
    const stems: string[] = []
    const re = /\[\[([^\]]+)\]\]/g
    let m: RegExpExecArray | null
    while ((m = re.exec(val)) !== null) stems.push(m[1])
    return [...new Set(stems)]
  }

  return {
    fields: {
      date: get('date') ?? DEFAULTS.date,
      type: get('type') ?? DEFAULTS.type,
      tags: parseInlineArray(get('tags')),
      confidence: get('confidence') ?? DEFAULTS.confidence,
      project: get('project') ?? '',
      sources: parseInlineArray(get('sources')),
      related: parseRelated(get('related')),
    },
    body,
  }
}

/** Serialize frontmatter fields + body back into a full markdown string. */
export function serializeFrontmatter(fields: FrontmatterFields, body: string): string {
  const lines: string[] = ['---']
  lines.push(`date: ${fields.date}`)
  lines.push(`type: ${fields.type}`)
  lines.push(`tags: [${fields.tags.join(', ')}]`)

  if (fields.project) {
    lines.push(`project: ${fields.project}`)
  }

  lines.push(`confidence: ${fields.confidence}`)
  lines.push(`sources: [${fields.sources.join(', ')}]`)

  const relatedFormatted = fields.related.map(s => `"[[${s}]]"`).join(', ')
  lines.push(`related: [${relatedFormatted}]`)

  lines.push('---')
  lines.push('')

  return lines.join('\n') + body
}

export function defaultFields(): FrontmatterFields {
  return { ...DEFAULTS, tags: [], sources: [], related: [] }
}
