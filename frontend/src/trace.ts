// The trace model: the SSE stream (§11.6), reduced to a list of lines.
//
// This is narration. It is deliberately separate from the audit chain, which is
// the record — the console reads state, mandates, guard decisions, orders and
// payments from §13.3, never from anything reconstructed here.

import type { StreamEvent } from './types'
import { summarizeToolArgs, summarizeToolResult } from './lib/summarize'

export type TraceItem =
  | { kind: 'user'; text: string }
  | { kind: 'thought'; text: string }
  | { kind: 'message'; text: string }
  | { kind: 'state'; state: string }
  | {
      kind: 'tool'
      id: string
      name: string
      at: string
      args: string
      rawArgs: Record<string, unknown>
      summary: string | null
      rawResult: Record<string, unknown> | null
      failed: boolean
    }
  | { kind: 'error'; code: string; detail: string }
  | {
      kind: 'done'
      state: string
      toolCalls: number
      submits: number
      reason: string | null
    }

/** Fold one stream event into the trace. Returns a new list. */
export function applyEvent(items: TraceItem[], event: StreamEvent): TraceItem[] {
  switch (event.type) {
    case 'thought':
      return [...items, { kind: 'thought', text: event.text }]

    case 'message':
      return [...items, { kind: 'message', text: event.text }]

    case 'state':
      return [...items, { kind: 'state', state: event.state }]

    case 'tool_call':
      return [
        ...items,
        {
          kind: 'tool',
          id: event.id,
          name: event.name,
          at: new Date().toISOString(),
          args: summarizeToolArgs(event.name, event.arguments ?? {}),
          rawArgs: event.arguments ?? {},
          summary: null,
          rawResult: null,
          failed: false,
        },
      ]

    case 'tool_result': {
      const result = event.result ?? {}
      const status = typeof result.status === 'string' ? result.status : ''
      return items.map((item) =>
        item.kind === 'tool' && item.id === event.id
          ? {
              ...item,
              summary: summarizeToolResult(event.name, result),
              rawResult: result,
              failed: status === 'BLOCKED' || status === 'ERROR',
            }
          : item,
      )
    }

    case 'error':
      return [...items, { kind: 'error', code: event.code, detail: event.detail }]

    case 'done':
      return [
        ...items,
        {
          kind: 'done',
          state: event.state,
          toolCalls: event.tool_call_count,
          submits: event.submit_attempt_count,
          reason: event.terminal_reason,
        },
      ]

    // `awaiting_authorization` is not narrated: it raises the authorization
    // card, and the card is rendered from the PROPOSED mandate row rather than
    // from the event body.
    default:
      return items
  }
}
