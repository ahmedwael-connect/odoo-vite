/**
 * Marketplace dialogs (MP-3): module detail (Overview / Screenshots /
 * Versions + dependencies / Reviews) and the GitHub index form.
 *
 * Descriptions arrive already sanitized by core.marketplace.sanitize_html
 * (allow-list), so the HTML is rendered as-is. Local reviews live in the
 * registry DB and are edited through marketplace.add_review/delete_review.
 */

import { useEffect, useState } from 'react'
import { getApi } from '../bridge'
import { Modal, useConfirm, useProgressRun } from '../components/dialog'
import { Banner } from '../components/widgets'
import {
  ActionButton,
  DimText,
  Field,
  SectionHeader,
  Select,
  Spinner,
  TextInput,
} from '../components/ui'
import { route } from '../events'
import { useApp } from '../store'
import type { MarketplaceDetail, MarketplaceItem, MarketplaceReview } from '../types'

// ------------------------------------------------------------------ helpers

function Stars({ value, count }: { value: number; count: number }) {
  if (!count) return <span className="dim-label">No ratings yet</span>
  const full = Math.max(0, Math.min(5, Math.round(value || 0)))
  const label = `${value} out of 5 stars${count > 1 ? `, ${count} rating(s)` : ''}`
  return (
    <span className="mk-stars" aria-label={label} title={label}>
      {'★'.repeat(full)}
      {'☆'.repeat(5 - full)}
    </span>
  )
}

function Cover({ item, large }: { item: Pick<MarketplaceItem, 'title' | 'cover_url'>; large?: boolean }) {
  const initials = (item.title || '?').trim().slice(0, 2).toUpperCase()
  return (
    <span className={large ? 'mk-cover mk-cover-lg' : 'mk-cover'} aria-hidden="true">
      {initials}
      {item.cover_url ? (
        <img
          className="mk-cover-img"
          src={item.cover_url}
          alt=""
          loading="lazy"
          onError={(e) => {
            e.currentTarget.style.opacity = '0'
          }}
        />
      ) : null}
    </span>
  )
}

const AVAIL_LABEL: Record<string, string> = {
  odoo_online: 'Odoo Online',
  odoo_sh: 'Odoo.sh',
  on_premise: 'On-premise',
}

function ReviewBlock({
  review,
  onDelete,
}: {
  review: MarketplaceReview
  onDelete?: () => void
}) {
  return (
    <div className="mk-review">
      <div className="mk-review-head">
        <Stars value={review.rating} count={1} />
        {review.title && <strong className="mk-review-title">{review.title}</strong>}
        <span className="dim-label">
          {review.author || 'Anonymous'}
          {review.date || review.created_at ? ` · ${review.date ?? review.created_at}` : ''}
          {review.source === 'store' ? ' · apps.odoo.com' : ''}
        </span>
        <span className="spacer" />
        {onDelete && (
          <ActionButton size="sm" danger onClick={onDelete}>
            Delete
          </ActionButton>
        )}
      </div>
      {review.body && <p className="mk-review-body">{review.body}</p>}
    </div>
  )
}

// ---------------------------------------------------------- detail dialog

