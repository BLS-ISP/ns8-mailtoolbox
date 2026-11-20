import enMessages from "../../public/i18n/en/translation.json";
import deMessages from "../../public/i18n/de/translation.json";

export const bundledMessages = {
  en: enMessages,
  de: deMessages,
};

export async function loadLanguage(lang) {
  const normalized = (lang || "en").substring(0, 2).toLowerCase();
  if (bundledMessages[normalized]) {
    return { default: bundledMessages[normalized] };
  }
  try {
    const messages = await import(
      /* webpackChunkName: "lang-[request]" */ `../../public/i18n/${normalized}/translation.json`
    );
    return messages;
  } catch (error) {
    console.warn(
      `Cannot import ${normalized} language messages, falling back to English.`,
      error
    );
    return { default: enMessages };
  }
}
