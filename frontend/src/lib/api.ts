// The only place the console talks to the server.
//
// Same origin: the bundle is served out of app/static by the FastAPI process
// that owns these endpoints, so there is no base URL and no CORS.

import type {
  AuditChain,
  ApiError,
  DemoActionResult,
  InjectionEvidence,
  MerchantOrder,
  PoisonedCartResult,
  SessionCreated,
  UiConfig,
} from '../types'

export class KavachApiError extends Error {
  code: string
  status: number
  detail: unknown

  constructor(status: number, error: ApiError) {
    super(error.message || error.code)
    this.name = 'KavachApiError'
    this.code = error.code
    this.status = status
    this.detail = error.detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    // §18.1 — every error arrives in the same envelope.
    const error = (body as { error?: ApiError } | null)?.error
    throw new KavachApiError(
      res.status,
      error ?? { code: `HTTP_${res.status}`, message: res.statusText },
    )
  }
  return body as T
}

export const api = {
  config: () => request<UiConfig>('/api/ui/config'),

  createSession: (userEmail: string) =>
    request<SessionCreated>('/api/buyer/sessions', {
      method: 'POST',
      body: JSON.stringify({ user_email: userEmail }),
    }),

  /** §11.6 — the human grants authority. The only path that signs a mandate. */
  authorize: (
    sessionId: string,
    body: { mandate_id: string; max_amount_paise: number; ttl_minutes: number },
  ) =>
    request<{ session_id: string; state: string }>(
      `/api/buyer/sessions/${sessionId}/authorize`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** §13.3 — the whole ordered chain. The console's source of truth. */
  chain: (correlationId: string) =>
    request<AuditChain>(`/api/audit/${correlationId}`),

  /** §7.9 — the only source of truth for payment status. */
  order: (orderId: string) => request<MerchantOrder>(`/api/ui/orders/${orderId}`),

  /** §12.4 — turns the browser's word into nothing until the server agrees. */
  verifyPayment: (body: {
    order_id: string
    razorpay_payment_id: string
    razorpay_order_id: string
    razorpay_signature: string
  }) =>
    request<{ order_id: string; order_status: string; signature_verified: boolean }>(
      '/api/payments/verify',
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** §15 — the demo control panel.
   *
   *  `correlation_id` is passed so the DEMO_ACTION_TRIGGERED event lands on the
   *  chain this console is already watching, and the audit trace shows the
   *  lever beside the rule it made fire. Every response says what it changed;
   *  nothing is assumed to have happened because a button was pressed. */
  demo: (action: string, body: DemoBody = {}) =>
    request<DemoActionResult>(`/api/demo/${action}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** §16.5 — the description as stored, and the tool schema it had to land in. */
  injection: () => request<InjectionEvidence>('/api/demo/injection'),

  /** §16.5 — the poisoned cart, through the same submit path as a real one. */
  forcePoisonedCart: (body: DemoBody = {}) =>
    request<PoisonedCartResult>('/api/demo/force-poisoned-cart', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

export interface DemoBody {
  correlation_id?: string | null
  session_id?: string | null
  mandate_id?: string | null
}
