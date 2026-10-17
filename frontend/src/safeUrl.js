// Links come from scraped job postings: only let http(s) and mailto through (a javascript: link would run code)
export function safeUrl(url) {
  try {
    return ['http:', 'https:', 'mailto:'].includes(new URL(url).protocol) ? url : undefined
  } catch {
    return undefined
  }
}
