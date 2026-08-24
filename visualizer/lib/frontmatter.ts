export interface FrontmatterFields {
  date: string
  type: string
  tags: string[]
  confidence: string
  project: string
  sources: string[]
  related: string[]   // bare stems, e.g. ["note-one", "note-two"]
  /** Verbatim raw lines for frontmatter keys this editor doesn't model (e.g.
   * `provenance`, `session_id`, or block-style YAML lists). Round-tripped on
   * save so editing a note never silently drops fields the UI doesn't know about. */
  extra: string
}

const DEFAULTS: FrontmatterFields = {
  date: new Date().toISOString().slice(0, 10),
  type: 'pattern',
  tags: [],
  confidence: 'medium',
  project: '',
  sources: [],
  related: [],
  extra: '',
}

const KNOWN_KEYS = new Set(['date', 'type', 'tags', 'confidence', 'project', 'sources', 'related'])

// ARC-005: quoting rules mirror core.vault_index's serialize_frontmatter so
// the Python tools and this editor emit (and read) byte-identical frontmatter.
// The shared contract is pinned by tests/fixtures/parity/frontmatter.json.
const YAML_SPECIAL_PREFIXES = '-?:[]{}#&*!|>\'"%@`'
const YAML_COERCED_WORDS = new Set(['true', 'yes', 'false', 'no', 'null', '~', ''])

function scalarNeedsQuotes(text: string): boolean {
  if (!text || text !== text.trim()) return true
  if (YAML_SPECIAL_PREFIXES.includes(text[0])) return true
  if (text.includes(': ') || text.endsWith(':')) return true
  if (text.includes(' #')) return true
  if (YAML_COERCED_WORDS.has(text.toLowerCase())) return true
  if (text.trim() !== '' && !Number.isNaN(Number(text))) return true
  return false
}

function quoteYaml(text: string): string {
  if (text.includes('"') && !text.includes("'")) return `'${text}'`
  const escaped = text.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  return `"${escaped}"`
}

function formatScalar(value: string): string {
  return scalarNeedsQuotes(value) ? quoteYaml(value) : value
}

function formatListItem(item: string, alwaysQuote: boolean): string {
  const structural = /[,[\]"']/.test(item) || item.includes(': ')
  if (alwaysQuote || structural || item !== item.trim() || !item) return quoteYaml(item)
  return item
}

/** Parse `---\n...\n---` frontmatter + body from a full markdown string. */
export function parseFrontmatter(content: string): { fields: FrontmatterFields; body: string } {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?/)
  if (!match) return { fields: { ...DEFAULTS }, body: content }

  const raw = match[1]
  const body = content.slice(match[0].length)

  const get = (key: string): string | null => {
    const m = raw.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'))
    return m ? m[1].trim().replace(/^["']|["']$/g, '') : null
  }

  const parseInlineArray = (val: string | null): string[] => {
    if (!val) return []
    // Handle YAML inline array: [a, b, c]. Split on commas OUTSIDE quotes so
    // quoted items containing commas survive (mirrors Python _split_list_items).
    const inner = val.match(/^\[(.*)\]$/)
    if (inner) {
      const items: string[] = []
      let current = ''
      let inQuote: string | null = null
      for (const ch of inner[1]) {
        if (inQuote) {
          current += ch
          if (ch === inQuote) inQuote = null
        } else if (ch === '"' || ch === "'") {
          inQuote = ch
          current += ch
        } else if (ch === ',') {
          items.push(current.trim())
          current = ''
        } else {
          current += ch
        }
      }
      if (current.trim()) items.push(current.trim())
      return items.map(s => s.replace(/^["']|["']$/g, '')).filter(Boolean)
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

  // Walk top-level key blocks (a key line plus any indented continuation lines,
  // e.g. block-style YAML lists) and keep verbatim any block whose key isn't
  // one of the fields this editor understands.
  const rawLines = raw.split('\n')
  const extraLines: string[] = []
  let i = 0
  while (i < rawLines.length) {
    const line = rawLines[i]
    const keyMatch = line.match(/^([A-Za-z_][\w-]*):/)
    if (!keyMatch) { i++; continue }
    const key = keyMatch[1]
    const blockLines = [line]
    let j = i + 1
    while (j < rawLines.length && /^\s/.test(rawLines[j]) && rawLines[j].trim() !== '') {
      blockLines.push(rawLines[j])
      j++
    }
    if (!KNOWN_KEYS.has(key)) extraLines.push(...blockLines)
    i = j
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
      extra: extraLines.join('\n'),
    },
    body,
  }
}

/** Serialize frontmatter fields + body back into a full markdown string. */
export function serializeFrontmatter(fields: FrontmatterFields, body: string): string {
  const lines: string[] = ['---']
  lines.push(`date: ${formatScalar(fields.date)}`)
  lines.push(`type: ${formatScalar(fields.type)}`)
  lines.push(`tags: [${fields.tags.map(t => formatListItem(t, false)).join(', ')}]`)

  if (fields.project) {
    lines.push(`project: ${formatScalar(fields.project)}`)
  }

  lines.push(`confidence: ${formatScalar(fields.confidence)}`)
  lines.push(`sources: [${fields.sources.map(s => formatListItem(s, false)).join(', ')}]`)

  const relatedFormatted = fields.related.map(s => `"[[${s}]]"`).join(', ')
  lines.push(`related: [${relatedFormatted}]`)

  if (fields.extra) {
    lines.push(fields.extra)
  }

  lines.push('---')
  lines.push('')

  return lines.join('\n') + body
}

export function defaultFields(): FrontmatterFields {
  return { ...DEFAULTS, tags: [], sources: [], related: [], extra: '' }
}
