import { useState } from 'react'
import { roleLabel, type Session } from '../auth/session'
import { navItemsFor, type View } from '../lib/nav'
import { SpineRule } from './Spine'

export function Shell({
  session,
  view,
  onNavigate,
  onSignOut,
  children,
}: {
  session: Session
  view: View
  onNavigate: (view: View) => void
  onSignOut: () => void
  children: React.ReactNode
}) {
  const [collapsed, setCollapsed] = useState(false)
  const items = navItemsFor(session.role)

  return (
    <div className="flex min-h-full">
      {/* Sticky: the demo marker and sign-out must stay in view on long pages,
          not scroll away with the content. */}
      <aside
        // Narrow screens get the collapsed rail whatever the toggle says: a
        // 240px sidebar beside content on a 380px screen leaves neither room.
        className={`sticky top-0 flex h-screen shrink-0 flex-col bg-paper transition-[width] duration-150 w-14 ${
          collapsed ? 'sm:w-14' : 'sm:w-60'
        }`}
      >
        <div className="flex items-center justify-between px-4 py-4">
          {!collapsed ? (
            <span className="t-title hidden tracking-tight text-ink sm:inline">rxconcile</span>
          ) : null}
          <button
            type="button"
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="t-small rounded px-1.5 py-1 text-muted hover:bg-ink-100 hover:text-ink"
          >
            {collapsed ? '»' : '«'}
          </button>
        </div>

        <nav className="mt-2 flex-1 overflow-y-auto px-2" aria-label="Main">
          <ul className="space-y-0.5">
            {items.map((item) => {
              const active = item.view === view
              return (
                <li key={item.view}>
                  <button
                    type="button"
                    onClick={() => onNavigate(item.view)}
                    aria-current={active ? 'page' : undefined}
                    title={collapsed ? item.label : undefined}
                    className={`flex w-full items-center gap-2.5 rounded px-2.5 py-2 text-left text-sm transition-colors ${
                      active
                        ? 'bg-surface font-medium text-ink'
                        : 'text-muted hover:bg-ink-100 hover:text-ink'
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        active ? 'bg-seal' : 'bg-transparent'
                      }`}
                    />
                    {!collapsed ? (
                      <span className="hidden truncate sm:inline">{item.label}</span>
                    ) : null}
                  </button>
                </li>
              )
            })}
          </ul>
        </nav>

        <div className="px-4 py-4">
          {!collapsed ? (
            <div className="hidden sm:block">
              <p className="t-body font-medium text-ink">{session.name}</p>
              <p className="t-small text-muted">
                {roleLabel(session.role)} · {session.employeeNumber}
              </p>
              <button
                type="button"
                onClick={onSignOut}
                className="t-small mt-2 rounded px-0 text-muted underline decoration-ink-300 underline-offset-4 hover:text-ink"
              >
                Sign out
              </button>
              {/* One quiet line, per the amended hard rule 8. */}
              <p className="t-small mt-4 text-muted">Demo access</p>
            </div>
          ) : (
            <button
              type="button"
              onClick={onSignOut}
              title="Sign out"
              aria-label="Sign out"
              className="t-small text-muted hover:text-ink"
            >
              ⏻
            </button>
          )}
        </div>
      </aside>

      {/* The spine: the same rule that divides prescribed from billed. */}
      <SpineRule className="sticky top-0 h-screen self-start" />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* One width for the whole page. Everything inside shares these edges,
            so the summary panel, the tables and every other widget line up. */}
        <main className="min-w-0 flex-1 px-6 py-8 sm:px-10">
          <div className="mx-auto w-full max-w-[1560px]">{children}</div>
        </main>
      </div>
    </div>
  )
}

/** Shared page heading, so every screen has one type size for its title. */
export function PageHeader({
  title,
  lede,
  actions,
}: {
  title: string
  lede?: string
  actions?: React.ReactNode
}) {
  return (
    <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="t-display text-ink">{title}</h1>
        {lede ? <p className="t-body mt-2 max-w-2xl text-muted">{lede}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-3">{actions}</div> : null}
    </header>
  )
}

/** An empty state is an invitation to act, not an error. */
export function EmptyState({
  title,
  body,
  action,
}: {
  title: string
  body: string
  action?: React.ReactNode
}) {
  return (
    <div className="rounded bg-surface px-6 py-12 text-center">
      <p className="t-title text-ink">{title}</p>
      <p className="t-body mx-auto mt-2 max-w-md text-muted">{body}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  )
}
