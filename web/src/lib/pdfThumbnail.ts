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

export async function pdfFirstPageThumbnail(file: File): Promise<string | null> {
  try {
    const pdfjs = await import('pdfjs-dist')
    // The worker is fetched from the same bundle rather than a CDN: the app is
    // served under a strict origin and must not depend on a third-party host.
    pdfjs.GlobalWorkerOptions.workerSrc = (
      await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
    ).default

    const data = new Uint8Array(await file.arrayBuffer())
    // The loading task, not the document proxy, owns teardown.
    const task = pdfjs.getDocument({ data })
    try {
      const doc = await task.promise
      const page = await doc.getPage(1)
      const base = page.getViewport({ scale: 1 })
      const scale = THUMBNAIL_EDGE / Math.max(base.width, base.height)
      const viewport = page.getViewport({ scale })

      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.floor(viewport.width))
      canvas.height = Math.max(1, Math.floor(viewport.height))
      const context = canvas.getContext('2d')
      if (!context) return null

      await page.render({ canvas, canvasContext: context, viewport }).promise
      return canvas.toDataURL('image/jpeg', 0.8)
    } finally {
      // Release the worker and its buffers whether or not rendering succeeded.
      await task.destroy()
    }
  } catch {
    return null
  }
}
