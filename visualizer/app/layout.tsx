import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Vault Visualizer',
  description: 'Knowledge graph explorer for your ClaudeVault',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  )
}
