/**
 * Marketplace view (MP-3) — apps.odoo.com mirror + GitHub index.
 *
 * Toolbar (search + sort/series/price/category + Index…) over a stats chip
 * strip and a responsive card grid; cards open the detail dialog. Reads are
 * silent (offline falls back to the local cache with a warn Banner), the
 * view only mounts its queries while the tab is active (keep-mounted tab,
 * docs/patterns.md background-poll rule — the mirror fetch is network I/O).
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { getApi } from '../bridge'
import { ActionButton, Card, EmptyState, Select, Spinner, TextInput } from '../components/ui'
import { Banner, OverflowMenu } from '../components/widgets'
import { route, useRefreshEvent } from '../events'
import { useApp } from '../store'
import type { MarketplaceItem, MarketplaceSearchData, MarketplaceStats } from '../types'
import { IndexModuleDialog, MarketplaceDetailDialog } from '../dialogs/marketplace'

const ORDERS = [
  'Relevance',
  'Best Sellers',
  'Name',
  'Ratings',
  'Lowest Price',
  'Highest Price',
  'Downloads',
  'Purchases',
  'Newest',
] as const

const SERIES = ['19.0', '18.0', '17.0', '16.0'] as const
const PRICES = ['Free', 'Paid'] as const
/** Mirrors core.marketplace.PAGE_SIZE (apps.odoo.com renders 20 per page). */
const PAGE_SIZE = 20
const DEBOUNCE_MS = 350

