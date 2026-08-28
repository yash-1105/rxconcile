/**
 * Demo access. Not a secure login.
 *
 * Credentials are hardcoded in this file and therefore readable by anyone who
 * opens devtools, and the role only filters what the client renders. Nothing
 * here restricts access to anything. Storing the demo password in plain sight
 * is the honest option: hashing it would imply a protection that does not exist.
 *
 * Session lives in sessionStorage, so closing the tab ends it.
 */

export type Role = 'employee' | 'admin'

export interface Account {
  email: string
  password: string
  name: string
  employeeNumber: string
  role: Role
}

/** Both demo accounts, shown on the sign-in screen as fill-in buttons. */
export const DEMO_ACCOUNTS: readonly Account[] = [
  {
    email: 'employee@gmail.com',
    password: 'employee123',
    name: 'Priya Nair',
    employeeNumber: 'EMP-4417',
    role: 'employee',
  },
  {
    email: 'admin@gmail.com',
    password: 'admin123',
    name: 'Rahul Mehta',
    employeeNumber: 'ADM-0001',
    role: 'admin',
  },
]

export interface Session {
  email: string
  name: string
  employeeNumber: string
  role: Role
}

const STORAGE_KEY = 'rxconcile.demo-session'

export function authenticate(email: string, password: string): Session | null {
  const match = DEMO_ACCOUNTS.find(
    (account) =>
      account.email.toLowerCase() === email.trim().toLowerCase() &&
      account.password === password,
  )
  if (!match) return null
  return {
    email: match.email,
    name: match.name,
    employeeNumber: match.employeeNumber,
    role: match.role,
  }
}

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<Session>
    if (!parsed.email || !parsed.role) return null
    return {
      email: parsed.email,
      name: parsed.name ?? parsed.email,
      employeeNumber: parsed.employeeNumber ?? '',
      role: parsed.role,
    }
  } catch {
    return null
  }
}

export function saveSession(session: Session): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session))
  } catch {
    /* a session that cannot persist still works for this tab */
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    /* nothing to clear */
  }
}

export function roleLabel(role: Role): string {
  return role === 'admin' ? 'Admin' : 'Employee'
}
