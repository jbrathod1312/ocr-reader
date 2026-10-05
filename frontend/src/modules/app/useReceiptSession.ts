import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { reduceProgress, type StageMap } from '../../lib/stage-state'
import {
  readDocument,
  ReaderError,
  readerIsReady,
  type DocumentMode,
  type DocumentPage,
  type ExtraTable,
  type PageFailure,
  type StatementSummary,
} from '../../ocr/api'
import { rowCells, shiftedAfterRemoval, withCell, withoutRow } from '../../ocr/result'
import type { ProgressEvent } from '../../ocr/types'
import { NO_EDITS, NO_WORDS, type PageRead, type Phase, type ReceiptSession } from './types'

/** A page the reader has just returned, with nothing edited or accepted yet. */
function freshRead(page: DocumentPage): PageRead {
  return {
    result: page.result,
    readResult: page.result,
    edited: NO_EDITS,
    validated: NO_EDITS,
    removed: 0,
    words: page.words,
  }
}

/**
 * Draw a page's picture on the canvas the viewer shows.
 *
 * The reader renders the pages now, so a picture is a URL rather than pixels
 * the browser made. It still lands on a canvas, because the overlay is drawn
 * over it, the zoom reads its size, and the download saves it.
 */
function drawPage(canvas: HTMLCanvasElement, url: string, signal: AbortSignal): void {
  const image = new Image()
  image.decoding = 'async'
  image.onload = () => {
    if (signal.aborted) return
    canvas.width = image.naturalWidth
    canvas.height = image.naturalHeight
    canvas.getContext('2d')?.drawImage(image, 0, 0)
  }
  image.src = url
}

const MODE_KEY = 'receipt-ocr:mode'

/** The kind the user chose last time, so it does not have to be chosen again. */
function storedMode(): DocumentMode {
  try {
    const saved = localStorage.getItem(MODE_KEY)
    return saved === 'bank' || saved === 'lottery' ? saved : 'receipt'
  } catch {
    return 'receipt'
  }
}

