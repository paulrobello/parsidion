// lib/apiAuth.test.ts
// SEC-102: pin the auth/CSRF contract for the visualizer API.
//
// These cases pin the behaviour documented in lib/apiAuth.ts:
//   * requireToken returns null when VISUALIZER_TOKEN is unset (single-user
//     default unchanged).
//   * requireToken returns 401 when the token is set but the header is
//     missing, malformed, or wrong.
//   * requireToken returns null for a constant-time-equal match.
//   * requireSameOrigin rejects Sec-Fetch-Site: cross-site and passes every
//     other value (including absent — non-browser clients). Curl has no
//     Sec-Fetch-Site header, which is exactly why reads also need
//     requireToken: same-origin alone is bypassable by any local client.

import { describe, it, expect, afterEach } from 'bun:test'
import { NextRequest, NextResponse } from 'next/server'
import { requireToken, requireSameOrigin, requireAuth, requireLocalHost, withApi, runGuards } from './apiAuth'

function makeRequest(headers: Record<string, string> = {}, method = 'GET'): NextRequest {
  const url = 'http://localhost:3999/api/stats'
  return new NextRequest(url, { method, headers })
}

describe('requireToken', () => {
  const originalToken = process.env.VISUALIZER_TOKEN

  afterEach(() => {
    if (originalToken === undefined) {
      delete process.env.VISUALIZER_TOKEN
    } else {
      process.env.VISUALIZER_TOKEN = originalToken
    }
  })

  it('returns null when VISUALIZER_TOKEN is unset', () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest({})
    expect(requireToken(req)).toBeNull()
  })

  it('returns null when VISUALIZER_TOKEN is empty', () => {
    process.env.VISUALIZER_TOKEN = ''
    const req = makeRequest({})
    expect(requireToken(req)).toBeNull()
  })

  it('returns 401 when token is set but header is missing', () => {
    process.env.VISUALIZER_TOKEN = 'secret'
    const req = makeRequest({})
    const res = requireToken(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(401)
  })

  it('returns 401 when the Authorization scheme is wrong', () => {
    process.env.VISUALIZER_TOKEN = 'secret'
    const req = makeRequest({ authorization: 'Basic c2VjcmV0' })
    const res = requireToken(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(401)
  })

  it('returns 401 when the bearer value is wrong', () => {
    process.env.VISUALIZER_TOKEN = 'secret'
    const req = makeRequest({ authorization: 'Bearer wrong-value' })
    const res = requireToken(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(401)
  })

  it('returns 401 on a length-mismatch attempt (no timing side channel)', () => {
    process.env.VISUALIZER_TOKEN = 'secret'
    // One char short — length check rejects before any comparison.
    const req = makeRequest({ authorization: 'Bearer secre' })
    const res = requireToken(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(401)
  })

  it('returns null for an exact constant-time-equal match', async () => {
    process.env.VISUALIZER_TOKEN = 'match-me-exactly'
    const req = makeRequest({ authorization: 'Bearer match-me-exactly' })
    expect(requireToken(req)).toBeNull()
  })
})

describe('requireSameOrigin', () => {
  it('returns 403 when Sec-Fetch-Site is cross-site', () => {
    const req = makeRequest({ 'sec-fetch-site': 'cross-site' })
    const res = requireSameOrigin(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(403)
  })

  it('returns null when Sec-Fetch-Site is same-origin', () => {
    const req = makeRequest({ 'sec-fetch-site': 'same-origin' })
    expect(requireSameOrigin(req)).toBeNull()
  })

  it('returns null when Sec-Fetch-Site is absent (non-browser client)', () => {
    // Curl has no Sec-Fetch-Site header — this is exactly why reads also
    // need requireToken: same-origin alone is bypassable by any local
    // non-browser client (SEC-102 finding #2).
    const req = makeRequest({})
    expect(requireSameOrigin(req)).toBeNull()
  })
})

describe('requireAuth (mutations)', () => {
  const originalToken = process.env.VISUALIZER_TOKEN

  afterEach(() => {
    if (originalToken === undefined) {
      delete process.env.VISUALIZER_TOKEN
    } else {
      process.env.VISUALIZER_TOKEN = originalToken
    }
  })

  it('returns 415 when a POST lacks application/json', () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest({}, 'POST')
    const res = requireAuth(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(415)
  })

  it('passes a POST with application/json and no token configured', () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest({ 'content-type': 'application/json' }, 'POST')
    expect(requireAuth(req)).toBeNull()
  })

  it('returns 401 for a POST with correct Content-Type but wrong token', () => {
    process.env.VISUALIZER_TOKEN = 'secret'
    const req = makeRequest(
      { 'content-type': 'application/json', authorization: 'Bearer wrong' },
      'POST',
    )
    const res = requireAuth(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(401)
  })

  it('passes a POST with correct Content-Type and correct token', () => {
    process.env.VISUALIZER_TOKEN = 'secret'
    const req = makeRequest(
      { 'content-type': 'application/json', authorization: 'Bearer secret' },
      'POST',
    )
    expect(requireAuth(req)).toBeNull()
  })
})

describe('requireLocalHost (SEC-001)', () => {
  const originalPort = process.env.PORT

  afterEach(() => {
    if (originalPort === undefined) {
      delete process.env.PORT
    } else {
      process.env.PORT = originalPort
    }
  })

  it('returns 403 when Host is an attacker hostname (DNS rebinding)', () => {
    // A rebound DNS name still sends its own hostname in Host — that is the
    // one header the attacking page cannot rewrite.
    const req = makeRequest({ host: 'attacker.example:3999' })
    const res = requireLocalHost(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(403)
  })

  it('returns null for Host: localhost:3999', () => {
    const req = makeRequest({ host: 'localhost:3999' })
    expect(requireLocalHost(req)).toBeNull()
  })

  it('returns null for Host: 127.0.0.1 (no port, default port in effect)', () => {
    delete process.env.PORT
    const req = makeRequest({ host: '127.0.0.1' })
    expect(requireLocalHost(req)).toBeNull()
  })

  it('returns null for Host: [::1]:3999 (bracketed IPv6 loopback)', () => {
    const req = makeRequest({ host: '[::1]:3999' })
    expect(requireLocalHost(req)).toBeNull()
  })

  it('returns 403 for a non-loopback IP', () => {
    const req = makeRequest({ host: '192.168.1.5:3999' })
    const res = requireLocalHost(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(403)
  })

  it('returns 403 when the port does not match the server port', () => {
    const req = makeRequest({ host: 'localhost:8080' })
    const res = requireLocalHost(req)
    expect(res).not.toBeNull()
    expect(res!.status).toBe(403)
  })

  it('returns null when Host is absent (non-browser client; token covers it)', () => {
    // Browsers always send Host, and a rebinding page cannot forge the value
    // — so an absent header is a non-browser client, the same class
    // requireSameOrigin passes through to requireToken.
    const req = new NextRequest('http://localhost:3999/api/stats')
    req.headers.delete('host')
    expect(requireLocalHost(req)).toBeNull()
  })

  it('runGuards rejects a rebound Host before any other guard', async () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest({ host: 'attacker.example:3999', 'sec-fetch-site': 'same-origin' }, 'POST')
    let called = false
    const handler = withApi(() => {
      called = true
      return NextResponse.json({ ok: true })
    }, { mutation: true })
    const res = await handler(req)
    expect(res.status).toBe(403)
    expect(called).toBe(false)
  })
})

describe('withApi / runGuards (ARC-014)', () => {
  const originalToken = process.env.VISUALIZER_TOKEN

  afterEach(() => {
    if (originalToken === undefined) {
      delete process.env.VISUALIZER_TOKEN
    } else {
      process.env.VISUALIZER_TOKEN = originalToken
    }
  })

  it('withApi returns an anonymous function (the wrapper signature)', () => {
    // ARC-014 enumeration test relies on this: a wrapped export's `name` is
    // empty because `withApi` returns an arrow expression. A plain
    // `export async function GET` would keep its name. Pin the contract so
    // a future refactor of withApi cannot silently break the enumeration test.
    const wrapped = withApi(() => NextResponse.json({ ok: true }))
    expect(typeof wrapped).toBe('function')
    expect(wrapped.name).toBe('')
  })

  it('withApi delegates to the handler when guards pass', async () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest({})
    const handler = withApi(() => NextResponse.json({ ok: true }))
    const res = await handler(req)
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ ok: true })
  })

  it('withApi short-circuits on token failure (401) without calling handler', async () => {
    process.env.VISUALIZER_TOKEN = 'secret'
    const req = makeRequest({})
    let called = false
    const handler = withApi(() => {
      called = true
      return NextResponse.json({ ok: true })
    })
    const res = await handler(req)
    expect(res.status).toBe(401)
    expect(called).toBe(false)
  })

  it('withApi short-circuits on cross-site (403) without calling handler', async () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest({ 'sec-fetch-site': 'cross-site' })
    let called = false
    const handler = withApi(() => {
      called = true
      return NextResponse.json({ ok: true })
    })
    const res = await handler(req)
    expect(res.status).toBe(403)
    expect(called).toBe(false)
  })

  it('withApi mutation also runs requireAuth Content-Type check', async () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest({}, 'POST')
    let called = false
    const handler = withApi(() => {
      called = true
      return NextResponse.json({ ok: true })
    }, { mutation: true })
    const res = await handler(req)
    expect(res.status).toBe(415)
    expect(called).toBe(false)
  })

  it('runGuards returns null when everything passes', () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest(
      { 'sec-fetch-site': 'same-origin', 'content-type': 'application/json' },
      'POST',
    )
    expect(runGuards(req, { mutation: true })).toBeNull()
  })

  it('withApi rejects mutation bodies over 10 MiB with 413 (SEC-030)', async () => {
    delete process.env.VISUALIZER_TOKEN
    const req = makeRequest(
      { 'content-type': 'application/json', 'content-length': String(11 * 1024 * 1024) },
      'POST',
    )
    let called = false
    const handler = withApi(() => {
      called = true
      return NextResponse.json({ ok: true })
    }, { mutation: true })
    const res = await handler(req)
    expect(res.status).toBe(413)
    expect(called).toBe(false)
  })
})
