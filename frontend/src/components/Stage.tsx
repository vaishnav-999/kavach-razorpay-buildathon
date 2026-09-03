// §14 — the right column, and the whole of the information hierarchy.
//
// THE RULE: one thing is dominant at any moment. The stage renders exactly one
// panel at full height; everything else that has data becomes a single hairline
// strip that states its headline and can be clicked to take the stage. Five
// panels competing for attention is what this replaces.
//
// The dominant panel is chosen from the record, not from a click:
//
//   a PROPOSED mandate exists   → Authorization. A human has to act; nothing
//                                 outranks that.
//   a guard decision exists     → Transaction Guard. The moment a verdict lands
//                                 it takes the screen and the rest recedes.
//   an order exists             → Settlement.
//   otherwise                   → the standing-by panel, which says what the
//                                 architecture will and will not let happen.
//
// A click overrides the choice until the record moves on, so a presenter can go
// back to a verdict; the override is dropped the moment the automatic answer
// changes, so nothing ever hides a new state behind a stale selection.
//
// This component holds no financial state. Every prop it renders was read back
// from the server by App and is passed through untouched.

import { useEffect, useRef, useState } from 'react'
import {
  CreditCard,
  KeyRound,
  Landmark,
  Shield,
  ShieldCheck,
  ShieldX,
} from 'lucide-react'

import AuthorizationCard from './AuthorizationCard'
import GuardConsole from './GuardConsole'
import SettlementCard from './SettlementCard'
import { Panel } from './ui'
import { rupees } from '../lib/format'
import type { GuardDecision, Mandate, MerchantOrder } from '../types'

type Focus = 'guard' | 'authorization' | 'settlement' | 'idle'

const SETTLED = new Set(['PAID', 'FAILED', 'CANCELLED'])

