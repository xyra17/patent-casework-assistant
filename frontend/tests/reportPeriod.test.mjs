import test from 'node:test'
import assert from 'node:assert/strict'
import {reportPeriod,monthLabel,monthRangeError} from '../src/reportPeriod.js'

test('monthly presets use complete months and roll across years',()=>{
  const now=new Date('2026-01-15T00:00:00Z')
  assert.deepEqual(reportPeriod('last',now),['2025-12','2025-12'])
  assert.deepEqual(reportPeriod('last3',now),['2025-10','2025-12'])
  assert.deepEqual(reportPeriod('last6',now),['2025-07','2025-12'])
  assert.deepEqual(reportPeriod('year',now),['2026-01','2026-01'])
})

test('business month follows Shanghai time regardless of browser zone',()=>{
  assert.deepEqual(reportPeriod('last',new Date('2026-09-30T15:59:00Z')),['2026-08','2026-08'])
  assert.deepEqual(reportPeriod('last',new Date('2026-09-30T16:00:00Z')),['2026-09','2026-09'])
})

test('range validation allows one through 36 months with readable labels',()=>{
  assert.equal(monthRangeError(['2026-02','2026-02']),'')
  assert.equal(monthRangeError(['2024-01','2026-12']),'')
  for(const range of [null,[],['2026-00','2026-10'],['2026-10','2026-09'],['2023-12','2026-12']])assert.ok(monthRangeError(range))
  assert.equal(monthLabel('2026-09'),'2026年9月')
})
