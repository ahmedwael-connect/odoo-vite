/**
 * Icon kit — inline stroke SVGs on a 16px grid, colored by currentColor.
 * Replaces the unicode glyphs (✓ ✕ ⚠ …) that rendered inconsistently
 * across WebKitGTK. Always aria-hidden: labels live on the control.
 */

import type { ReactNode, SVGProps } from 'react'

export type IconName =
  | 'check'
  | 'x'
  | 'info'
  | 'alert'
  | 'more'
  | 'chevron-down'
  | 'chevron-up'
  | 'chevron-left'
  | 'chevron-right'
  | 'play'
  | 'stop'
  | 'plus'
  | 'minus'
  | 'trash'
  | 'refresh'
  | 'search'
  | 'folder'
  | 'copy'
  | 'external'
  | 'eye'
  | 'database'

const FILL = { fill: 'currentColor', stroke: 'none' } as const

const ICONS: Record<IconName, ReactNode> = {
  check: <path d="M3 8.5 6.5 12 13 4.5" />,
  x: <path d="M4 4l8 8M12 4l-8 8" />,
  info: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.4v3.6" />
      <circle cx="8" cy="5" r="0.9" {...FILL} />
    </>
  ),
  alert: (
    <>
      <path d="M8 2.5 14.5 13.5h-13z" />
      <path d="M8 6.6v3.1" />
      <circle cx="8" cy="11.6" r="0.9" {...FILL} />
    </>
  ),
  more: (
    <>
      <circle cx="8" cy="3.4" r="1.3" {...FILL} />
      <circle cx="8" cy="8" r="1.3" {...FILL} />
      <circle cx="8" cy="12.6" r="1.3" {...FILL} />
    </>
  ),
  'chevron-down': <path d="M4.5 6.25 8 9.75l3.5-3.5" />,
  'chevron-up': <path d="M4.5 9.75 8 6.25l3.5 3.5" />,
  'chevron-left': <path d="M9.75 4.5 6.25 8l3.5 3.5" />,
  'chevron-right': <path d="M6.25 4.5 9.75 8l-3.5 3.5" />,
  play: <path d="M5 3.4v9.2L12.4 8z" {...FILL} />,
  stop: <rect x="4.5" y="4.5" width="7" height="7" rx="1" {...FILL} />,
  plus: <path d="M8 3.5v9M3.5 8h9" />,
  minus: <path d="M3.5 8h9" />,
  trash: (
    <>
      <path d="M2.8 4.4h10.4M6.4 4.4V2.8h3.2v1.6" />
      <path d="M4.3 4.4l.6 8.4h6.2l.6-8.4" />
      <path d="M6.7 6.8v3.6M9.3 6.8v3.6" />
    </>
  ),
  refresh: (
    <>
      <path d="M13.5 8A5.5 5.5 0 1 1 8 2.5" />
      <path d="M6.4 1 10.2 2.5 6.4 4z" {...FILL} />
    </>
  ),
  search: (
    <>
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.3 10.3 14 14" />
    </>
  ),
  folder: (
    <path d="M2 4.6A1.6 1.6 0 0 1 3.6 3h2.6L7.6 4.8h4.8A1.6 1.6 0 0 1 14 6.4v5A1.6 1.6 0 0 1 12.4 13H3.6A1.6 1.6 0 0 1 2 11.4z" />
  ),
  copy: (
    <>
      <rect x="5.6" y="5.6" width="7.9" height="7.9" rx="1.5" />
      <path d="M10.4 5.6V4A1.5 1.5 0 0 0 8.9 2.5H4A1.5 1.5 0 0 0 2.5 4v4.9A1.5 1.5 0 0 0 4 10.4h1.6" />
    </>
  ),
  external: (
    <>
      <path d="M9.2 2.8H13.2V6.8" />
      <path d="M13.2 2.8 7.6 8.4" />
      <path d="M12 9.4v3.1a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3.1" />
    </>
  ),
  eye: (
    <>
      <path d="M1.6 8S4 3.8 8 3.8 14.4 8 14.4 8 12 12.2 8 12.2 1.6 8 1.6 8Z" />
      <circle cx="8" cy="8" r="2" />
    </>
  ),
  database: (
    <>
      <ellipse cx="8" cy="4" rx="5.4" ry="2.2" />
      <path d="M2.6 4v8c0 1.22 2.42 2.2 5.4 2.2s5.4-.98 5.4-2.2V4" />
      <path d="M2.6 8c0 1.22 2.42 2.2 5.4 2.2s5.4-.98 5.4-2.2" />
    </>
  ),
}

export function Icon({
  name,
  size = 16,
  className = '',
  ...rest
}: { name: IconName; size?: number } & Omit<SVGProps<SVGSVGElement>, 'name'>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      className={`icon ${className}`.trim()}
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...rest}
    >
      {ICONS[name]}
    </svg>
  )
}