/** Upload, read, edit, and page through one document. */
export function useReceiptSession(): ReceiptSession {
  const [mode, setModeState] = useState<DocumentMode>(storedMode)
  const [statement, setStatement] = useState<StatementSummary | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [stages, setStages] = useState<StageMap>({})
  // Every page read, by page index. An image is page 0 of one.
  const [pages, setPages] = useState<ReadonlyMap<number, PageRead>>(new Map())
  const [pageErrors] = useState<ReadonlyMap<number, string>>(new Map())
  const [error, setError] = useState<string | null>(null)
  const [hasPreview, setHasPreview] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)
  const [imageDimensions, setImageDimensions] = useState<{ width: number; height: number } | null>(null)

  /**
   * Extra tables the reader has taken out of the export, by key. Kept here
   * rather than in the card that shows them so it survives paging: a table is
   * dropped for the document, not for the page it happens to be printed on.
   */
  const [droppedTables, setDroppedTables] = useState<ReadonlySet<string>>(new Set())
  const [documentPages, setDocumentPages] = useState<DocumentPage[]>([])
  const [extraTables, setExtraTables] = useState<ExtraTable[]>([])
  const [currentPage, setCurrentPage] = useState(0)
  const [isPdfMode, setIsPdfMode] = useState(false)

  // The kind and the file a read starts from, held where a callback made once
  // can still see them.
  const modeRef = useRef(mode)
  const fileRef = useRef<File | null>(null)

  const previewRef = useRef<HTMLCanvasElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const drawRef = useRef<AbortController | null>(null)
  // What the last reading held for its pictures, freed when it is replaced.
  const releaseRef = useRef<(() => void) | null>(null)

  const current = pages.get(currentPage)
  const result = current?.result ?? null
  const edited = current?.edited ?? NO_EDITS
  const validated = current?.validated ?? NO_EDITS
  const removed = current?.removed ?? 0
  const words = current?.words ?? NO_WORDS

  // Whether the reader is up, asked once at start-up so the dropzone can warn
  // before a file is chosen rather than after.
  useEffect(() => {
    const controller = new AbortController()
    void readerIsReady(controller.signal).then((ready) => {
      if (!ready && !controller.signal.aborted) {
        setError('The reader is not running. Start it with `pnpm reader`.')
      }
    })
    return () => controller.abort()
  }, [])

  /** The page on screen, drawn once the canvas it goes on is mounted. */
  useEffect(() => {
    const page = documentPages[currentPage]
    const canvas = previewRef.current
    if (!page || !canvas || !hasPreview) return
    drawRef.current?.abort()
    const controller = new AbortController()
    drawRef.current = controller
    drawPage(canvas, page.imageUrl, controller.signal)
    return () => controller.abort()
  }, [documentPages, currentPage, hasPreview])

  const onFile = useCallback(async (file: File) => {
    fileRef.current = file
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const progress = (event: ProgressEvent) => setStages((prev) => reduceProgress(prev, event))

    releaseRef.current?.()
    releaseRef.current = null
    setFileName(file.name)
    setPhase('running')
    setStages({})
    setPages(new Map())
    setDroppedTables(new Set())
    // The last file's picture must not stand in for this one while it loads.
    setHasPreview(false)
    setError(null)
    setDocumentPages([])
    setExtraTables([])
    setStatement(null)
    setCurrentPage(0)
    setIsPdfMode(false)

    progress({ stage: 'upload', status: 'start', message: 'sending the page to the reader…' })
    const started = performance.now()
    try {
      const read = await readDocument(file, controller.signal, modeRef.current)
      if (abortRef.current !== controller) return
      const elapsed = performance.now() - started
      progress({ stage: 'upload', status: 'done', elapsedMs: elapsed })
      progress({
        stage: 'read',
        status: 'done',
        message: `${read.pages.length} page${read.pages.length === 1 ? '' : 's'}`,
        elapsedMs: elapsed,
      })

      const first = read.pages[0]
      if (!first) throw new ReaderError('The reader found no pages in that file.')
      releaseRef.current = read.release
      setDocumentPages(read.pages)
      setExtraTables(read.extraTables)
      setStatement(read.statement)
      setIsPdfMode(read.pages.length > 1 || file.type === 'application/pdf')
      setPages(new Map(read.pages.map((page, index) => [index, freshRead(page)])))
      const rows = read.pages.reduce((total, page) => total + page.result.rows.length, 0)
      progress({ stage: 'rows', status: 'done', message: `${rows} rows` })
      setImageDimensions(first.size)
      setHasPreview(true)
      setPhase('done')
    } catch (e) {
      if (abortRef.current !== controller) return
      if (controller.signal.aborted) {
        setPhase('idle')
        return
      }
      progress({ stage: 'read', status: 'error' })
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }, [])

  // Edits belong to their page, so moving between pages keeps them and the
  // export carries every page's.
  const onEditPage = useCallback(
    (pageIndex: number, rowIndex: number, cellIndex: number, value: string) => {
      setPages((prev) => {
        const page = prev.get(pageIndex)
        if (!page) return prev
        return new Map(prev).set(pageIndex, {
          ...page,
          result: withCell(page.result, rowIndex, cellIndex, value),
          edited: new Set(page.edited).add(rowIndex),
          // Someone has just read this row against the page and typed what it
          // says, which is what accepting it means.
          validated: new Set(page.validated).add(rowIndex),
        })
      })
    },
    [],
  )

  /** A row taken out is gone from the page's reading and from every export. */
  const onRemoveRowPage = useCallback((pageIndex: number, rowIndex: number) => {
    setPages((prev) => {
      const page = prev.get(pageIndex)
      if (!page || rowIndex < 0 || rowIndex >= page.result.rows.length) return prev
      return new Map(prev).set(pageIndex, {
        ...page,
        result: withoutRow(page.result, rowIndex),
        edited: shiftedAfterRemoval(page.edited, rowIndex),
        validated: shiftedAfterRemoval(page.validated, rowIndex),
        removed: page.removed + 1,
      })
    })
  }, [])

  /**
   * Accept every row of the page on screen at once.
   *
   * The page on screen rather than the document: accepting a row says someone
   * has looked at it, and the pages nobody has turned to have not been.
   */
  const validateAll = useCallback(() => {
    setPages((prev) => {
      const page = prev.get(currentPage)
      if (!page) return prev
      const all = new Set(rowCells(page.result).map((_, index) => index))
      return new Map(prev).set(currentPage, { ...page, validated: all })
    })
  }, [currentPage])

  const resetEdits = () => {
    setPages((prev) => {
      const page = prev.get(currentPage)
      if (!page) return prev
      // The edits go, and so does the acceptance they carried with them.
      return new Map(prev).set(currentPage, {
        ...page,
        result: page.readResult,
        edited: NO_EDITS,
        validated: NO_EDITS,
        removed: 0,
      })
    })
  }

  const cancel = () => {
    abortRef.current?.abort()
    setPhase(pages.size > 0 ? 'done' : 'idle')
  }

  const clearCurrent = () => {
    abortRef.current?.abort()
    drawRef.current?.abort()
    releaseRef.current?.()
    releaseRef.current = null
    setPhase('idle')
    setPages(new Map())
    setDroppedTables(new Set())
    setError(null)
    setHasPreview(false)
    setFileName(null)
    setImageDimensions(null)
    setDocumentPages([])
    setExtraTables([])
    setStatement(null)
    fileRef.current = null
    setIsPdfMode(false)
    setCurrentPage(0)
  }

  /**
   * Choose what the document is. The reader is not asked to work it out: the
   * choice picks the route. A file already on screen is read again as the new
   * kind, because the rows on screen were read as the old one.
   */
  const setMode = useCallback(
    (next: DocumentMode) => {
      if (next === modeRef.current) return
      modeRef.current = next
      setModeState(next)
      try {
        localStorage.setItem(MODE_KEY, next)
      } catch {
        // A browser that will not remember the choice still has it for now.
      }
      if (fileRef.current) void onFile(fileRef.current)
    },
    [onFile],
  )

  /** Take an extra table out of the export, or put it back. */
  const toggleTable = useCallback((key: string) => {
    setDroppedTables((dropped) => {
      const next = new Set(dropped)
      if (!next.delete(key)) next.add(key)
      return next
    })
  }, [])

  /** Look at another page. Every page is read before any is shown. */
  const switchPdfPage = useCallback(
    (pageIndex: number) => {
      if (!documentPages[pageIndex]) return
      setCurrentPage(pageIndex)
      setImageDimensions(documentPages[pageIndex].size)
    },
    [documentPages],
  )

  const exportPages = useMemo(
    () =>
      [...pages.entries()]
        .sort(([a], [b]) => a - b)
        .map(([index, page]) => ({ page: index + 1, result: page.result })),
    [pages],
  )
  const pageError = pageErrors.get(currentPage)
  /** Pages the export has no rows for. The reader reads them all, so this is rare. */
  const failures = useMemo<PageFailure[]>(
    () =>
      documentPages.flatMap((_, index) =>
        pages.has(index) ? [] : [{ page: index + 1, message: 'This page produced no rows.' }],
      ),
    [documentPages, pages],
  )
  const tablePages = useMemo(
    () =>
      [...pages.entries()]
        .sort(([a], [b]) => a - b)
        .map(([index, page]) => ({
          index,
          result: page.result,
          edited: page.edited,
          validated: page.validated,
        })),
    [pages],
  )

  const busy = phase === 'running' || phase === 'booting'

  return {
    mode,
    setMode,
    statement,
    stages,
    pages,
    pageErrors,
    error,
    hasPreview,
    fileName,
    imageDimensions,
    documentPages,
    extraTables,
    currentPage,
    isPdfMode,
    previewRef,
    result,
    edited,
    validated,
    removed,
    words,
    busy,
    pageError,
    failures,
    exportPages,
    tablePages,
    droppedTables,
    toggleTable,
    onFile,
    onEditPage,
    onRemoveRowPage,
    validateAll,
    resetEdits,
    cancel,
    clearCurrent,
    switchPdfPage,
  }
}
