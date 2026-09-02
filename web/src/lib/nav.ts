import type { Role } from '../auth/session'

export type View = 'overview' | 'queue' | 'new' | 'history' | 'dictionary' | 'how'

export interface NavItem {
  view: View
  label: string
  /**
   * Per-role wording for the same screen.
   *
   * An admin *verifies* a claim; an employee *submits* one. It is the same
   * route and the same upload form -- what differs is who is doing it and what
   * happens next, and the label should say so.
   */
  labelByRole?: Partial<Record<Role, string>>
  /** Roles that see this entry at all. */
  roles: readonly Role[]
}

/**
 * The review queue is the one screen only a reviewer has, because it is the
 * only one with no employee-facing counterpart: an employee has no queue, they
 * have their own claims. Every other screen exists for both roles and changes
 * its contents, not its presence. Filtering here is client-side and is not
 * access control -- the queue's data comes from an endpoint that decides for
 * itself who may read it.
 */
export const NAV: readonly NavItem[] = [
  { view: 'overview', label: 'Overview', roles: ['employee', 'admin'] },
  { view: 'queue', label: 'Review queue', roles: ['admin'] },
  {
    view: 'new',
    label: 'Verify',
    labelByRole: { employee: 'Submit claim' },
    roles: ['employee', 'admin'],
  },
  { view: 'history', label: 'History', roles: ['employee', 'admin'] },
  { view: 'dictionary', label: 'Medicine dictionary', roles: ['employee', 'admin'] },
  { view: 'how', label: 'How it works', roles: ['employee', 'admin'] },
]

export function navItemsFor(role: Role): readonly NavItem[] {
  return NAV.filter((item) => item.roles.includes(role)).map((item) => ({
    ...item,
    label: item.labelByRole?.[role] ?? item.label,
  }))
}

/**
 * Where each role lands.
 *
 * A reviewer opens on the work. An employee opens on their own standing --
 * allowance, and what they have waiting -- because they have no queue to work.
 */
export function landingFor(role: Role): View {
  return role === 'admin' ? 'queue' : 'overview'
}
