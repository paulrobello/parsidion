import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Parsidion Visualizer',
  description: 'Runtime-agnostic knowledge graph explorer for your Parsidion vault',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  )
}
