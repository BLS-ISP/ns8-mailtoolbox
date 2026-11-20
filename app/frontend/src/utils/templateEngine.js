const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/

export function formatDateForLocale(isoDate, lang = 'en') {
  if (!isoDate || typeof isoDate !== 'string') return ''
  const trimmed = isoDate.trim()
  const match = ISO_DATE_RE.exec(trimmed)
  if (!match) return trimmed
  const [, year, month, day] = match
  if (lang === 'de') {
    return `${day}.${month}.${year}`
  }
  return `${year}-${month}-${day}`
}

export function formatDateRange(startDate, endDate, lang = 'en', labels = {}) {
  const start = formatDateForLocale(startDate, lang)
  const end = formatDateForLocale(endDate, lang)

  if (start && end) {
    return start === end ? start : `${start} – ${end}`
  }
  if (start && !end) {
    const prefix = labels.fromPrefix || (lang === 'de' ? 'ab ' : 'from ')
    return `${prefix}${start}`
  }
  if (!start && end) {
    const prefix = labels.untilPrefix || (lang === 'de' ? 'bis ' : 'until ')
    return `${prefix}${end}`
  }
  return labels.openEnded || (lang === 'de' ? 'Unbefristet' : 'Open-ended')
}

function splitNameParts(displayName = '') {
  const clean = String(displayName || '').trim()
  if (!clean) return { given: '', sn: '' }
  const parts = clean.split(/\s+/)
  if (parts.length === 1) return { given: parts[0], sn: '' }
  return {
    given: parts[0],
    sn: parts.slice(1).join(' '),
  }
}

export function buildTemplateContext({
  mailbox,
  startDate = '',
  endDate = '',
  forwardEnabled = false,
  forwardAddress = '',
  lang = 'en',
}) {
  const displayName = mailbox?.display_name || mailbox?.user || ''
  const fallbackNames = splitNameParts(displayName)
  const givenName = mailbox?.given_name || fallbackNames.given || ''
  const sn = mailbox?.sn || fallbackNames.sn || ''
  const mail = mailbox?.mail || ''
  const cleanDeputy =
    forwardEnabled && typeof forwardAddress === 'string'
      ? forwardAddress.trim()
      : ''

  return {
    displayName,
    givenName,
    sn,
    mail,
    startDate: formatDateForLocale(startDate, lang),
    endDate: formatDateForLocale(endDate, lang),
    deputyEmail: cleanDeputy,
  }
}

function processConditionalBlocks(input, context) {
  let text = String(input ?? '').replace(/\r\n/g, '\n')

  // First handle standalone conditional blocks where {{#key}} and {{/key}} sit on their own lines
  const standaloneBlockRe =
    /(^|\n)[ \t]*\{\{#\s*([a-zA-Z0-9_]+)\s*\}\}[ \t]*\n([\s\S]*?)\n[ \t]*\{\{\/\s*\2\s*\}\}[ \t]*(?=\n|$)/g

  text = text.replace(standaloneBlockRe, (full, leadingNewline, key, inner) => {
    const val = context[key]
    const truthy = val !== undefined && val !== null && String(val).trim() !== ''
    if (!truthy) {
      return ''
    }
    return `${leadingNewline}${inner}`
  })

  // Then handle inline or remaining conditional blocks {{#key}}...{{/key}}
  const inlineBlockRe = /\{\{#\s*([a-zA-Z0-9_]+)\s*\}\}([\s\S]*?)\{\{\/\s*\1\s*\}\}/g
  let prev
  do {
    prev = text
    text = text.replace(inlineBlockRe, (_, key, inner) => {
      const val = context[key]
      const truthy = val !== undefined && val !== null && String(val).trim() !== ''
      return truthy ? inner : ''
    })
  } while (text !== prev)

  return text
}

export function renderTemplateString(rawText, context, { isSubject = false } = {}) {
  if (!rawText) return ''
  let output = processConditionalBlocks(rawText, context)

  output = output.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (match, key) => {
    if (Object.prototype.hasOwnProperty.call(context, key)) {
      return context[key] ?? ''
    }
    return match
  })

  if (isSubject) {
    return output.replace(/\s+/g, ' ').trim()
  }

  return output
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export function renderTemplate(template, params) {
  const effectiveLang = template?.lang || params.lang || 'en'
  const context = buildTemplateContext({ ...params, lang: effectiveLang })
  return {
    subject: renderTemplateString(template?.subject || '', context, {
      isSubject: true,
    }),
    body: renderTemplateString(template?.body || '', context, {
      isSubject: false,
    }),
    context,
  }
}

export const FALLBACK_TEMPLATES = [
  {
    id: 'default_en',
    name: 'Default Out-of-Office Notice (EN)',
    lang: 'en',
    is_default: true,
    subject: 'Out of Office: {{displayName}}',
    body:
      'Hello,\n\n' +
      'Thank you for your email. I am currently out of the office{{#startDate}} starting {{startDate}}{{/startDate}}{{#endDate}} through {{endDate}}{{/endDate}} with limited or no access to email.\n\n' +
      '{{#deputyEmail}}\n' +
      'Your message is being forwarded to {{deputyEmail}}. For urgent matters, please contact {{deputyEmail}} directly.\n' +
      '{{/deputyEmail}}\n\n' +
      'Best regards,\n' +
      '{{displayName}}',
  },
  {
    id: 'default_de',
    name: 'Standard Abwesenheitsnotiz (DE)',
    lang: 'de',
    is_default: false,
    subject: 'Abwesenheitsnotiz: {{displayName}}',
    body:
      'Guten Tag,\n\n' +
      'vielen Dank für Ihre Nachricht. Ich bin derzeit{{#startDate}} ab dem {{startDate}}{{/startDate}} außer Haus{{#endDate}} und voraussichtlich ab dem {{endDate}} (Folgetag) wieder erreichbar{{/endDate}}.\n\n' +
      '{{#deputyEmail}}\n' +
      'Ihre E-Mail wird während meiner Abwesenheit an {{deputyEmail}} weitergeleitet. In dringenden Fällen wenden Sie sich bitte direkt an {{deputyEmail}}.\n' +
      '{{/deputyEmail}}\n\n' +
      'Mit freundlichen Grüßen\n' +
      '{{displayName}}',
  },
]
