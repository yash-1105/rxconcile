/**
 * Spotting the same file chosen twice.
 *
 * Uploading one document as both the pharmacy bill and the lab bill is a slip,
 * not a duplicate claim. Merged, it produced two copies of every lab line and
 * the engine correctly reported TEST_DUPLICATE — a finding about the operator's
 * mouse, not about the pharmacy. Caught here instead, before anything is sent.
 *
 * Hashed rather than compared by name and size: two different bills can share
 * both, and a renamed copy of one file shares neither.
 */

export async function hashFile(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * Pairs of slots holding byte-identical files.
 *
 * Returns the labels of every colliding pair, so the message can name them.
 * An empty result means every supplied file is distinct.
 */
export async function findDuplicateFiles(
  files: ReadonlyArray<{ key: string; label: string; file: File | null }>,
): Promise<Array<[string, string]>> {
  const present = files.filter((entry): entry is typeof entry & { file: File } =>
    entry.file !== null,
  )
  const hashes = await Promise.all(present.map((entry) => hashFile(entry.file)))
  const collisions: Array<[string, string]> = []
  for (let i = 0; i < present.length; i += 1) {
    for (let j = i + 1; j < present.length; j += 1) {
      if (hashes[i] === hashes[j]) collisions.push([present[i]!.label, present[j]!.label])
    }
  }
  return collisions
}
