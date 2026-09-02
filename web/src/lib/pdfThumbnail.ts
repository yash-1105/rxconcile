/**
 * A real thumbnail of a PDF's first page, so the upload box shows the document
 * rather than describing it.
 *
 * pdf.js is imported dynamically and never lands in the main bundle. It is
 * several times the size of the whole application, and most claims are
 * photographs — a reader who never attaches a PDF should never download a PDF
 * renderer. Vite code-splits the dynamic import automatically.
 *
 * Every failure path returns null and the caller falls back to showing nothing.
 * A preview is a convenience; it must never cost somebody their upload.
 */

/** Longest edge of the generated thumbnail, in device pixels. */
const THUMBNAIL_EDGE = 640

/**
 * How long to wait before giving up on a preview.
 *
 * Belt and braces against the failure above: a promise that never settles is
 * indistinguishable from one that is slow, and a box that spins forever is
 * worse than one that stays empty.
 */
const RENDER_TIMEOUT_MS = 15_000

function withTimeout<T>(work: Promise<T>): Promise<T> {
  return Promise.race([
    work,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error('pdf preview timed out')), RENDER_TIMEOUT_MS),
    ),
  ])
}

export async function pdfFirstPageThumbnail(file: File): Promise<string | null> {
  try {
    const pdfjs = await import('pdfjs-dist')

    // The worker is built here rather than handed to pdf.js as a URL.
    //
    // pdf.js ships its worker as an ES module. Given only `workerSrc`, pdf.js
    // starts it as a CLASSIC worker, the module syntax fails inside it, and
    // `getDocument().promise` then never settles at all -- no error, no
    // rejection, just a preview box that waits forever. Constructing the worker
    // with `{ type: 'module' }` and passing the port is the documented Vite
    // recipe, and it also keeps the worker on this origin rather than a CDN.
    pdfjs.GlobalWorkerOptions.workerPort = new Worker(
      new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url),
      { type: 'module' },
    )

    const data = new Uint8Array(await file.arrayBuffer())
    // The loading task, not the document proxy, owns teardown.
    const task = pdfjs.getDocument({ data })
    try {
      const doc = await withTimeout(task.promise)
      const page = await doc.getPage(1)
      const base = page.getViewport({ scale: 1 })
      const scale = THUMBNAIL_EDGE / Math.max(base.width, base.height)
      const viewport = page.getViewport({ scale })

      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.floor(viewport.width))
      canvas.height = Math.max(1, Math.floor(viewport.height))
      const context = canvas.getContext('2d')
      if (!context) return null

      await withTimeout(page.render({ canvas, canvasContext: context, viewport }).promise)
      return canvas.toDataURL('image/jpeg', 0.8)
    } finally {
      // Release the worker and its buffers whether or not rendering succeeded.
      await task.destroy()
    }
  } catch {
    return null
  }
}
