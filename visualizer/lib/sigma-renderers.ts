// ---------------------------------------------------------------------------
// Custom sigma canvas renderers extracted from GraphCanvas.tsx (QA-004)
// ---------------------------------------------------------------------------
import type { Settings } from 'sigma/settings'
import type { NodeDisplayData, PartialButFor } from 'sigma/types'
import { LABEL_COLOR } from '@/lib/sigma-colors'

// The sigma NodeLabelDrawingFunction / NodeHoverDrawingFunction signatures use
// PartialButFor<NodeDisplayData, "x" | "y" | "size" | "label" | "color">.
// We alias it here for brevity.
type NodeData = PartialButFor<NodeDisplayData, 'x' | 'y' | 'size' | 'label' | 'color'>

/**
 * Draws a node label with a dark stroke outline for legibility on dark backgrounds.
 * Replaces sigma's default drawDiscNodeLabel.
 */
export function drawNodeLabel(
  context: CanvasRenderingContext2D,
  data: NodeData,
  settings: Settings
): void {
  if (!data.label) return
  const size: number = settings.labelSize ?? 11
  const font: string = settings.labelFont ?? 'sans-serif'
  context.font = `700 ${size}px ${font}`
  const x = data.x + data.size + 3
  const y = data.y + size / 4
  context.lineJoin = 'round'
  context.lineWidth = 3
  context.strokeStyle = 'rgba(3, 4, 10, 0.95)'
  context.strokeText(data.label, x, y)
  context.fillStyle = LABEL_COLOR
  context.fillText(data.label, x, y)
}

/**
 * Draws a rounded-rect hover tooltip box + orange highlight ring around the node.
 * Replaces sigma's default drawDiscNodeHover.
 */
export function drawNodeHover(
  context: CanvasRenderingContext2D,
  data: NodeData,
  settings: Settings
): void {
  if (!data.label) return
  const size: number = settings.labelSize ?? 11
  const font: string = settings.labelFont ?? 'sans-serif'
  context.font = `700 ${size}px ${font}`
  const PADDING = 4
  const textWidth = context.measureText(data.label).width
  const boxWidth = Math.round(textWidth + PADDING * 2 + 4)
  const boxHeight = Math.round(size + PADDING * 2)
  const radius = Math.max(data.size, size / 2) + PADDING
  const bx = data.x + radius
  const by = data.y - boxHeight / 2
  const r = 4

  // Background pill
  context.fillStyle = 'rgba(10, 14, 28, 0.94)'
  context.shadowOffsetX = 0
  context.shadowOffsetY = 2
  context.shadowBlur = 10
  context.shadowColor = 'rgba(0, 0, 0, 0.7)'
  context.beginPath()
  context.moveTo(bx + r, by)
  context.lineTo(bx + boxWidth - r, by)
  context.arcTo(bx + boxWidth, by, bx + boxWidth, by + r, r)
  context.lineTo(bx + boxWidth, by + boxHeight - r)
  context.arcTo(bx + boxWidth, by + boxHeight, bx + boxWidth - r, by + boxHeight, r)
  context.lineTo(bx + r, by + boxHeight)
  context.arcTo(bx, by + boxHeight, bx, by + boxHeight - r, r)
  context.lineTo(bx, by + r)
  context.arcTo(bx, by, bx + r, by, r)
  context.closePath()
  context.fill()
  context.shadowBlur = 0
  context.strokeStyle = 'rgba(249, 115, 22, 0.5)'
  context.lineWidth = 1
  context.stroke()

  // Highlight ring
  context.beginPath()
  context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2)
  context.strokeStyle = '#f97316'
  context.lineWidth = 2
  context.stroke()

  // Label text
  context.fillStyle = '#f97316'
  context.fillText(data.label, bx + PADDING + 2, data.y + size / 4)
}