function Cover({ item }: { item: MarketplaceItem }) {
  const initials = (item.title || '?').trim().slice(0, 2).toUpperCase()
  return (
    <span className="mk-cover" aria-hidden="true">
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

function Stars({ value, count }: { value: number; count: number }) {
  if (!count) return <span className="dim-label"> unrated</span>
  const full = Math.max(0, Math.min(5, Math.round(value || 0)))
  const label = `${value} out of 5 stars, ${count} rating(s)`
  return (
    <span className="mk-stars" aria-label={label} title={label}>
      {'★'.repeat(full)}
      {'☆'.repeat(5 - full)}
    </span>
  )
}

function CardTile({
  item,
  installed,
  onOpen,
}: {
  item: MarketplaceItem
  installed: boolean
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      className="mk-card"
      onClick={onOpen}
      aria-label={`Open ${item.title}`}
    >
      <span className="mk-card-head">
        <Cover item={item} />
        <span className="mk-card-titles">
          <span className="mk-title">{item.title}</span>
          <span className="mk-author">
            {item.author || 'Unknown publisher'}
            {item.official ? ' · official' : ''}
          </span>
        </span>
      </span>
      <span className="mk-summary">{item.summary || 'No summary.'}</span>
      <span className="mk-meta">
        <Stars value={item.rating_value ?? 0} count={item.rating_count ?? 0} />
        <span className="mk-price">{item.free ? 'FREE' : item.price || 'Paid'}</span>
        {item.featured && <span className="chip on">Featured</span>}
        {installed && <span className="chip on">Installed</span>}
        {(item.purchases ?? 0) > 0 && <span>{item.purchases} sales</span>}
      </span>
    </button>
  )
}

export default function Marketplace({ active = false }: { active?: boolean }) {
  const { currentId, setDialog } = useApp()
  const [query, setQuery] = useState('')
  const [order, setOrder] = useState<string>('Relevance')
  const [category, setCategory] = useState('')
  const [series, setSeries] = useState('')
  const [price, setPrice] = useState('')
  const [page, setPage] = useState(1)

  const [items, setItems] = useState<MarketplaceItem[]>([])
  const [total, setTotal] = useState(0)
  const [offline, setOffline] = useState(false)
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [stats, setStats] = useState<MarketplaceStats | null>(null)
  const [cats, setCats] = useState<string[]>([])
  const [installedTech, setInstalledTech] = useState<Set<string>>(new Set())

  const reqRef = useRef(0)

  const applySearch = (data: MarketplaceSearchData) => {
    setItems(data.items)
    setTotal(data.total)
    setOffline(data.offline)
    setNote(data.note)
  }

  const runSearch = useCallback(async () => {
    const req = ++reqRef.current
    setLoading(true)
    setError('')
    try {
      const res = await getApi().marketplace.search(query, order, category, series, price, '', page)
      if (req !== reqRef.current) return // a newer request superseded this one
      if (res.ok && res.data) {
        applySearch(res.data)
        // chips (cached/github/…) must reflect rows the search just upserted
        void getApi()
          .marketplace.stats()
          .then((s) => {
            if (req === reqRef.current && s.ok && s.data) setStats(s.data)
          })
      } else {
        setError(res.message)
      }
    } catch (err) {
      if (req === reqRef.current) setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (req === reqRef.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, order, category, series, price, page])

  // debounced search — only while the tab is active (network I/O gate)
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => void runSearch(), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [active, runSearch])

  // stats + categories on every activation (server-side TTL-cached, local SQL)
  useEffect(() => {
    if (!active) return
    void getApi()
      .marketplace.stats()
      .then((res) => {
        if (res.ok && res.data) setStats(res.data)
      })
    void getApi()
      .marketplace.categories()
      .then((res) => {
        if (res.ok && res.data) setCats(res.data)
      })
  }, [active])

  // installs + stats change behind our back (dialog flows emit refresh)
  useRefreshEvent(() => {
    void getApi()
      .marketplace.stats()
      .then((res) => {
        if (res.ok && res.data) setStats(res.data)
      })
    if (currentId) {
      void getApi()
        .marketplace.installed_in(currentId)
        .then((rows) =>
          setInstalledTech(
            new Set(rows.map((r) => String(r.tech_name ?? '')).filter(Boolean)),
          ),
        )
        .catch(() => undefined)
    }
  })

  // installed badges for the selected instance
  useEffect(() => {
    if (!currentId) {
      setInstalledTech(new Set())
      return
    }
    void getApi()
      .marketplace.installed_in(currentId)
      .then((rows) =>
        setInstalledTech(
          new Set(rows.map((r) => String(r.tech_name ?? '')).filter(Boolean)),
        ),
      )
      .catch(() => setInstalledTech(new Set()))
  }, [currentId])

  const openDetail = (item: MarketplaceItem) =>
    setDialog(
      <MarketplaceDetailDialog
        moduleId={item.id}
        onPickTech={(tech) => {
          setQuery(tech)
          setCategory('')
          setSeries('')
          setPrice('')
          setPage(1)
        }}
        onClose={() => setDialog(null)}
      />,
    )

  const syncFeatured = async () => {
    const res = await getApi().marketplace.sync_featured()
    route({ kind: 'message', payload: { text: res.message, level: res.ok ? 'info' : 'error' } })
    if (res.ok) {
      const s = await getApi().marketplace.stats()
      if (s.ok && s.data) setStats(s.data)
    }
  }

  const refreshStats = async () => {
    const s = await getApi().marketplace.stats()
    if (s.ok && s.data) setStats(s.data)
    const c = await getApi().marketplace.categories()
    if (c.ok && c.data) setCats(c.data)
  }

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const menu = [
    {
      label: 'Sync featured flags',
      onClick: () => void syncFeatured(),
    },
    {
      label: 'Refresh stats & categories',
      onClick: () => void refreshStats(),
    },
    {
      label: 'Index GitHub module…',
      onClick: () => setDialog(<IndexModuleDialog onClose={() => setDialog(null)} />),
    },
  ]

  const emptyText = offline
    ? 'Mirror unreachable and nothing cached yet — connect once to warm the cache.'
    : query || category || series || price
      ? 'No apps match — adjust the search or filters.'
      : 'Search the apps.odoo.com mirror, or index a GitHub repo from the toolbar.'

  return (
    <>
      <div className="mk-toolbar">
        <TextInput
          type="search"
          className="input mk-search"
          aria-label="Search apps"
          placeholder="Search apps…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setPage(1)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void runSearch()
          }}
        />
        <Select
          aria-label="Sort"
          value={order}
          onChange={(e) => {
            setOrder(e.target.value)
            setPage(1)
          }}
        >
          {ORDERS.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Series"
          value={series}
          onChange={(e) => {
            setSeries(e.target.value)
            setPage(1)
          }}
        >
          <option value="">All versions</option>
          {SERIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Price"
          value={price}
          onChange={(e) => {
            setPrice(e.target.value)
            setPage(1)
          }}
        >
          <option value="">Any price</option>
          {PRICES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Category"
          value={category}
          onChange={(e) => {
            setCategory(e.target.value)
            setPage(1)
          }}
        >
          <option value="">All categories</option>
          {cats.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <ActionButton
          onClick={() => setDialog(<IndexModuleDialog onClose={() => setDialog(null)} />)}
          title="Index an Odoo addon repository from GitHub into the local marketplace"
        >
          Index…
        </ActionButton>
        <OverflowMenu items={menu} label="Marketplace actions" />
      </div>

      {stats && (
        <div className="mk-stats" aria-label="Marketplace statistics">
          <span className="chip on">
            {stats.site_total ? `${stats.site_total.toLocaleString()} on apps.odoo.com` : 'mirror unreachable'}
          </span>
          <span className="chip">cached {stats.cached}</span>
          <span className="chip">GitHub {stats.github}</span>
          <span className="chip">featured {stats.featured}</span>
          <span className="chip">official {stats.official}</span>
          <span className="chip">installed {stats.installs}</span>
          <span className="chip">reviews {stats.reviews}</span>
        </div>
      )}

      {offline && (
        <Banner kind="warn" onClose={() => setOffline(false)}>
          {note || 'Mirror unreachable — showing cached results.'}
        </Banner>
      )}
      {error && (
        <Banner kind="error" onClose={() => setError('')}>
          {error}
        </Banner>
      )}

      <Card title={total ? `${total.toLocaleString()} apps` : 'Apps'}>
        {loading && <Spinner label="Searching…" />}
        <div className="mk-grid" aria-busy={loading}>
          {items.map((item) => (
            <CardTile
              key={item.id}
              item={item}
              installed={installedTech.has(item.tech || item.tech_name || '')}
              onOpen={() => openDetail(item)}
            />
          ))}
        </div>
        {items.length === 0 && !loading && <EmptyState text={emptyText} />}
        {total > PAGE_SIZE && (
          <div className="mk-pager">
            <ActionButton
              disabled={loading || page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </ActionButton>
            <span className="dim-label">
              Page {page} of {pages}
            </span>
            <ActionButton
              disabled={loading || page >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </ActionButton>
          </div>
        )}
      </Card>
    </>
  )
}
