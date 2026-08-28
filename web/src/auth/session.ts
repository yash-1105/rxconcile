/**
 * Demo access. Not a secure login.
 *
 * The credentials live on the server, in api/rxconcile/demo_auth.py, and are
 * hardcoded there. The role only filters what the client renders, and nothing
 * here restricts access to anything. This file no longer carries the passwords
 * -- not because that would protect them, but because the sign-in form has no
 * reason to know them.
 *
 * Session lives in sessionStorage, so closing the tab ends it.
 */

export type Role = 'employee' | 'admin'

export interface Session {
  email: string
  name: string
  employeeNumber: string
  role: Role
  /**
   * Issued by the server. This, not the role below, is what the API trusts —
   * the server looks the role up from the email the token was bound to, so a
   * client claiming 'admin' here changes nothing about what it can see.
   */
  token: string
}

const STORAGE_KEY = 'rxconcile.demo-session'

export function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<Session>
    if (!parsed.email || !parsed.role || !parsed.token) return null
    return {
      email: parsed.email,
      name: parsed.name ?? parsed.email,
      employeeNumber: parsed.employeeNumber ?? '',
      role: parsed.role,
      token: parsed.token,
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
