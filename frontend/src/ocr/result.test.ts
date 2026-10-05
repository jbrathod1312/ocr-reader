import { describe, expect, it } from 'vitest'

import { rowCells, shiftedAfterRemoval, withoutRow } from './result'
import type { OcrResult } from './types'

const result = {
  kind: 'table',
  headers: ['A', 'B'],
  rows: [
    { cells: ['1', 'a'], confidence: 1 },
    { cells: ['2', 'b'], confidence: 1 },
    { cells: ['3', 'c'], confidence: 1 },
  ],
  tables: [{ headers: ['A', 'B'], rows: [{ cells: ['1', 'a'], confidence: 1 }, { cells: ['2', 'b'], confidence: 1 }, { cells: ['3', 'c'], confidence: 1 }] }],
  validation: [
    { code: 'invoice-total', message: 'x', rows: [1] },
    { code: 'invoice-total', message: 'y', rows: [2] },
  ],
  skipped: [],
} as unknown as OcrResult

describe('withoutRow', () => {
  it('takes the row out of the reading and of its own table', () => {
    const out = withoutRow(result, 1)
    expect(rowCells(out)).toEqual([['1', 'a'], ['3', 'c']])
    expect(out.tables[0]?.rows).toHaveLength(2)
  })

  it('moves the checks on the rows below up, and drops the removed row\'s own', () => {
    const out = withoutRow(result, 1)
    expect(out.validation.map((issue) => issue.rows)).toEqual([[1]])
  })
})

describe('shiftedAfterRemoval', () => {
  it('forgets the removed row and renumbers those after it', () => {
    expect([...shiftedAfterRemoval(new Set([0, 1, 3]), 1)].sort()).toEqual([0, 2])
  })
})
