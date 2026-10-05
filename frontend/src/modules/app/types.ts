import type { RefObject } from 'react'

import type { StageMap } from '../../lib/stage-state'
import type {
  DocumentMode,
  DocumentPage,
  ExportPage,
  ExtraTable,
  PageFailure,
  StatementSummary,
} from '../../ocr/api'
import type { OcrResult, WordBox } from '../../ocr/types'
import type { PageRows } from '../../components/ResultView'

export type Phase = 'idle' | 'booting' | 'running' | 'done' | 'error'

/** One page's reading: as read, as edited, and which rows were edited. */
export interface PageRead {
  result: OcrResult
  readResult: OcrResult
  edited: ReadonlySet<number>
  /**
   * Rows the user has accepted by hand. A row the checks flagged is still
   * flagged in the data; this says someone has since looked at it and it
   * reads as printed.
   */
  validated: ReadonlySet<number>
  /** How many rows have been taken out of this page. */
  removed: number
  /** The words the page was read from, for the viewer's text overlay. */
  words: readonly WordBox[]
}

export const NO_EDITS: ReadonlySet<number> = new Set()
export const NO_WORDS: readonly WordBox[] = []

export function resultTitle(result: OcrResult): string {
  return result.title || 'Table'
}

/** The reading session the page layout renders. */
export interface ReceiptSession {
  /** What the user says the next file is: nothing here works it out. */
  mode: DocumentMode
  /** Choose the kind of document; a file already loaded is read again as it. */
  setMode: (mode: DocumentMode) => void
  /** What a bank statement says about itself, when one was read. */
  statement: StatementSummary | null
  stages: StageMap
  pages: ReadonlyMap<number, PageRead>
  pageErrors: ReadonlyMap<number, string>
  error: string | null
  hasPreview: boolean
  fileName: string | null
  imageDimensions: { width: number; height: number } | null
  /** Every page of the document, with where to fetch its picture. */
  documentPages: DocumentPage[]
  /** The tables printed under the pages' own, as the reader gathered them. */
  extraTables: ExtraTable[]
  currentPage: number
  isPdfMode: boolean
  previewRef: RefObject<HTMLCanvasElement | null>
  result: OcrResult | null
  edited: ReadonlySet<number>
  /** Rows of the page on screen the user has accepted by hand. */
  validated: ReadonlySet<number>
  /** How many rows of the page on screen have been removed. */
  removed: number
  /** The words the page on screen was read from, for the text overlay. */
  words: readonly WordBox[]
  busy: boolean
  pageError: string | undefined
  /** Pages the export has no rows for, with why, for the skipped log. */
  failures: PageFailure[]
  exportPages: ExportPage[]
  tablePages: PageRows[]
  /** Extra tables left out of the JSON export, by `ExtraTable.key`. */
  droppedTables: ReadonlySet<string>
  /** Take an extra table out of the JSON export, or put it back. */
  toggleTable: (key: string) => void
  onFile: (file: File) => Promise<void>
  onEditPage: (pageIndex: number, rowIndex: number, cellIndex: number, value: string) => void
  /** Take a row out of a page, and so out of the export. */
  onRemoveRowPage: (pageIndex: number, rowIndex: number) => void
  /** Accept every row of the page on screen that is still waiting on a look. */
  validateAll: () => void
  resetEdits: () => void
  cancel: () => void
  clearCurrent: () => void
  switchPdfPage: (pageIndex: number) => void
}
