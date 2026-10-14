/** Russian relative time for a card's «обновлено N назад».
 *
 * Minutes, hours, days, weeks, months, years — one ladder, the form the
 * job trackers settled on. `null` for a row the payload carries no date
 * for: an empty label would read as "обновлено undefined назад".
 */

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

export function relTime(iso: string | null | undefined, now: Date = new Date()): string | null {
  if (!iso) return null
  const seconds = Math.floor((now.getTime() - new Date(iso).getTime()) / 1000)
  // A future stamp is clock skew between server and client, not a forecast.
  if (seconds < 45) return 'только что'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} ${plural(minutes, 'минуту', 'минуты', 'минут')} назад`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ${plural(hours, 'час', 'часа', 'часов')} назад`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} ${plural(days, 'день', 'дня', 'дней')} назад`
  const weeks = Math.floor(days / 7)
  if (weeks < 5) return `${weeks} ${plural(weeks, 'неделю', 'недели', 'недель')} назад`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months} ${plural(months, 'месяц', 'месяца', 'месяцев')} назад`
  const years = Math.floor(days / 365)
  return `${years} ${plural(years, 'год', 'года', 'лет')} назад`
}