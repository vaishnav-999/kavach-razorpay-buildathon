// Consuming the §11.6 stream.
//
// `POST /api/buyer/sessions/{id}/message` answers with an SSE body, so
// `EventSource` — which can only issue GETs — is not usable. This reads the
// response body directly and parses the wire format: `event:` lines, `data:`
// lines, `:` comment heartbeats, blank line terminates a frame.
//
// The parser trusts nothing about framing: a chunk boundary can fall anywhere,
// including inside a JSON payload, so frames are assembled from a buffer.

import type { StreamEvent } from '../types'

export interface StreamHandle {
  abort: () => void
}

export function streamMessage(
  sessionId: string,
  content: string,
  handlers: {
    onEvent: (event: StreamEvent) => void
    onClose: () => void
    onTransportError: (message: string) => void
  },
): StreamHandle {
  const controller = new AbortController()

  void (async () => {
    try {
      const res = await fetch(`/api/buyer/sessions/${sessionId}/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        const body = await res.json().catch(() => null)
        const error = (body as { error?: { code: string; message: string } } | null)
          ?.error
        handlers.onEvent({
          type: 'error',
          code: error?.code ?? `HTTP_${res.status}`,
          detail: error?.message ?? 'The stream could not be opened.',
        })
        handlers.onClose()
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        for (;;) {
          const boundary = findBoundary(buffer)
          if (!boundary) break
          const frame = buffer.slice(0, boundary.index)
          buffer = buffer.slice(boundary.index + boundary.length)
          const event = parseFrame(frame)
          if (event) handlers.onEvent(event)
        }
      }
      handlers.onClose()
    } catch (err) {
      if (controller.signal.aborted) return
      handlers.onTransportError(
        err instanceof Error ? err.message : 'The stream ended unexpectedly.',
      )
      handlers.onClose()
    }
  })()

  return { abort: () => controller.abort() }
}

/**
 * Where one frame ends.
 *
 * sse-starlette writes CRLF line endings, so a frame terminates on a blank
 * CRLF line and not on the bare "\n\n" a naive reader looks for. Both are legal
 * and a proxy may rewrite one into the other, so both are matched and whichever
 * appears first wins.
 */
function findBoundary(buffer: string): { index: number; length: number } | null {
  const crlf = buffer.indexOf('\r\n\r\n')
  const lf = buffer.indexOf('\n\n')
  if (crlf === -1 && lf === -1) return null
  if (crlf !== -1 && (lf === -1 || crlf < lf)) return { index: crlf, length: 4 }
  return { index: lf, length: 2 }
}

function parseFrame(frame: string): StreamEvent | null {
  const data: string[] = []
  for (const rawLine of frame.split(/\r\n|\n|\r/)) {
    // `:` is a heartbeat comment. `event:` is redundant here — every payload
    // carries its own `type`, and that is the field the console switches on.
    if (!rawLine || rawLine.startsWith(':') || !rawLine.startsWith('data:')) continue
    data.push(rawLine.slice(5).replace(/^ /, ''))
  }
  if (!data.length) return null
  try {
    return JSON.parse(data.join('\n')) as StreamEvent
  } catch {
    return null
  }
}
