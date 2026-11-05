import test from 'node:test'
import assert from 'node:assert/strict'
import { sessionOutputFiles } from '../src/sessionFiles.js'

test('generated files exclude attachment working copies and directories, preserving nested names', () => {
  const files = [
    { type: 'file', path: '/session/input.pdf', name: 'input.pdf', mtime: 9 },
    { type: 'dir', path: '/session/reports', name: 'reports', mtime: 8 },
    { type: 'file', path: '/session/reports/input.pdf', name: 'input.pdf', mtime: 3 },
    { type: 'file', path: '/session/report.md', name: 'report.md', mtime: 5 },
  ]
  assert.deepEqual(sessionOutputFiles(files, [{ name: 'input.pdf' }], '/session/').map(f => f.path),
    ['/session/report.md', '/session/reports/input.pdf'])
  assert.equal(files.length, 4)
  assert.equal(files[0].path, '/session/input.pdf')
  assert.deepEqual(sessionOutputFiles([], [], ''), [])
})