export function MarketplaceDetailDialog({
  moduleId,
  onPickTech,
  onClose,
}: {
  moduleId: string
  /** Jump the marketplace view to a dependency's search results. */
  onPickTech: (tech: string) => void
  onClose: () => void
}) {
  const api = getApi()
  const { current, currentId, setBusy } = useApp()
  const confirm = useConfirm()
  const runProgress = useProgressRun()
  const [detail, setDetail] = useState<MarketplaceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [rating, setRating] = useState('5')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [author, setAuthor] = useState('')

  useEffect(() => {
    let live = true
    setLoading(true)
    setError('')
    api.marketplace
      .detail(moduleId)
      .then((res) => {
        if (!live) return
        if (res.ok && res.data) setDetail(res.data)
        else setError(res.message)
      })
      .catch((err: unknown) => live && setError(String(err)))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moduleId, reload])

  const addReview = async () => {
    setBusy(true)
    try {
      const res = await api.marketplace.add_review(moduleId, Number(rating), title, body, author)
      route({ kind: 'message', payload: { text: res.message, level: res.ok ? 'info' : 'error' } })
      if (res.ok) {
        setTitle('')
        setBody('')
        setReload((n) => n + 1)
      }
    } finally {
      setBusy(false)
    }
  }

  const deleteReview = async (review: MarketplaceReview) => {
    if (review.id == null) return
    const ok = await confirm({
      heading: 'Delete your review?',
      body: review.title || 'This review',
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      const res = await api.marketplace.delete_review(review.id)
      route({ kind: 'message', payload: { text: res.message, level: res.ok ? 'info' : 'error' } })
      if (res.ok) setReload((n) => n + 1)
    } finally {
      setBusy(false)
    }
  }

  const reviews = [...(detail?.reviews ?? []), ...(detail?.local_reviews ?? [])]

  // --- install flow (P4): browser download + Downloads watch, or a zip
  // the user already has. Extraction is validated by core; the -i step is
  // an explicit confirmation because it restarts the instance's DB.
  const afterImport = async (path: string) => {
    if (!currentId || !detail) return
    const unpack = await confirm({
      heading: `Install into ${current?.name ?? 'this instance'}?`,
      body: `Unpack ${detail.title}\n${path}`,
      confirmLabel: 'Unpack',
    })
    if (!unpack) return
    setBusy(true)
    try {
      const res = await api.marketplace.install_from_zip(
        path,
        currentId,
        moduleId,
      )
      route({ kind: 'message', payload: { text: res.message, level: res.ok ? 'info' : 'error' } })
      if (!res.ok) {
        setError(res.message)
        return
      }
      const primary = detail.tech_name || detail.tech
      const init = await confirm({
        heading: 'Install into the database now?',
        body: `Runs odoo-bin -i ${primary} on ${current?.name ?? 'the instance'}.`,
        confirmLabel: 'Install',
      })
      if (init && primary) {
        const r = await runProgress(`Installing ${primary}`, (opId) =>
          api.modules.install(currentId, [primary], opId),
        )
        route({
          kind: 'message',
          payload: { text: r.message, level: r.ok ? 'info' : 'error' },
        })
      }
      onClose()
    } finally {
      setBusy(false)
    }
  }

  const openDownloadPage = async () => {
    if (!detail?.detail_url) return
    const res = await api.marketplace.open_url(detail.detail_url)
    route({ kind: 'message', payload: { text: res.message, level: res.ok ? 'info' : 'error' } })
  }

  const findDownloadedZip = async () => {
    if (!detail || !currentId) return
    const tech = detail.tech_name || detail.tech
    const res = await runProgress(`Waiting for ${tech}.zip`, (opId) =>
      api.marketplace.watch_download(moduleId, tech, opId),
    )
    if (!res.ok || !res.data?.path) {
      route({ kind: 'message', payload: { text: res.message, level: 'error' } })
      return
    }
    await afterImport(res.data.path)
  }

  const installFromFile = async () => {
    const picked = await api.app.pick_file('Add-on zip', 'open', 'zip')
    if (!picked.ok || !picked.path) return
    await afterImport(picked.path)
  }

  return (
    <Modal
      title={detail?.title ?? 'App'}
      onClose={onClose}
      width={680}
      footer={
        <>
          {detail?.detail_url && (
            <a
              className="btn action"
              href={detail.detail_url}
              target="_blank"
              rel="noreferrer noopener"
              onClick={() => void api.marketplace.record_download(moduleId)}
            >
              Open on apps.odoo.com
            </a>
          )}
          <ActionButton onClick={onClose}>Close</ActionButton>
        </>
      }
    >
      {loading && <Spinner label="Loading app…" />}
      {error && (
        <Banner kind="error" onClose={() => setError('')}>
          {error}
        </Banner>
      )}
      {detail && !loading && (
        <>
          <div className="mk-detail-head">
            <Cover item={detail} large />
            <div className="mk-detail-titles">
              <h4 className="mk-title">{detail.title}</h4>
              <span className="dim-label">
                {detail.author || 'Unknown publisher'}
                {detail.official ? ' · Odoo S.A. (official)' : ''}
              </span>
              <div className="mk-meta">
                <Stars value={detail.rating_value ?? 0} count={detail.rating_count ?? 0} />
                <span className="mk-price">{detail.free ? 'FREE' : detail.price || 'Paid'}</span>
                {detail.featured && <span className="chip on">Featured</span>}
                {(detail.purchases ?? 0) > 0 && <span>{detail.purchases} sales</span>}
              </div>
            </div>
          </div>

          {currentId ? (
            <div className="btn-row mk-actions" role="group" aria-label="Install">
              <ActionButton size="sm" onClick={() => void openDownloadPage()}>
                Download…
              </ActionButton>
              <ActionButton
                size="sm"
                title="Watch your Downloads folder for the finished zip"
                onClick={() => void findDownloadedZip()}
              >
                Find downloaded zip…
              </ActionButton>
              <ActionButton
                size="sm"
                primary
                title="Pick an add-on zip you already have"
                onClick={() => void installFromFile()}
              >
                Install from file…
              </ActionButton>
            </div>
          ) : (
            <DimText>Select an instance to install add-ons.</DimText>
          )}

          <SectionHeader text="Overview" />
          <div
            className="mk-desc"
            dangerouslySetInnerHTML={{
              __html: detail.description_html || '<p>No description provided.</p>',
            }}
          />

          {detail.screenshots.length > 0 && (
            <>
              <SectionHeader text="Screenshots" />
              <div className="mk-shot-grid">
                {detail.screenshots.map((src, i) => (
                  <img key={`${src}#${i}`} className="mk-shot" src={src} alt="" loading="lazy" />
                ))}
              </div>
            </>
          )}

          <SectionHeader text="Versions & dependencies" />
          <div className="mk-dep-list">
            {detail.versions.map((v) => (
              <span key={v} className="chip on">
                {v}
              </span>
            ))}
          </div>
          <div className="mk-avail">
            {Object.entries(detail.available ?? {}).map(([key, ok]) => (
              <span key={key} className={ok ? 'mk-avail-yes' : 'mk-avail-no'}>
                {ok ? '✓' : '✕'} {AVAIL_LABEL[key] ?? key}
              </span>
            ))}
          </div>
          {detail.depends.length > 0 && (
            <div className="mk-dep-list" aria-label="Dependencies">
              {detail.depends.map((d) => (
                <button
                  key={d.tech || d.label}
                  type="button"
                  className="chip mk-dep"
                  title={`Search ${d.tech || d.label}`}
                  onClick={() => {
                    onPickTech(d.tech || d.label)
                    onClose()
                  }}
                >
                  {d.label || d.tech}
                </button>
              ))}
            </div>
          )}

          <SectionHeader text={`Reviews (${reviews.length})`} />
          {reviews.length === 0 && <DimText>No reviews yet — be the first.</DimText>}
          {reviews.map((r, i) => (
            <ReviewBlock
              key={r.id ?? r.remote_id ?? `r${i}`}
              review={r}
              onDelete={r.id != null && r.source === 'local' ? () => void deleteReview(r) : undefined}
            />
          ))}

          <SectionHeader text="Write a review (saved locally)" />
          <div className="mk-review-form">
            <Field label="Rating">
              <Select value={rating} onChange={(e) => setRating(e.target.value)} aria-label="Rating">
                {['5', '4', '3', '2', '1'].map((n) => (
                  <option key={n} value={n}>
                    {n} / 5
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Title">
              <TextInput
                value={title}
                maxLength={200}
                placeholder="Sums it up"
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            <label className="field">
              <span className="field-label">Review</span>
              <textarea
                className="input textarea"
                rows={3}
                maxLength={4000}
                value={body}
                placeholder="What worked, what didn't…"
                onChange={(e) => setBody(e.target.value)}
              />
            </label>
            <Field label="Your name (optional)">
              <TextInput
                value={author}
                maxLength={80}
                placeholder="Your name (optional)"
                onChange={(e) => setAuthor(e.target.value)}
              />
            </Field>
            <div className="btn-row">
              <ActionButton
                primary
                disabled={!title.trim() && !body.trim()}
                onClick={() => void addReview()}
              >
                Save review
              </ActionButton>
            </div>
          </div>

          <SectionHeader text="Details" />
          <div className="kv">
            <span className="dim-label">Technical name</span>
            <span className="mono">{detail.tech_name || detail.tech}</span>
            <span className="dim-label">Series</span>
            <span>{detail.series}</span>
            <span className="dim-label">License</span>
            <span>{detail.license || '—'}</span>
            {detail.repo_url && (
              <>
                <span className="dim-label">Repository</span>
                <a href={detail.repo_url} target="_blank" rel="noreferrer noopener" className="mono">
                  {detail.repo_url}
                </a>
              </>
            )}
          </div>
        </>
      )}
    </Modal>
  )
}

// --------------------------------------------------------- index dialog

export function IndexModuleDialog({ onClose }: { onClose: () => void }) {
  const api = getApi()
  const runProgress = useProgressRun()
  const [repo, setRepo] = useState('')
  const [branch, setBranch] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const source = repo.trim()
    if (!source) {
      setError('Enter a repository as owner/repo (e.g. OCA/web-responsive)')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await runProgress(`Indexing ${source}`, (opId) =>
        api.marketplace.index(source, branch.trim(), opId),
      )
      route({ kind: 'message', payload: { text: res.message, level: res.ok ? 'info' : 'error' } })
      if (res.ok) onClose()
      else setError(res.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Index a GitHub module"
      onClose={onClose}
      width={480}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton primary loading={busy} onClick={() => void submit()}>
            Index
          </ActionButton>
        </>
      }
    >
      <DimText>
        Shallow-clones the repository (or refreshes it) and indexes every Odoo addon manifest it
        contains into the local marketplace.
      </DimText>
      <Field label="Repository (owner/repo)">
        <TextInput
          autoFocus
          data-autofocus
          value={repo}
          placeholder="OCA/web-responsive"
          onChange={(e) => setRepo(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submit()
          }}
        />
      </Field>
      <Field label="Branch (optional — default branch otherwise)">
        <TextInput value={branch} placeholder="16.0" onChange={(e) => setBranch(e.target.value)} />
      </Field>
      {error && <p className="error">{error}</p>}
    </Modal>
  )
}
