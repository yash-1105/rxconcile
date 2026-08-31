import { useState } from 'react'
import { openDemoSession } from '../api/client'
import { type Session } from '../auth/session'
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
      setError('Those demo credentials were not recognised.')
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
          </section>

          <SpineRule className="hidden self-stretch md:block" />

          <section className="md:pl-12">
            <form onSubmit={(event) => void submit(event)} className="rounded bg-surface p-6">
              <h2 className="t-title text-ink">Sign in</h2>

              <label className="mt-5 block">
                <span className="t-micro text-muted">Email</span>
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="off"
                  className="t-body mt-1.5 w-full rounded bg-ink-50 px-3 py-2.5 text-ink placeholder:text-ink-400"
                  placeholder="you@example.com"
                />
              </label>

              <label className="mt-4 block">
                <span className="t-micro text-muted">Password</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="off"
                  className="t-body mt-1.5 w-full rounded bg-ink-50 px-3 py-2.5 text-ink"
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

            </form>
          </section>
        </div>
      </main>

    </div>
  )
}
