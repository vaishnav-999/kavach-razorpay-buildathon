// One line per tool call, in the §14 shape:
//
//     > discover_merchants        3 found, 1 transactable
//
// Every summary is read out of the tool result the server produced. Nothing
// here recomputes a figure, and every amount goes through `rupees()`.

import { rupees } from './format'

type Json = Record<string, unknown>

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number | null => (typeof v === 'number' ? v : null)
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

export function summarizeToolResult(name: string, result: Json): string {
  // A tool that failed says so first, whatever it was.
  if (str(result.status) === 'ERROR') {
    return `${str(result.code) || 'ERROR'} — ${str(result.detail)}`
  }

  switch (name) {
    case 'discover_merchants': {
      const merchants = arr(result.merchants) as Json[]
      const transactable = merchants.filter((m) => m.transactable === true).length
      return `${merchants.length} found, ${transactable} transactable`
    }

    case 'get_merchant_profile': {
      const missing = arr(result.missing_capabilities) as string[]
      const slug = str(result.slug)
      return missing.length
        ? `${slug} — missing ${missing.join(', ')}`
        : `${slug} — transactable end to end`
    }

    case 'get_catalog': {
      const products = arr(result.products) as Json[]
      return `${products.length} products at ${str(result.merchant_id)}`
    }

    case 'check_availability': {
      const items = arr(result.items) as Json[]
      const ok = items.filter((i) => i.available === true).length
      return `${ok} of ${items.length} lines available`
    }

    case 'create_cart':
      return `${str(result.cart_id)} ${str(result.status)}`

    case 'add_to_cart': {
      const items = arr(result.items) as Json[]
      const last = items[items.length - 1]
      const line = last ? `${str(last.sku)} ×${num(last.qty) ?? ''}` : ''
      return `${line}  (${items.length} lines)`
    }

    case 'request_quote': {
      const total = num(result.total_paise)
      const lines = arr(result.line_items).length
      return total === null
        ? 'quote returned'
        : `${lines} lines, signed total ${rupees(total)}`
    }

    case 'propose_mandate': {
      const cap = num(result.max_amount_paise)
      return `${str(result.status)} — proposes a ceiling of ${
        cap === null ? '—' : rupees(cap)
      }, grants nothing`
    }

    case 'submit_purchase': {
      if (str(result.status) === 'BLOCKED') {
        return `BLOCKED ${str(result.failed_rule_id)} ${str(result.block_code)}`
      }
      const amount = num(result.amount_paise)
      return `${str(result.guard_verdict)} — order ${str(result.order_id)} for ${
        amount === null ? '—' : rupees(amount)
      }`
    }

    case 'report_finding':
      return `${str(result.status)} — written to the audit trail`

    default:
      return str(result.status) || 'ok'
  }
}

/** The arguments worth showing beside a call, before its result arrives. */
export function summarizeToolArgs(name: string, args: Json): string {
  switch (name) {
    case 'add_to_cart':
      return `${str(args.sku)} ×${num(args.qty) ?? ''}`
    case 'get_catalog':
    case 'create_cart':
      return str(args.merchant_id)
    case 'get_merchant_profile':
      return str(args.slug)
    case 'request_quote':
      return str(args.cart_id)
    case 'propose_mandate':
      return str(args.quote_id)
    case 'submit_purchase':
      return `${str(args.quote_id)} ${str(args.mandate_id)}`
    case 'report_finding':
      return `${str(args.sku)} ${str(args.severity)}`
    default:
      return ''
  }
}
