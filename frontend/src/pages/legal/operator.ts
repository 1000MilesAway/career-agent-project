/** Who runs the service, as the legal pages name it.
 *
 * One place, because three documents quote it and a mismatch between them is
 * itself a defect in a privacy policy. A null renders as «уточняется» rather
 * than an invented value: a made-up INN is worse than a visibly missing one.
 */

export const OPERATOR = {
  /** Full name of the individual, or the IP / self-employed status with it. */
  name: null as string | null,
  inn: null as string | null,
  /** A mailbox someone reads: requests about personal data arrive here. */
  email: null as string | null,
}

export const SITE = 'https://career-helper.site'

/** The edition date every document carries; bump it with any change of text. */
export const EDITION = '2 октября 2026 г.'
