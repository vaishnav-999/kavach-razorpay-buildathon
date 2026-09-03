// §7.9, §12.3, §12.4 — settling an order the Guard already allowed.
//
// Two rules hold this component together:
//
//  1. **Payment status is never inferred from the checkout callback.** The
//     handler's response is posted to `/api/payments/verify` and then this card
//     re-reads `GET /api/ui/orders/{id}` — the §7.9 view — and renders whatever
//     the merchant says. The browser's word is never written anywhere
//     (invariant 4).
//  2. No amount here is computed. `amount_paise` came from the order row, which
//     came from the quote the merchant signed.
//
// When this panel is not the dominant one on the stage it collapses to a single
// row that keeps the amount, the status and the Pay button reachable.

import { CreditCard, Landmark, RefreshCw } from 'lucide-react'
import type { MerchantOrder } from '../types'
import { rupees } from '../lib/format'
import { Badge, Id, Panel, Row, Skeleton } from './ui'

const SETTLED = new Set(['PAID', 'FAILED', 'CANCELLED'])

export default function SettlementCard({
  order,
  loading,
  paying,
  error,
  onPay,
  onRefresh,
}: {
  order: MerchantOrder | null
  loading: boolean
  paying: boolean
  error: string | null
  onPay: () => void
  onRefresh: () => void
}) {
  const paid = order?.status === 'PAID'
  const settled = order ? SETTLED.has(order.status) : false

  return (
    <Panel
      title="Settlement"
      icon={<Landmark size={14} />}
      className="animate-panel-in"
      accent={paid ? 'pass' : 'none'}
      right={
        <>
          <button
            type="button"
            className="btn-ghost"
            onClick={onRefresh}
            title="Re-read the merchant's order"
          >
            <RefreshCw size={12} />
            poll
          </button>
          {order ? (
            <Badge
              tone={paid ? 'pass' : order.status === 'FAILED' ? 'fail' : 'neutral'}
            >
              {order.status}
            </Badge>
          ) : null}
        </>
      }
    >
      {!order && loading ? <Skeleton rows={4} /> : null}

      {order ? (
        <>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-zinc-800 px-4 py-3">
            <span className="mono text-xl font-semibold tracking-tight text-zinc-100">
              {rupees(order.amount_paise)}
            </span>
            <span className="mono text-sm text-zinc-400">{order.currency}</span>
            <span
              className={`mono text-sm uppercase tracking-wider ${
                paid ? 'text-pass' : order.status === 'FAILED' ? 'text-fail' : 'text-zinc-400'
              }`}
            >
              {order.status}
            </span>
          </div>

          <dl className="px-4 py-3">
            <Row label="order">
              <Id value={order.id} />
            </Row>
            <Row label="razorpay order">
              <Id value={order.razorpay_order_id ?? '—'} />
            </Row>
            <Row label="guard decision">
              <Id value={order.guard_decision_id} />
            </Row>
            {order.payment ? (
              <>
                <Row label="payment">
                  <Id value={order.payment.razorpay_payment_id ?? order.payment.id} />
                </Row>
                <Row label="payment status">
                  <span className={paid ? 'text-pass' : 'text-zinc-200'}>
                    {order.payment.status}
                  </span>
                </Row>
                <Row label="signature verified">
                  <span
                    className={
                      order.payment.signature_verified ? 'text-pass' : 'text-fail'
                    }
                  >
                    {order.payment.signature_verified ? 'true' : 'false'}
                  </span>
                </Row>
                <Row label="source">{order.payment.source}</Row>
              </>
            ) : null}
          </dl>

          {error ? (
            <p className="mono border-t border-zinc-800 bg-fail/10 px-4 py-2 text-xs text-fail">
              {error}
            </p>
          ) : null}

          <div className="flex items-center gap-4 border-t border-zinc-800 bg-zinc-900/40 px-4 py-3">
            {!settled ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={paying || !order.razorpay_order_id}
                onClick={onPay}
              >
                <CreditCard size={14} />
                {paying ? 'Verifying…' : 'Pay with Razorpay'}
              </button>
            ) : null}
            <p className="max-w-[70ch] text-xs leading-5 text-zinc-500">
              {paid
                ? 'Status read from the merchant after the server re-derived the HMAC over the order id in our own database.'
                : 'Status is polled from the merchant. Nothing the checkout handler returns is written until the server has verified it.'}
            </p>
          </div>
        </>
      ) : null}
    </Panel>
  )
}
