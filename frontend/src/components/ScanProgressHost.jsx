// Floating scan progress, top-left corner.
//
// The requirement was explicit: «Должен быть всплывающий в левом верхнем углу
// Progress bar со статусом получения обложек». A scan can take tens of seconds
// (one Last.fm lookup per album without artwork), so the admin needs to see the
// stage and the cover counter without staying on the button that started it.
//
// Hosted at the App level, fed by `store/scanStore`, so it works no matter which
// screen the admin navigates to while the job runs.
import React from 'react'
import { AlertTriangle, CheckCircle2, Image as ImageIcon, Loader2, X } from 'lucide-react'
import { useScanStore } from '../store/scanStore'
import { describeScan } from '../utils/scanReport'
import { SCAN_STAGE_LABEL, percentOf, scanCounter } from '../utils/scanProgress'

function Counter({ progress }) {
  const counter = scanCounter(progress)
  if (!counter) return null
  return (
    <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--dim)', flexShrink: 0 }}>
      {counter.current} / {counter.total}
    </span>
  )
}

function ScanJobCard({ job }) {
  const dismissJob = useScanStore((state) => state.dismissJob)
  const progress = job.progress || {}
  const percent = percentOf(progress)
  const stage = progress.stage || 'idle'
  const running = job.status === 'running'
  const failed = job.status === 'failed'
  // While covers are being fetched the interesting part is the counter, so the
  // bar goes indeterminate when there is no known total yet.
  const message = progress.message
    || (failed ? job.error : '')
    || (stage === 'done' ? 'Готово' : 'Сканирование…')

  const tone = failed ? 'var(--danger)' : 'var(--accent)'
  // Same surface language as `.tmusic-toast`, so the card reads as part of the
  // app rather than a browser default. A failed job tints red.
  const background = failed ? 'rgba(255, 107, 107, 0.12)' : 'rgba(28, 25, 22, 0.94)'

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`Сканирование «${job.display_name}»`}
      style={{
        pointerEvents: 'auto',
        width: 'min(340px, calc(100vw - 24px))',
        boxSizing: 'border-box',
        padding: '10px 12px',
        borderRadius: 12,
        border: `1px solid ${failed ? 'rgba(255,107,107,0.35)' : 'var(--line)'}`,
        background,
        color: 'var(--ink)',
        boxShadow: '0 12px 32px rgba(0,0,0,0.45)',
        display: 'grid',
        gap: 7,
      }}
      className="tmusic-scan-card"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {running ? (
          <Loader2 size={15} className="animate-spin" style={{ color: tone, flexShrink: 0 }} />
        ) : failed ? (
          <AlertTriangle size={15} style={{ color: 'var(--danger)', flexShrink: 0 }} />
        ) : (
          <CheckCircle2 size={15} style={{ color: tone, flexShrink: 0 }} />
        )}
        <span style={{ fontSize: 12.5, fontWeight: 800, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={job.display_name}>
          {job.display_name}
        </span>
        {running && (
          <span style={{ fontSize: 10.5, color: 'var(--dim)', flexShrink: 0 }}>
            {SCAN_STAGE_LABEL[stage] || stage}
          </span>
        )}
        <button
          type="button"
          onClick={() => dismissJob(job.job_id)}
          aria-label="Закрыть"
          title="Закрыть"
          style={{
            background: 'none', border: 'none', padding: 0, cursor: 'pointer',
            color: 'var(--dim)', display: 'inline-flex', flexShrink: 0, minHeight: 22, minWidth: 22,
            alignItems: 'center', justifyContent: 'center',
          }}
        >
          <X size={14} />
        </button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--dim)' }}>
        <ImageIcon size={12} style={{ flexShrink: 0 }} />
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={message}>
          {message}
        </span>
        <Counter progress={progress} />
      </div>

      <div
        role="progressbar"
        aria-label={`Прогресс сканирования «${job.display_name}»`}
        aria-valuemin={0}
        aria-valuemax={percent === null ? undefined : 100}
        aria-valuenow={percent === null ? undefined : percent}
        aria-valuetext={percent === null ? undefined : `${percent}%`}
        style={{
          height: percent === null ? 4 : 6,
          borderRadius: 3,
          background: 'var(--line)',
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {percent === null ? (
          // Unknown total (artist lookup): an indeterminate stripe reads as
          // "working" without lying about a percentage.
          <div
            className="tmusic-scan-indeterminate"
            style={{ position: 'absolute', inset: 0, width: '40%', background: tone, borderRadius: 3 }}
          />
        ) : (
          <div
            style={{
              height: '100%',
              width: `${percent}%`,
              background: tone,
              transition: 'width 0.2s ease',
            }}
          />
        )}
      </div>

      {!running && job.report && (
        <div style={{ fontSize: 11.5, color: failed ? 'var(--danger)' : 'var(--ink)', lineHeight: 1.4 }}>
          {failed ? (job.error || 'Сканирование не удалось.') : describeScan(job.report)}
        </div>
      )}
    </div>
  )
}

export function ScanProgressHost() {
  const jobs = useScanStore((state) => state.jobs)
  if (!jobs.length) return null
  return (
    <div
      aria-label="Прогресс сканирования каталога"
      style={{
        position: 'fixed',
        // "Левый верхний угол" — below the top bar's own controls but clearly
        // floating over the content, and above every other layer except modals.
        top: 'calc(12px + var(--safe-top) + var(--offline-banner-h, 0px))',
        left: 'max(12px, var(--safe-left))',
        zIndex: 'var(--z-scan)',
        display: 'grid',
        gap: 8,
        // The host must never swallow clicks meant for the UI underneath.
        pointerEvents: 'none',
      }}
    >
      {jobs.map((job) => (
        <ScanJobCard key={job.job_id} job={job} />
      ))}
    </div>
  )
}
