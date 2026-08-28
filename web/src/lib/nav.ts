import type { Role } from '../auth/session'

export type View = 'overview' | 'new' | 'history' | 'dictionary' | 'how'

export interface NavItem {
  view: View
  label: string
  /** Roles that see this entry at all. */
  roles: readonly Role[]
}

/**
 * Employee and admin see the same five screens; the role changes what the
 * Overview and History screens contain, not which screens exist. Filtering is
 * client-side and is not access control.
 */
export const NAV: readonly NavItem[] = [
  { view: 'overview', label: 'Overview', roles: ['employee', 'admin'] },
  { view: 'new', label: 'New reconciliation', roles: ['employee', 'admin'] },
  { view: 'history', label: 'History', roles: ['employee', 'admin'] },
  { view: 'dictionary', label: 'Medicine dictionary', roles: ['employee', 'admin'] },
  { view: 'how', label: 'How it works', roles: ['employee', 'admin'] },
]

export function navItemsFor(role: Role): readonly NavItem[] {
  return NAV.filter((item) => item.roles.includes(role))
}
