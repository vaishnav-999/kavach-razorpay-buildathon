// §14 — the console.
//
// The rule that shapes this file: **no financial state lives in React.** The
// SSE stream is narration and goes into `items`; everything the console asserts
// — session state, the mandate, the nine guard rules, the order, the payment —
// is read back from the server. `chain` is the §13.3 audit chain and `order` is
// the §7.9 merchant view. Neither is ever patched locally from a stream event
// or from a checkout callback; both are re-fetched and re-rendered.

import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'

import Header from './components/Header'
import ChatPanel from './components/ChatPanel'
import AuthorizationCard from './components/AuthorizationCard'
import GuardConsole from './components/GuardConsole'
import AuditTrace from './components/AuditTrace'
import SettlementCard from './components/SettlementCard'
import { api, KavachApiError } from './lib/api'
import { openCheckout } from './lib/razorpay'
import { streamMessage, type StreamHandle } from './lib/sse'
import { applyEvent, type TraceItem } from './trace'
import type { AuditChain, MerchantOrder, SessionCreated, UiConfig } from './types'

// There is no auth provider in this system (§2.2), so the console runs as the
// demo user. The mandate is bound to this address server-side.
const DEMO_USER = 'priya@example.com'

const SETTLED = new Set(['PAID', 'FAILED', 'CANCELLED'])

