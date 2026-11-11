/**
 * Regression tests for the VaultStats health poll URL.
 *
 * fetchHealth appended "&fast=1" unconditionally, so when no vault param
 * preceded it the request went out as /api/health&fast=1 — a malformed URL
 * the route answered 404. Both vault states must produce a well-formed query.
 */
import { describe, test, expect, vi, afterEach } from 'bun:test'
import { render, waitFor, cleanup } from '@testing-library/react'
import { VaultStats } from './VaultStats'

const HEALTH = {
  vault: 'test-vault',
  overall: 90,
  grade: 'A',
  dimensions: [] as unknown[],
  note_types: {} as Record<string, number>,
  warnings: [] as string[],
}

const originalFetch = globalThis.fetch

function stubFetch() {
  const fetchMock = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const url = String(input)
    const body =
      url.startsWith('/api/health') ? HEALTH
      : url.startsWith('/api/summarizer/status') ? { running: false, progress: null, pendingSummaries: 0 }
      : { pendingSummaries: 0 }
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
  })
  globalThis.fetch = fetchMock as unknown as typeof fetch
  return fetchMock
}

function healthUrls(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls.map(call => String(call[0])).filter(url => url.startsWith('/api/health'))
}

describe('VaultStats health URL', () => {
  afterEach(() => {
    cleanup()
    globalThis.fetch = originalFetch
  })

  test('uses ?fast=1 when no vault param precedes it', async () => {
    const fetchMock = stubFetch()
    render(<VaultStats vault={null} totalNotes={3} />)
    await waitFor(() => {
      expect(healthUrls(fetchMock).length).toBeGreaterThan(0)
    })
    expect(healthUrls(fetchMock)).toContain('/api/health?fast=1')
  })

  test('uses &fast=1 after the vault param', async () => {
    const fetchMock = stubFetch()
    render(<VaultStats vault="MyVault" totalNotes={3} />)
    await waitFor(() => {
      expect(healthUrls(fetchMock).length).toBeGreaterThan(0)
    })
    expect(healthUrls(fetchMock)).toContain('/api/health?vault=MyVault&fast=1')
  })
})
