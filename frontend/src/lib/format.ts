// Display formatting. Pure functions over values the server sent.
//
// **There is no arithmetic on money here.** `rupees()` splits an integer paise
// value into two integer parts and prints them; it never adds, subtracts,
// compares or totals anything. Every figure the console shows was computed by
// the merchant, the Mandate Authority or the Guard, and arrived over the wire.

/** `516000` → `₹5,160.00`. The only place paise becomes rupees. */
export function rupees(paise: number): string {
  const sign = paise < 0 ? '-' : ''
  const abs = Math.abs(Math.trunc(paise))
  const whole = Math.trunc(abs / 100)
  const fraction = abs % 100
  return `${sign}₹${whole.toLocaleString('en-IN')}.${String(fraction).padStart(2, '0')}`
}

/**
 * The one conversion that runs in the other direction: §18.3 permits rupees in
 * "the mandate authorization input, which is converted at the edge".
 *
 * Parsed as text — digits before the point, digits after, padded to two — so no
 * float ever touches a money value. Returns null for anything unparseable, and
 * the server clamps whatever it does return (§8.3).
 */
export function parseRupeesToPaise(input: string): number | null {
  const text = input.trim().replace(/[₹,\s]/g, '')
  if (!/^\d+(\.\d{0,2})?$/.test(text)) return null
  const [whole, fraction = ''] = text.split('.')
  const paise = `${whole}${fraction.padEnd(2, '0')}`.replace(/^0+(?=\d)/, '')
  const value = Number(paise)
  return Number.isSafeInteger(value) ? value : null
}

/** The inverse display for the authorization input, in plain rupees. */
export function paiseToRupeeInput(paise: number): string {
  const abs = Math.abs(Math.trunc(paise))
  return `${Math.trunc(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}

/** `2026-09-03T10:31:44Z` → `10:31:44` — the console runs in one sitting. */
export function clockTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('en-GB', { hour12: false })
}

export function dateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-GB', { hour12: false })
}

/** Minutes until `iso`, as a label. Never used in a decision, only shown. */
export function relativeMinutes(iso: string | null): string {
  if (!iso) return '—'
  const ms = new Date(iso).getTime() - Date.now()
  if (Number.isNaN(ms)) return iso
  if (ms <= 0) return 'expired'
  return `in ${Math.round(ms / 60000)} min`
}

/** Signatures are long. Show enough to compare two by eye, never the whole. */
export function shortHex(value: string | null, head = 16): string {
  if (!value) return '—'
  return value.length <= head ? value : `${value.slice(0, head)}…`
}

/** A guard rule's observed/threshold cell. Arrays and booleans included. */
export function ruleValue(value: unknown, unit: string): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '(none)'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number' && unit === 'paise') return rupees(value)
  return String(value)
}

export function titleize(value: string): string {
  return value.replace(/_/g, ' ').toLowerCase()
}
