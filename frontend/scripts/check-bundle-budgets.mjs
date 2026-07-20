import { gzipSync } from 'node:zlib'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const assetsDir = new URL('../dist/assets/', import.meta.url)
const budgets = {
  chunkGzipBytes: 180 * 1024,
  pdfWorkerGzipBytes: 450 * 1024,
}

const failures = []
for (const name of readdirSync(assetsDir)) {
  if (!/\.(js|mjs)$/.test(name)) continue
  const compressedBytes = gzipSync(readFileSync(join(assetsDir.pathname, name))).byteLength
  const limit = name.startsWith('pdf.worker.')
    ? budgets.pdfWorkerGzipBytes
    : budgets.chunkGzipBytes
  if (compressedBytes > limit) {
    failures.push(`${name}: ${(compressedBytes / 1024).toFixed(1)} KiB gzip > ${(limit / 1024).toFixed(0)} KiB`)
  }
}

if (failures.length) {
  console.error('Bundle budget exceeded:\n' + failures.map(item => `- ${item}`).join('\n'))
  process.exit(1)
}

console.log('Bundle budgets passed (180 KiB gzip per chunk; 450 KiB for the lazy PDF worker).')
