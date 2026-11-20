export const TEMPLATE_PLACEHOLDERS = [
  { token: "{{displayName}}", key: "displayName" },
  { token: "{{givenName}}", key: "givenName" },
  { token: "{{sn}}", key: "sn" },
  { token: "{{mail}}", key: "mail" },
  { token: "{{startDate}}", key: "startDate" },
  { token: "{{endDate}}", key: "endDate" },
  { token: "{{deputyEmail}}", key: "deputyEmail" },
  {
    token: "{{#endDate}}... {{endDate}}{{/endDate}}",
    key: "conditionalEndDate",
  },
  {
    token: "{{#deputyEmail}}... {{deputyEmail}}{{/deputyEmail}}",
    key: "conditionalDeputyEmail",
  },
];

export function formatTemplateDate(isoDate, lang = "en") {
  if (!isoDate || typeof isoDate !== "string") {
    return "";
  }
  const trimmed = isoDate.trim();
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed);
  if (!match) {
    return trimmed;
  }
  if (lang && lang.toLowerCase().startsWith("de")) {
    return `${match[3]}.${match[2]}.${match[1]}`;
  }
  return trimmed;
}

export function renderVacationTemplate(
  rawText,
  {
    mailbox = {},
    startDate = "",
    endDate = "",
    forwardEnabled = false,
    forwardAddress = "",
    lang = "en",
  } = {}
) {
  if (!rawText || typeof rawText !== "string") {
    return "";
  }

  const formattedStart = formatTemplateDate(startDate, lang);
  const formattedEnd = formatTemplateDate(endDate, lang);
  const deputyEmail =
    forwardEnabled && forwardAddress ? forwardAddress.trim() : "";

  const context = {
    displayName: mailbox.display_name || mailbox.user || "",
    givenName: mailbox.given_name || "",
    sn: mailbox.sn || "",
    mail: mailbox.mail || "",
    department: mailbox.department || "",
    title: mailbox.title || "",
    startDate: formattedStart,
    endDate: formattedEnd,
    deputyEmail,
  };

  // Process conditional sections {{#key}}...{{/key}}
  let rendered = rawText.replace(
    /\{\{#([a-zA-Z0-9_]+)\}\}([\s\S]*?)\{\{\/\1\}\}/g,
    (_, key, inner) => {
      const val = context[key];
      if (val && String(val).trim() !== "") {
        return inner;
      }
      return "";
    }
  );

  // Process scalar variables {{key}}
  rendered = rendered.replace(/\{\{([a-zA-Z0-9_]+)\}\}/g, (full, key) => {
    if (Object.prototype.hasOwnProperty.call(context, key)) {
      return context[key];
    }
    return full;
  });

  // Collapse 3+ consecutive newlines caused by omitted conditional blocks
  rendered = rendered.replace(/\n{3,}/g, "\n\n").trim();

  return rendered;
}
