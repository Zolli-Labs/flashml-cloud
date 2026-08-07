/** "1 person", "2 people" — a count and its noun, agreeing.
 *
 * Takes the plural explicitly rather than appending "s", because the first
 * place this was needed ("person" -> "people") is exactly the case an
 * append-s helper gets wrong, and a helper that is wrong for the reason you
 * reached for it is worse than none.
 */
export function countOf(n: number, singular: string, plural?: string): string {
  return `${n} ${n === 1 ? singular : (plural ?? `${singular}s`)}`;
}
