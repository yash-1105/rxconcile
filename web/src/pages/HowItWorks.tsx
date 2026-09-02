/**
 * How it works: the diagram, and nothing else.
 *
 * This page carried explanatory cards, per-step prose and a "what it does not
 * do" section. All of it was removed deliberately in a copy pass — the diagram
 * is the page now.
 *
 * Worth knowing for whoever reads this next: those cards were the only place in
 * the UI that told a reader this is a proof of concept, gives no medical advice
 * and makes no insurance decision. That disclosure now lives only in the README
 * and the API description. Nothing here CLAIMS otherwise, so no hard rule is
 * broken, but if a client-facing statement is wanted again this is where it was.
 */

import { Pipeline } from '../components/Pipeline'
import { PageHeader } from '../components/Shell'

export function HowItWorks() {
  return (
    <>
      <PageHeader title="How it works" />
      <Pipeline />
    </>
  )
}