export default function App() {
  const [config, setConfig] = useState<UiConfig | null>(null)
  const [session, setSession] = useState<SessionCreated | null>(null)
  const [startError, setStartError] = useState<string | null>(null)

  const [items, setItems] = useState<TraceItem[]>([])
  const [streaming, setStreaming] = useState(false)
  const streamRef = useRef<StreamHandle | null>(null)

  const [chain, setChain] = useState<AuditChain | null>(null)
  const [chainLoading, setChainLoading] = useState(false)

  const [justification, setJustification] = useState<string | null>(null)
  const [authBusy, setAuthBusy] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)

  const [order, setOrder] = useState<MerchantOrder | null>(null)
  const [orderLoading, setOrderLoading] = useState(false)
  const [paying, setPaying] = useState(false)
  const [payError, setPayError] = useState<string | null>(null)

  // ── session lifecycle ───────────────────────────────────────────────────

  const startSession = useCallback(async () => {
    streamRef.current?.abort()
    streamRef.current = null
    setStreaming(false)
    setItems([])
    setChain(null)
    setOrder(null)
    setJustification(null)
    setAuthError(null)
    setPayError(null)
    setStartError(null)
    setSession(null)
    try {
      setSession(await api.createSession(DEMO_USER))
    } catch (err) {
      setStartError(
        err instanceof KavachApiError
          ? `${err.code} — ${err.message}`
          : 'The session could not be created.',
      )
    }
  }, [])

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null))
    void startSession()
    return () => streamRef.current?.abort()
  }, [startSession])

  // ── the record ──────────────────────────────────────────────────────────

  const correlationId = session?.correlation_id ?? null

  const refreshChain = useCallback(async () => {
    if (!correlationId) return
    setChainLoading(true)
    try {
      setChain(await api.chain(correlationId))
    } catch {
      // A chain that does not exist yet is not an error: the first event is
      // written when the first message arrives.
    } finally {
      setChainLoading(false)
    }
  }, [correlationId])

  useEffect(() => {
    if (correlationId) void refreshChain()
  }, [correlationId, refreshChain])

  // While the agent is running, the record is re-read on a timer as well as on
  // the events that imply it changed.
  useEffect(() => {
    if (!streaming) return
    const id = window.setInterval(() => void refreshChain(), 3000)
    return () => window.clearInterval(id)
  }, [streaming, refreshChain])

  // ── §7.9 order polling ──────────────────────────────────────────────────

  const orderId = chain?.orders[chain.orders.length - 1]?.id ?? null

  const refreshOrder = useCallback(async () => {
    if (!orderId) return
    setOrderLoading(true)
    try {
      setOrder(await api.order(orderId))
    } catch {
      /* the poll retries; a failed read reports nothing rather than a status */
    } finally {
      setOrderLoading(false)
    }
  }, [orderId])

  useEffect(() => {
    if (!orderId) return
    void refreshOrder()
  }, [orderId, refreshOrder])

  useEffect(() => {
    if (!orderId || (order && SETTLED.has(order.status))) return
    const id = window.setInterval(() => void refreshOrder(), 3000)
    return () => window.clearInterval(id)
  }, [orderId, order, refreshOrder])

  // ── the stream ──────────────────────────────────────────────────────────

  const send = useCallback(
    (content: string) => {
      if (!session || streaming) return
      setItems((prev) => [...prev, { kind: 'user', text: content }])
      setStreaming(true)

      streamRef.current = streamMessage(session.session_id, content, {
        onEvent: (event) => {
          setItems((prev) => applyEvent(prev, event))
          if (event.type === 'awaiting_authorization') {
            const note = (event as Record<string, unknown>).agent_justification
            setJustification(typeof note === 'string' ? note : null)
          }
          if (
            event.type === 'state' ||
            event.type === 'tool_result' ||
            event.type === 'awaiting_authorization' ||
            event.type === 'error' ||
            event.type === 'done'
          ) {
            void refreshChain()
          }
        },
        onClose: () => {
          setStreaming(false)
          streamRef.current = null
          void refreshChain()
        },
        onTransportError: (message) => {
          setItems((prev) =>
            applyEvent(prev, {
              type: 'error',
              code: 'STREAM_INTERRUPTED',
              detail: `${message} Nothing was submitted; re-read the audit chain for what actually happened.`,
            }),
          )
        },
      })
    },
    [session, streaming, refreshChain],
  )

  // ── the human grants authority ──────────────────────────────────────────

  const authorize = useCallback(
    async (mandateId: string, maxAmountPaise: number, ttlMinutes: number) => {
      if (!session) return
      setAuthBusy(true)
      setAuthError(null)
      try {
        await api.authorize(session.session_id, {
          mandate_id: mandateId,
          max_amount_paise: maxAmountPaise,
          ttl_minutes: ttlMinutes,
        })
        await refreshChain()
        // The mandate is signed; the agent still has to submit it. §11.3 has no
        // edge the loop can take on its own from AUTHORIZED, so the turn is
        // started here, by the same click.
        send(
          `I have authorized mandate ${mandateId}. Submit the purchase against ` +
            'the signed quote.',
        )
      } catch (err) {
        setAuthError(
          err instanceof KavachApiError
            ? `${err.code} — ${err.message}`
            : 'Authorization failed.',
        )
      } finally {
        setAuthBusy(false)
      }
    },
    [session, refreshChain, send],
  )

  // ── §12.3 / §12.4 settlement ────────────────────────────────────────────

  const pay = useCallback(async () => {
    if (!config || !order || !order.razorpay_order_id) return
    setPayError(null)
    try {
      await openCheckout({
        keyId: config.razorpay_key_id,
        amountPaise: order.amount_paise,
        razorpayOrderId: order.razorpay_order_id,
        orderId: order.id,
        onResponse: (response) => {
          setPaying(true)
          void api
            .verifyPayment({
              order_id: order.id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
            })
            .catch((err) => {
              setPayError(
                err instanceof KavachApiError
                  ? `${err.code} — ${err.message}`
                  : 'Verification failed.',
              )
            })
            // Whatever verification returned, the status shown comes from the
            // merchant's own view of the order, never from this response.
            .finally(() => {
              setPaying(false)
              void refreshOrder()
              void refreshChain()
            })
        },
        onDismiss: () => void refreshOrder(),
      })
    } catch (err) {
      setPayError(err instanceof Error ? err.message : 'Checkout could not open.')
    }
  }, [config, order, refreshOrder, refreshChain])

  // ── derived, all of it from the server ──────────────────────────────────

  const sessionState = chain?.session?.state ?? session?.state ?? 'INIT'
  const terminalReason = chain?.session?.terminal_reason ?? null
  const decision = chain?.guard_decisions[chain.guard_decisions.length - 1] ?? null
  const proposed = chain?.mandates.find((m) => m.status === 'PROPOSED') ?? null

  return (
    <div className="flex h-full flex-col">
      <Header
        session={session}
        sessionState={sessionState}
        demoMode={config?.demo_mode ?? false}
        onNewSession={() => void startSession()}
        onDemoAction={() => {
          void refreshChain()
          void refreshOrder()
        }}
      />

      {startError ? (
        <div className="m-2 flex items-start gap-3 rounded-card border border-fail/40 bg-fail/10 p-4">
          <AlertTriangle size={16} className="mt-1 shrink-0 text-fail" />
          <div>
            <p className="mono text-xs font-medium uppercase tracking-wider text-fail">
              session not started
            </p>
            <p className="mt-1 text-sm text-zinc-300">{startError}</p>
            <button
              type="button"
              className="btn mt-3"
              onClick={() => void startSession()}
            >
              <RotateCcw size={14} />
              Try again
            </button>
          </div>
        </div>
      ) : null}

      <main className="console-grid min-h-0 flex-1 gap-2 p-2">
        <ChatPanel
          items={items}
          streaming={streaming}
          sessionState={sessionState}
          terminalReason={terminalReason}
          onSend={send}
          onNewSession={() => void startSession()}
        />

        <div className="flex min-h-0 flex-col gap-2">
          {proposed ? (
            <AuthorizationCard
              key={proposed.id}
              mandate={proposed}
              justification={justification}
              busy={authBusy}
              error={authError}
              onAuthorize={(amount, ttl) => void authorize(proposed.id, amount, ttl)}
            />
          ) : null}

          {orderId ? (
            <SettlementCard
              order={order}
              loading={orderLoading}
              paying={paying}
              error={payError}
              onPay={() => void pay()}
              onRefresh={() => void refreshOrder()}
            />
          ) : null}

          <GuardConsole decision={decision} loading={chainLoading && !chain} />

          <AuditTrace
            chain={chain}
            correlationId={correlationId}
            loading={chainLoading && !chain}
          />
        </div>
      </main>
    </div>
  )
}
