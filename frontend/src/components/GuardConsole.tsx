// §9, §14 — the screen the demo rests on.
//
// All nine rules, every time, in id order, on ALLOW as well as BLOCK
// (invariant 10). A passing rule carries its observed value, its threshold, its
// unit and its detail exactly as a failing one does, and showing them is what
// makes the Guard auditable rather than merely restrictive: eight green rows
// with real numbers in them are the evidence that the ninth red row means
// something.
//
// Nothing on this screen is computed here. Every field is read from the §9.3
// decision the Guard wrote to `guard_decisions` and the audit chain returned.

import { ShieldCheck, ShieldX, Shield } from 'lucide-react'
import type { GuardDecision, GuardRule } from '../types'
import { clockTime, rupees, ruleValue } from '../lib/format'
import { Badge, Empty, Id, Panel, Skeleton } from './ui'

const RULE_ORDER = [
  'MG-001',
  'MG-002',
  'MG-003',
  'MG-004',
  'MG-005',
  'MG-006',
  'MG-007',
  'MG-008',
  'MG-009',
]

function ordered(rules: GuardRule[]): GuardRule[] {
  return [...rules].sort(
    (a, b) => RULE_ORDER.indexOf(a.rule_id) - RULE_ORDER.indexOf(b.rule_id),
  )
}

export default function GuardConsole({
  decision,
  loading,
}: {
  decision: GuardDecision | null
  loading: boolean
}) {
  const blocked = decision?.verdict === 'BLOCK'

  return (
    <Panel
      title="Transaction Guard"
      icon={
        blocked ? (
          <ShieldX size={14} className="text-fail" />
        ) : decision ? (
          <ShieldCheck size={14} className="text-pass" />
        ) : (
          <Shield size={14} />
        )
      }
      accent={decision ? (blocked ? 'fail' : 'pass') : 'none'}
      className="col-guard"
      right={
        decision ? (
          <>
            <span className="mono text-xs text-zinc-500">
              {decision.duration_ms} ms · {clockTime(decision.evaluated_at)}
            </span>
            <Badge tone={blocked ? 'fail' : 'pass'}>{decision.verdict}</Badge>
          </>
        ) : null
      }
    >
      {!decision && loading ? <Skeleton rows={9} /> : null}

      {!decision && !loading ? (
        <Empty>
          No submission has been evaluated yet. Nine rules run on every
          submit_purchase, before any order can exist.
        </Empty>
      ) : null}

      {decision ? <Decision key={decision.decision_id} decision={decision} /> : null}
    </Panel>
  )
}

function Decision({ decision }: { decision: GuardDecision }) {
  const rules = ordered(decision.rules)
  const failed = rules.find((r) => r.rule_id === decision.failed_rule_id) ?? null

  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-8 border-b border-zinc-800 px-4 py-3">
        <Meta label="decision">
          <Id value={decision.decision_id} />
        </Meta>
        <Meta label="requested">
          <span className="text-zinc-100">
            {rupees(decision.requested_total_paise)}
          </span>{' '}
          <span className="text-zinc-600">{decision.currency}</span>
        </Meta>
        <Meta label="mandate">
          <Id value={decision.mandate_id ?? '—'} />
        </Meta>
        <Meta label="quote">
          <Id value={decision.quote_id ?? '—'} />
        </Meta>
      </dl>

      {failed ? (
        <div className="animate-panel-in border-b border-zinc-800 bg-fail/10 px-4 py-3">
          <div className="flex items-center gap-2">
            <Badge tone="fail">{failed.rule_id}</Badge>
            <span className="mono text-sm font-medium text-fail">
              {decision.block_code}
            </span>
          </div>
          <p className="mt-2 text-sm text-zinc-200">{failed.detail}</p>
          <p className="mt-2 text-xs text-zinc-500">
            No Razorpay order was created. All nine rules were evaluated;
            failed_rule_id is the lowest-numbered failure. More than one rule can
            fail, and every failure is shown below.
          </p>
        </div>
      ) : null}

      <div className="rule-grid border-b border-zinc-800 px-4 py-2">
        <span />
        <span className="label">rule</span>
        <span className="label">check</span>
        <span className="label">observed</span>
        <span className="label">threshold</span>
      </div>

      <ol>
        {rules.map((rule, index) => (
          <RuleRow key={rule.rule_id} rule={rule} index={index} />
        ))}
      </ol>
    </div>
  )
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3 py-1">
      <dt className="label w-20 shrink-0">{label}</dt>
      <dd className="mono min-w-0 truncate text-xs text-zinc-300">{children}</dd>
    </div>
  )
}

function RuleRow({ rule, index }: { rule: GuardRule; index: number }) {
  const failed = !rule.passed
  return (
    <li
      // ~150ms apart, so nine rules read as an evaluation happening rather than
      // a table appearing. The animation is keyed to the decision id upstream,
      // so it runs once per decision and never on a re-render.
      style={{ animationDelay: `${index * 150}ms` }}
      className={`animate-rule-in rule-grid border-b border-zinc-800/60 px-4 py-3 last:border-b-0 ${
        failed ? 'bg-fail/10' : ''
      }`}
    >
      <span
        aria-hidden
        className={`mt-1 h-2 w-2 rounded-input ${failed ? 'bg-fail' : 'bg-pass'}`}
      />

      <span
        className={`mono text-sm font-medium ${failed ? 'text-fail' : 'text-pass'}`}
      >
        {rule.rule_id}
      </span>

      <span className="min-w-0">
        <span className="mono block text-sm text-zinc-200">{rule.name}</span>
        <span className="mt-1 block text-xs leading-5 text-zinc-500">
          {rule.detail}
        </span>
        {failed && rule.block_code ? (
          <span className="mono mt-2 inline-flex text-xs font-medium text-fail">
            {rule.block_code}
          </span>
        ) : null}
      </span>

      <Cell value={rule.observed} unit={rule.unit} failed={failed} />
      <Cell value={rule.threshold} unit={rule.unit} failed={false} />
    </li>
  )
}

function Cell({
  value,
  unit,
  failed,
}: {
  value: unknown
  unit: string
  failed: boolean
}) {
  return (
    <span className="min-w-0">
      <span
        className={`mono block break-words text-sm ${
          failed ? 'text-fail' : 'text-zinc-200'
        }`}
      >
        {ruleValue(value, unit)}
      </span>
      <span className="mt-1 block font-mono text-xs text-zinc-600">{unit}</span>
    </span>
  )
}