export default function Stage({
  decision,
  chainLoading,
  proposed,
  justification,
  authBusy,
  authError,
  onAuthorize,
  orderId,
  order,
  orderLoading,
  paying,
  payError,
  onPay,
  onRefreshOrder,
}: {
  decision: GuardDecision | null
  chainLoading: boolean
  proposed: Mandate | null
  justification: string | null
  authBusy: boolean
  authError: string | null
  onAuthorize: (maxAmountPaise: number, ttlMinutes: number) => void
  orderId: string | null
  order: MerchantOrder | null
  orderLoading: boolean
  paying: boolean
  payError: string | null
  onPay: () => void
  onRefreshOrder: () => void
}) {
  const available: Focus[] = []
  if (decision) available.push('guard')
  if (proposed) available.push('authorization')
  if (orderId) available.push('settlement')

  const auto: Focus = proposed
    ? 'authorization'
    : decision
      ? 'guard'
      : orderId
        ? 'settlement'
        : 'idle'

  // The override is presentation state and nothing else: it selects which panel
  // is large. It is discarded whenever the record moves the automatic answer.
  const [override, setOverride] = useState<Focus | null>(null)
  const lastAuto = useRef(auto)
  useEffect(() => {
    if (lastAuto.current !== auto) {
      lastAuto.current = auto
      setOverride(null)
    }
  }, [auto])

  const focus = override && available.includes(override) ? override : auto
  const receded = available.filter((f) => f !== focus)

  return (
    <div className="flex min-h-0 min-w-0 flex-[3] flex-col">
      {available.length > 1 ? (
        <div className="flex h-9 shrink-0 items-center gap-1 border-b border-zinc-800 bg-zinc-900 px-3">
          {available.map((f) => (
            <StageTab
              key={f}
              focus={f}
              active={f === focus}
              decision={decision}
              order={order}
              onClick={() => setOverride(f)}
            />
          ))}
          <span className="mono ml-auto hidden shrink-0 text-xs text-zinc-600 xl:inline">
            {focus === 'guard'
              ? '§9 — nine rules, evaluated at the one call site'
              : focus === 'authorization'
                ? '§8 — the human grants authority'
                : '§12 — settlement'}
          </span>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-[3] flex-col">
        {focus === 'guard' || focus === 'idle' ? (
          focus === 'guard' ? (
            <GuardConsole decision={decision} loading={chainLoading && !decision} />
          ) : (
            <StandingBy loading={chainLoading} />
          )
        ) : null}

        {focus === 'authorization' && proposed ? (
          <AuthorizationCard
            key={proposed.id}
            mandate={proposed}
            justification={justification}
            busy={authBusy}
            error={authError}
            onAuthorize={onAuthorize}
          />
        ) : null}

        {focus === 'settlement' && orderId ? (
          <SettlementCard
            order={order}
            loading={orderLoading}
            paying={paying}
            error={payError}
            onPay={onPay}
            onRefresh={onRefreshOrder}
          />
        ) : null}
      </div>

      {/* Everything that has data but is not dominant, as one row each. */}
      {receded.map((f) => (
        <RecededStrip
          key={f}
          focus={f}
          decision={decision}
          proposed={proposed}
          order={order}
          paying={paying}
          onOpen={() => setOverride(f)}
          onPay={onPay}
        />
      ))}
    </div>
  )
}

const TAB_LABEL: Record<Exclude<Focus, 'idle'>, string> = {
  guard: 'Guard',
  authorization: 'Authorization',
  settlement: 'Settlement',
}

function StageTab({
  focus,
  active,
  decision,
  order,
  onClick,
}: {
  focus: Focus
  active: boolean
  decision: GuardDecision | null
  order: MerchantOrder | null
  onClick: () => void
}) {
  if (focus === 'idle') return null

  // Status dots are green and red only where those colours mean pass and fail.
  const dot =
    focus === 'guard' && decision
      ? decision.verdict === 'BLOCK'
        ? 'bg-fail'
        : 'bg-pass'
      : focus === 'settlement' && order
        ? order.status === 'PAID'
          ? 'bg-pass'
          : order.status === 'FAILED'
            ? 'bg-fail'
            : 'bg-zinc-600'
        : 'bg-zinc-600'

  return (
    <button
      type="button"
      onClick={onClick}
      className={`mono flex h-6 shrink-0 items-center gap-2 rounded-input px-2 text-xs uppercase tracking-wider transition-colors duration-150 ease-out ${
        active
          ? 'bg-zinc-800 text-zinc-100'
          : 'text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300'
      }`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {TAB_LABEL[focus]}
    </button>
  )
}

/** The receded form: one row, one headline, one click back to the stage. */
function RecededStrip({
  focus,
  decision,
  proposed,
  order,
  paying,
  onOpen,
  onPay,
}: {
  focus: Focus
  decision: GuardDecision | null
  proposed: Mandate | null
  order: MerchantOrder | null
  paying: boolean
  onOpen: () => void
  onPay: () => void
}) {
  if (focus === 'guard' && decision) {
    const blocked = decision.verdict === 'BLOCK'
    return (
      <Strip
        icon={
          blocked ? (
            <ShieldX size={13} className="text-fail" />
          ) : (
            <ShieldCheck size={13} className="text-pass" />
          )
        }
        title="Transaction Guard"
        onOpen={onOpen}
      >
        <span
          className={`mono shrink-0 text-sm font-medium ${
            blocked ? 'text-fail' : 'text-pass'
          }`}
        >
          {blocked ? 'BLOCKED' : 'ALLOWED'}
        </span>
        {blocked ? (
          <span className="mono min-w-0 truncate text-xs text-zinc-400">
            {decision.failed_rule_id} · {decision.block_code}
          </span>
        ) : (
          <span className="mono min-w-0 truncate text-xs text-zinc-400">
            9 / 9 rules passed
          </span>
        )}
      </Strip>
    )
  }

  if (focus === 'authorization' && proposed) {
    return (
      <Strip
        icon={<KeyRound size={13} className="text-zinc-500" />}
        title="Authorization"
        onOpen={onOpen}
      >
        <span className="mono shrink-0 text-xs text-zinc-300">{proposed.status}</span>
        <span className="min-w-0 truncate text-xs text-zinc-500">
          unsigned — a proposal grants nothing
        </span>
      </Strip>
    )
  }

  if (focus === 'settlement') {
    const paid = order?.status === 'PAID'
    const settled = order ? SETTLED.has(order.status) : false
    return (
      <Strip
        icon={<Landmark size={13} className="text-zinc-500" />}
        title="Settlement"
        onOpen={onOpen}
        action={
          order && !settled && order.razorpay_order_id ? (
            <button
              type="button"
              className="btn btn-primary h-7 px-2.5 text-xs"
              disabled={paying}
              onClick={(e) => {
                e.stopPropagation()
                onPay()
              }}
            >
              <CreditCard size={12} />
              {paying ? 'Verifying…' : 'Pay with Razorpay'}
            </button>
          ) : null
        }
      >
        <span className="mono shrink-0 text-sm text-zinc-100">
          {order ? rupees(order.amount_paise) : '—'}
        </span>
        <span
          className={`mono shrink-0 text-xs uppercase tracking-wider ${
            paid ? 'text-pass' : order?.status === 'FAILED' ? 'text-fail' : 'text-zinc-400'
          }`}
        >
          {order ? order.status : 'reading…'}
        </span>
      </Strip>
    )
  }

  return null
}

function Strip({
  icon,
  title,
  onOpen,
  action,
  children,
}: {
  icon: React.ReactNode
  title: string
  onOpen: () => void
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="flex h-10 shrink-0 items-center border-t border-zinc-800 bg-zinc-950">
      <button
        type="button"
        onClick={onOpen}
        className="flex h-full min-w-0 flex-1 items-center gap-3 px-4 text-left transition-colors duration-150 ease-out hover:bg-zinc-900"
      >
        <span className="shrink-0">{icon}</span>
        <span className="panel-title">{title}</span>
        {children}
      </button>
      {action ? <div className="shrink-0 pr-3">{action}</div> : null}
    </div>
  )
}

/**
 * The standing-by panel. The copy is the point: it teaches the architecture to
 * someone who has never seen the system, in a box the size of its own words
 * rather than an empty half-screen.
 */
function StandingBy({ loading }: { loading: boolean }) {
  return (
    <Panel
      title="Transaction Guard"
      icon={<Shield size={14} />}
      right={<span className="mono text-xs text-zinc-600">9 rules · idle</span>}
    >
      <div className="px-4 py-4">
        <p className="max-w-[62ch] text-base leading-6 text-zinc-300">
          Nothing has been submitted yet.
        </p>
        <p className="mt-3 max-w-[62ch] text-sm leading-6 text-zinc-500">
          The agent can search merchants, build a cart and ask you for authority.
          It cannot create a payment order. The only path to one runs through
          this Guard, which evaluates nine rules against a quote the merchant
          signed and a mandate you signed — and no Razorpay order exists unless
          the verdict is ALLOW.
        </p>
        <p className="mt-3 max-w-[62ch] text-sm leading-6 text-zinc-500">
          Assume the model is fully compromised. That assumption is what the
          nine rows that appear here are for.
        </p>

        {loading ? (
          <div className="mono mt-4 text-xs text-zinc-600">reading the chain…</div>
        ) : null}
      </div>
    </Panel>
  )
}
