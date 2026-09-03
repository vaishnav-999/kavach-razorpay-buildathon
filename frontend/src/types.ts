// Wire types. These mirror the server's Pydantic models; nothing here is
// computed, defaulted or widened on the client.
//
// Every money field is an integer named `*_paise` (§18.3, invariant 6). There
// is no rupee-denominated field anywhere in this file, and there is no number
// in the app that was not read from one of these responses.

export interface UiConfig {
  razorpay_key_id: string
  demo_mode: boolean
}

export interface SessionCreated {
  session_id: string
  correlation_id: string
  state: string
  llm_provider: string
  llm_model: string
  cassette_name: string | null
}

export interface LineItem {
  sku: string
  name: string | null
  category: string | null
  qty: number
  unit_price_paise: number
  line_total_paise: number
}

export interface GuardRule {
  rule_id: string
  name: string
  passed: boolean
  observed: unknown
  threshold: unknown
  unit: string
  detail: string
  block_code: string | null
}

/** §9.3, exactly. All nine rules, on ALLOW as well as BLOCK. */
export interface GuardDecision {
  verdict: 'ALLOW' | 'BLOCK'
  decision_id: string
  correlation_id: string
  session_id: string | null
  mandate_id: string | null
  quote_id: string | null
  merchant_id: string | null
  requested_total_paise: number
  currency: string
  evaluated_at: string
  duration_ms: number
  failed_rule_id: string | null
  block_code: string | null
  rules: GuardRule[]
}

export type MandateStatus =
  | 'PROPOSED'
  | 'ACTIVE'
  | 'REVOKED'
  | 'EXPIRED'
  | 'EXHAUSTED'

export interface Mandate {
  id: string
  session_id: string | null
  correlation_id: string | null
  user_email: string
  status: MandateStatus
  currency: string
  max_amount_paise: number
  cumulative_cap_paise: number
  max_transactions: number
  allowed_merchant_ids: string[]
  allowed_categories: string[]
  issued_at: string | null
  expires_at: string | null
  revoked_at: string | null
  prompt_playback: string
  signature: string | null
}

export interface Quote {
  id: string
  cart_id: string
  merchant_id: string
  currency: string
  line_items: LineItem[]
  total_paise: number
  issued_at: string
  expires_at: string
  status: string
  signature: string
}

export interface Payment {
  id: string
  order_id: string
  razorpay_payment_id: string | null
  razorpay_order_id: string | null
  amount_paise: number
  currency: string
  method: string | null
  status: 'CREATED' | 'AUTHORIZED' | 'CAPTURED' | 'FAILED'
  signature_verified: boolean
  source: 'CHECKOUT' | 'WEBHOOK'
}

export interface Order {
  id: string
  merchant_id: string
  quote_id: string
  mandate_id: string
  guard_decision_id: string
  correlation_id: string
  amount_paise: number
  currency: string
  status: 'CREATED' | 'PENDING_PAYMENT' | 'PAID' | 'FAILED' | 'CANCELLED'
  razorpay_order_id: string | null
  receipt: string | null
  line_items: LineItem[]
}

/** §7.9 — the only source of truth for payment status in the UI. */
export interface MerchantOrder extends Order {
  payment: Payment | null
}

export interface WebhookEvent {
  id: string
  razorpay_event_id: string
  event_type: string
  order_id: string | null
  signature_valid: boolean
  was_duplicate: boolean
  processed_at: string | null
  created_at: string
}

export interface AuditEvent {
  seq: number
  id: string
  correlation_id: string
  session_id: string | null
  event_type: string
  actor: 'user' | 'agent' | 'platform' | 'merchant' | 'razorpay' | 'demo'
  payload: Record<string, unknown>
  created_at: string
}

export interface AuditSession {
  id: string
  state: string
  llm_provider: string | null
  llm_model: string | null
  cassette_name: string | null
  user_email: string
  intent: string | null
  tool_call_count: number
  submit_attempt_count: number
  terminal_reason: string | null
}

/** §13.3 — the whole ordered chain, and the app's source of truth. */
export interface AuditChain {
  correlation_id: string
  session: AuditSession | null
  events: AuditEvent[]
  guard_decisions: GuardDecision[]
  mandates: Mandate[]
  quotes: Quote[]
  orders: Order[]
  payments: Payment[]
  webhook_events: WebhookEvent[]
}

// ── SSE (§11.6) ───────────────────────────────────────────────────────────

export type StreamEvent =
  | { type: 'thought'; text: string }
  | { type: 'message'; text: string }
  | { type: 'state'; state: string }
  | { type: 'tool_call'; id: string; name: string; arguments: Record<string, unknown> }
  | { type: 'tool_result'; id: string; name: string; result: Record<string, unknown> }
  | { type: 'awaiting_authorization'; [k: string]: unknown }
  | { type: 'error'; code: string; detail: string }
  | {
      type: 'done'
      state: string
      tool_call_count: number
      submit_attempt_count: number
      terminal_reason: string | null
    }

export interface ApiError {
  code: string
  message: string
  correlation_id?: string | null
  detail?: unknown
}

// ── §15 demo control panel ────────────────────────────────────────────────

/** One field a demo action moved. Rendered as a before → after row. */
export interface DemoChange {
  target: string
  field: string
  before: unknown
  after: unknown
  unit: string | null
}

export interface DemoActionResult {
  action: string
  summary: string
  triggers: string | null
  changed: DemoChange[]
  correlation_id: string
  detail: Record<string, unknown>
}

/** §16.5 — what the merchant published and where its instruction had to land. */
export interface InjectionEvidence {
  product: {
    sku: string
    name: string
    merchant_id: string
    unit_price_paise: number
    /** Verbatim. The same bytes /merchant/catalog serves. Never truncated here. */
    description: string
  }
  served_verbatim: {
    length: number
    sha256: string
    sanitised: boolean
    /** The platform's own reading of the stored text. Changes nothing. */
    instruction_markers: string[]
    flagged_reason: string | null
    detail: string
    audit_event_type: string
  }
  tool: {
    name: string
    description: string
    parameters: { properties?: Record<string, { description?: string; type?: string }>; required?: string[] }
  }
  parameters: {
    declared_by_tool: Record<string, string[]>
    declared_total: number
    absent: { name: string; present: boolean }[]
    detail: string
  }
  arithmetic: {
    correct_total_paise: number
    poisoned_total_paise: number
    mandate_cap_paise: number
    overshoot_paise: number
    injected_line: {
      sku: string
      qty: number
      unit_price_paise: number
      line_total_paise: number
    }
    currency: string
  }
  attacks: { asks_for: string; answer: string }[]
}

/** The forced poisoned-cart submission, through the real Guard call site. */
export interface PoisonedCartResult {
  action: string
  summary: string
  correlation_id: string
  cart: { cart_id: string; merchant_id: string; items: { sku: string; qty: number }[] }
  quote: {
    quote_id: string
    total_paise: number
    currency: string
    line_items: LineItem[]
    signature: string
  }
  mandate: {
    mandate_id: string
    status: string
    source: string
    max_amount_paise: number
    cumulative_cap_paise: number
    prompt_playback: string
  }
  guard: GuardDecision
  razorpay: {
    client_calls_before: number
    client_calls_after: number
    orders_for_quote: number
    order_created: boolean
    detail: string
  }
  injection: {
    sku: string
    qty: number
    line_total_paise: number
    source: string
    failed_rule: GuardRule | null
  }
}
