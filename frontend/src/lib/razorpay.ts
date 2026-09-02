// §12.3 — the Razorpay checkout modal.
//
// Two things this file is careful about:
//
//  1. Only `RAZORPAY_KEY_ID` reaches the browser, and it is read at runtime
//     from `/api/ui/config` rather than baked into the bundle (§4.3).
//  2. What the handler receives is a claim, not a result. It is posted to
//     `/api/payments/verify` and the console then re-reads §7.9. Nothing in
//     this file writes a payment status anywhere (invariant 4).

const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js'

export interface CheckoutResponse {
  razorpay_payment_id: string
  razorpay_order_id: string
  razorpay_signature: string
}

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void }
  }
}

let loading: Promise<void> | null = null

export function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve()
  if (loading) return loading
  loading = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = CHECKOUT_SRC
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => {
      loading = null
      reject(new Error('checkout.js could not be loaded.'))
    }
    document.head.appendChild(script)
  })
  return loading
}

export async function openCheckout(options: {
  keyId: string
  amountPaise: number
  razorpayOrderId: string
  orderId: string
  onResponse: (response: CheckoutResponse) => void
  onDismiss: () => void
}): Promise<void> {
  await loadCheckout()
  if (!window.Razorpay) throw new Error('checkout.js did not register Razorpay.')

  new window.Razorpay({
    key: options.keyId,
    amount: options.amountPaise,
    currency: 'INR',
    order_id: options.razorpayOrderId,
    name: 'Kavach',
    description: options.orderId,
    handler: options.onResponse,
    modal: { ondismiss: options.onDismiss },
    theme: { color: '#0b0d10' },
  }).open()
}
