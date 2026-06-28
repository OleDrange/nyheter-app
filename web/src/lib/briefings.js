import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { marked } from 'marked';

// Mappa generatoren skriver dagsfilene til. I produksjon (Docker) settes
// BRIEFING_DIR=/data/briefings eksplisitt mot det delte volumet. Uten env-var
// (lokal dev) leser vi repo-lokal briefings/-mappe — samme sted generatoren
// skriver lokalt (BRIEFING_DATA_DIR=.).
const DIR =
  process.env.BRIEFING_DIR ||
  fileURLToPath(new URL('../../../briefings', import.meta.url));

/**
 * Claude-output bruker «•»-punkter; normaliser til «- » så marked tolker dem
 * som liste. Returnerer HTML klar for `set:html`.
 */
export function renderMarkdown(md) {
  return marked.parse(String(md || '').replace(/^\s*•\s+/gm, '- '));
}

/** Alle datoer med en briefing, nyeste først (ISO-datoer sorteres som tekst). */
export async function listDates() {
  try {
    const files = await readdir(DIR);
    return files
      .filter((f) => f.endsWith('.json'))
      .map((f) => f.replace(/\.json$/, ''))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

/** Les én dagsfil. Returnerer null hvis den ikke finnes / er ugyldig. */
export async function getBriefing(date) {
  try {
    const raw = await readFile(path.join(DIR, `${date}.json`), 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** "2026-06-28" → "lørdag 28. juni 2026" (lokaltid-trygg, ingen UTC-skift). */
export function formatDateNo(dateStr, opts = { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) {
  const [y, m, d] = String(dateStr).split('-').map(Number);
  if (!y || !m || !d) return dateStr;
  return new Intl.DateTimeFormat('nb-NO', opts).format(new Date(y, m - 1, d));
}

/** "2026-06-28" → "lørdag" (kort ukedagsbruk i arkivet). */
export function weekdayNo(dateStr) {
  return formatDateNo(dateStr, { weekday: 'long' });
}

// Tickere i markedssnapshotet — rekkefølgen styrer også markedswidgeten/sparklines.
export const MARKET_KEYS = ['brent', 'sp500', 'osebx', 'btc', 'eurnok', 'usdnok'];

/**
 * Bygg per-ticker tallserier fra de siste briefingene (til sparklines).
 * Returnerer { brent: number[], sp500: number[], … } i stigende datorekkefølge.
 * `endDate` (valgfri) avgrenser vinduet til t.o.m. den datoen, så enkeltdag-siden
 * viser trenden fram til den dagen og ikke nyere data.
 */
export async function getMarketHistory({ limit = 30, endDate = null } = {}) {
  let dates = await listDates(); // nyeste først
  if (endDate) dates = dates.filter((d) => d <= endDate);
  dates = dates.slice(0, limit).reverse(); // eldste → nyeste

  const series = Object.fromEntries(MARKET_KEYS.map((k) => [k, []]));
  for (const d of dates) {
    const m = (await getBriefing(d))?.market;
    if (!m || m.error) continue;
    for (const k of MARKET_KEYS) {
      if (typeof m[k] === 'number' && Number.isFinite(m[k])) series[k].push(m[k]);
    }
  }
  return series;
}

// Ledende emoji (inkl. flagg som 🇳🇴) + mellomrom + resten av overskriften.
const HEADING_EMOJI =
  /^((?:\p{Extended_Pictographic}|\p{Regional_Indicator})[\p{Extended_Pictographic}\p{Regional_Indicator}️‍]*)\s+(.*)$/u;

/**
 * Del nyhetsbriefingen (`news_md`) i de syv «## »-seksjonene slik at hver kan
 * vises som eget kort. Returnerer [{ emoji, title, html }].
 */
export function splitNewsSections(md) {
  const text = String(md || '').trim();
  if (!text) return [];
  return text
    .split(/^##\s+/m)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((part) => {
      const nl = part.indexOf('\n');
      const heading = (nl === -1 ? part : part.slice(0, nl)).trim();
      const body = nl === -1 ? '' : part.slice(nl + 1).trim();
      const m = heading.match(HEADING_EMOJI);
      return {
        emoji: m ? m[1] : '',
        title: m ? m[2] : heading,
        html: renderMarkdown(body),
      };
    });
}

/**
 * Del forskningsbriefingen (`research_md`) per studie. Hver studie er
 * `## [tittel](url)` etterfulgt av Hva/Resultat/Relevans.
 * Returnerer [{ title, url, html }].
 */
export function splitResearch(md) {
  const text = String(md || '').trim();
  if (!text) return [];
  return text
    .split(/^##\s+/m)
    .map((s) => s.replace(/\n*-{3,}\s*$/, '').trim()) // dropp avsluttende «---»
    .filter(Boolean)
    .map((part) => {
      const nl = part.indexOf('\n');
      const heading = (nl === -1 ? part : part.slice(0, nl)).trim();
      const body = nl === -1 ? '' : part.slice(nl + 1).trim();
      const link = heading.match(/^\[(.*?)\]\((.*?)\)\s*$/);
      return {
        title: link ? link[1] : heading,
        url: link ? link[2] : null,
        html: renderMarkdown(body),
      };
    });
}
