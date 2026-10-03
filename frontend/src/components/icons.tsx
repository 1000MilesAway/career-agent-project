/** A handful of line icons, inline.
 *
 * Inline SVG rather than an icon package: the cabinet needs six glyphs, and a
 * dependency for six paths is weight with nothing to do. All share one grid
 * (20px, 1.6 stroke) so they sit on the same baseline in the sidebar. */

import type { ReactNode } from 'react'

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export const ResumeIcon = () => (
  <Icon>
    <path d="M5 2.5h7l3.5 3.5v11.5h-10.5z" />
    <path d="M12 2.5V6h3.5M7.5 10h5M7.5 13h5" />
  </Icon>
)

export const SearchIcon = () => (
  <Icon>
    <circle cx="9" cy="9" r="5.5" />
    <path d="M13 13l4 4" />
  </Icon>
)

export const StarIcon = ({ filled = false }: { filled?: boolean }) => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 20 20"
    fill={filled ? 'currentColor' : 'none'}
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M10 2.8l2.2 4.6 5 .6-3.7 3.4 1 5-4.5-2.5-4.5 2.5 1-5L2.8 8l5-.6z" />
  </svg>
)

export const ChatIcon = () => (
  <Icon>
    <path d="M3 5.5A2.5 2.5 0 0 1 5.5 3h9A2.5 2.5 0 0 1 17 5.5v6a2.5 2.5 0 0 1-2.5 2.5H9l-4 3v-3h0.5A2.5 2.5 0 0 1 3 11.5z" />
  </Icon>
)

export const LogoutIcon = () => (
  <Icon>
    <path d="M8 3.5H4.5v13H8M12.5 6.5L16 10l-3.5 3.5M16 10H8" />
  </Icon>
)

/** A plain cog: a ring with eight square teeth. */
export const SettingsIcon = () => (
  <Icon>
    <circle cx="10" cy="10" r="5" />
    <circle cx="10" cy="10" r="1.8" />
    <path
      strokeWidth="2.6"
      strokeLinecap="butt"
      d="M15 10h2.5M13.5 13.5l1.8 1.8M10 15v2.5M6.5 13.5l-1.8 1.8M5 10H2.5M6.5 6.5L4.7 4.7M10 5V2.5M13.5 6.5l1.8-1.8"
    />
  </Icon>
)

export const SunIcon = () => (
  <Icon>
    <circle cx="10" cy="10" r="3.2" />
    <path d="M10 2.5v1.8M10 15.7v1.8M2.5 10h1.8M15.7 10h1.8M4.7 4.7l1.3 1.3M14 14l1.3 1.3M4.7 15.3 6 14M14 6l1.3-1.3" />
  </Icon>
)

export const MoonIcon = () => (
  <Icon>
    <path d="M16 12.2A6.5 6.5 0 0 1 7.8 4a6.5 6.5 0 1 0 8.2 8.2z" />
  </Icon>
)

/** «Как в системе»: a screen. */
export const AutoThemeIcon = () => (
  <Icon>
    <rect x="3" y="4" width="14" height="10" rx="1.5" />
    <path d="M7.5 17h5M10 14v3" />
  </Icon>
)

export const InfoIcon = () => (
  <Icon>
    <circle cx="10" cy="10" r="7.5" />
    <path d="M10 9v5M10 6.2v.1" />
  </Icon>
)

export const CloseIcon = () => (
  <Icon>
    <path d="M5 5l10 10M15 5L5 15" />
  </Icon>
)

export const ExternalIcon = () => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <path d="M8 4H4v12h12v-4M11 3h6v6M17 3l-8 8" />
  </svg>
)

/** A paper plane, for the Telegram buttons — the glyph people recognise it by. */
export const TelegramIcon = () => (
  <Icon>
    <path d="M17.5 3 2.5 9.2l5.3 1.9L15 5.5l-5.3 6.6 5.8 4.4z" />
    <path d="M7.8 11.1v5.4l2.5-2.9" />
  </Icon>
)
