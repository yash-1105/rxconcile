/**
 * The one place a spine state is put into words.
 *
 * The findings list, both tables and the legend all read this, so the
 * vocabulary is learned once and cannot drift between them. Kept out of
 * Spine.tsx so that file exports only components.
 */

import type { SpineState } from '../components/Spine'

export const STATUS_LABEL: Record<SpineState, string> = {
  clean: 'Matches',
  substitution: 'Substituted',
  warning: 'Check',
  problem: 'Problem',
  unchecked: 'Not checked',
  'out-of-scope': 'Out of scope',
}

export const STATUS_MEANING: Record<SpineState, string> = {
  problem: 'a real discrepancy between the documents',
  warning: 'worth a look, but not necessarily wrong',
  unchecked: 'we did not have what we needed to verify this',
  clean: 'the documents agree',
  substitution: 'same medicine, a different brand was dispensed',
  'out-of-scope': 'not a medicine, so outside reimbursement',
}

/** Order the marks are explained in: most serious first. */
export const LEGEND_ORDER: readonly SpineState[] = [
  'problem',
  'warning',
  'substitution',
  'unchecked',
  'clean',
]
