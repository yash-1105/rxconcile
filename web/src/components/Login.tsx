import { useState } from 'react'
import { openDemoSession } from '../api/client'
import { DEMO_ACCOUNTS, type Session } from '../auth/session'
import { SpineRule } from './Spine'

export function Login({ onSignIn }: { onSignIn: (session: Session) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      // The server issues the token and decides the role; nothing here does.
      const demo = await openDemoSession(email, password)
      setError(null)
      onSignIn({
        email: demo.email,
        name: demo.name,
        employeeNumber: demo.employee_number,
        role: demo.role,
        token: demo.token,
      })
    } catch {
      setError('Those demo credentials were not recognised. Use one of the buttons below.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full flex-col">
      <main className="mx-auto flex w-full max-w-5xl flex-1 items-center px-6 py-10">
        <div className="grid w-full gap-10 md:grid-cols-[1fr_auto_1fr] md:gap-0">
          <section className="flex flex-col justify-center md:pr-12">
            <h1 className="t-display text-ink">rxconcile</h1>
            <p className="t-body mt-3 max-w-sm text-muted">
              Compares a pharmacy bill against the prescription it was dispensed from, and
              reports exactly where the two documents disagree.
            </p>
            <p className="t-small mt-6 max-w-sm text-muted">
              Proof of concept for medical reimbursement audit. Automated document comparison
              only — every finding needs human review.
            </p>
          </section>

          <SpineRule className="hidden self-stretch md:block" />

          <section className="md:pl-12">
            <form onSubmit={(event) => void submit(event)} className="rounded bg-surface p-6">
              <h2 className="t-title text-ink">Demo access</h2>
              <p className="t-small mt-1 text-muted">Not a secure login.</p>

              <label className="mt-5 block">
                <span className="t-micro text-muted">Email</span>
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="off"
                  className="t-data mt-1.5 w-full rounded bg-ink-50 px-3 py-2 text-ink placeholder:text-ink-400"
                  placeholder="employee@gmail.com"
                />
              </label>

              <label className="mt-4 block">
                <span className="t-micro text-muted">Password</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="off"
                  className="t-data mt-1.5 w-full rounded bg-ink-50 px-3 py-2 text-ink"
                  placeholder="employee123"
                />
              </label>

              {error ? <p className="t-small mt-3 text-flag">{error}</p> : null}

              <button
                type="submit"
                disabled={busy}
                className="mt-5 w-full rounded bg-seal px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
              >
                {busy ? 'Signing in…' : 'Continue'}
              </button>

              {/* The credentials are on the screen on purpose: someone demoing
                  this should not have to be told the password, and hiding them
                  would imply they are protecting something. */}
              <div className="mt-6 border-t border-ink-200 pt-4">
                <p className="t-micro text-muted">Fill in a demo account</p>
                <div className="mt-2 grid gap-2">
                  {DEMO_ACCOUNTS.map((account) => (
                    <button
                      key={account.email}
                      type="button"
                      onClick={() => {
                        setEmail(account.email)
                        setPassword(account.password)
                        setError(null)
                      }}
                      className="flex items-baseline justify-between gap-3 rounded bg-ink-50 px-3 py-2 text-left hover:bg-ink-100"
                    >
                      <span className="t-data text-ink">{account.email}</span>
                      <span className="t-data text-muted">{account.password}</span>
                    </button>
                  ))}
                </div>
              </div>
            </form>
          </section>
        </div>
      </main>

      <footer className="px-6 py-5">
        <div className="mx-auto max-w-5xl">
          <p className="t-small text-muted">Demo access. Not a secure login.</p>
          <p className="t-small mt-1 text-muted">
            Proof of concept. Automated document comparison only, not clinical verification. All
            findings require human review.
          </p>
        </div>
      </footer>
    </div>
  )
}
