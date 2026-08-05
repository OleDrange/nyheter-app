import { readdir, readFile, stat } from 'node:fs/promises';
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

/**
 * Signatur over hele arkivet — endrer seg når en dagsfil legges til ELLER skrives om.
 * Brukes som cache-nøkkel av biblioteket (`library.js`). Filnavn alene holder ikke:
 * begge generatorene skriver inn i samme dagsfil, så dagens fil endres etter at den
 * først er lest.
 */
export async function briefingStamp() {
  try {
    const files = (await readdir(DIR)).filter((f) => f.endsWith('.json')).sort();
    const stats = await Promise.all(files.map((f) => stat(path.join(DIR, f))));
    return `${files.length}:${stats.reduce((sum, s) => sum + s.mtimeMs, 0)}`;
  } catch {
    return 'none';
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

// Forskningssiden bor på eget subdomene (samme app, host-rutet i middleware.js).
export const FORSKNING_URL = 'https://forskning.modr.no';

// Kategoriene i forskningsbriefingen — rekkefølge og emoji brukes av visningen.
// `medisin` ligger sist og er legacy: generatoren produserer den ikke lenger, men arkiverte
// briefinger har den, og tomme grupper skjules uansett i ResearchList.
export const RESEARCH_CATEGORIES = [
  { id: 'longevity', label: 'Longevity', emoji: '🧬' },
  { id: 'trening', label: 'Trening', emoji: '🏋️' },
  { id: 'kosthold', label: 'Kosthold', emoji: '🥗' },
  { id: 'sovn_stress', label: 'Søvn og stress', emoji: '😴' },
  { id: 'medisin', label: 'Medisin', emoji: '🩺' },
];

// Tickere i markedssnapshotet — rekkefølgen styrer også markedswidgeten.
export const MARKET_KEYS = ['brent', 'sp500', 'osebx', 'btc', 'eth', 'nordnet'];

/**
 * Bygg per-ticker dagsserier fra de siste briefingene (til mini-grafene).
 * Returnerer { brent: [{ date, value }], … } i stigende datorekkefølge, ett punkt
 * per dag. `endDate` (valgfri) avgrenser vinduet til t.o.m. den datoen, så
 * enkeltdag-siden viser trenden fram til den dagen og ikke nyere data.
 */
export async function getMarketHistory({ limit = 8, endDate = null } = {}) {
  let dates = await listDates(); // nyeste først
  if (endDate) dates = dates.filter((d) => d <= endDate);
  dates = dates.slice(0, limit).reverse(); // eldste → nyeste

  const series = Object.fromEntries(MARKET_KEYS.map((k) => [k, []]));
  for (const d of dates) {
    const m = (await getBriefing(d))?.market;
    if (!m || m.error) continue;
    for (const k of MARKET_KEYS) {
      if (typeof m[k] === 'number' && Number.isFinite(m[k])) series[k].push({ date: d, value: m[k] });
    }
  }
  return series;
}

// Ledende emoji (inkl. flagg som 🇳🇴) + mellomrom + resten av overskriften.
const HEADING_EMOJI =
  /^((?:\p{Extended_Pictographic}|\p{Regional_Indicator})[\p{Extended_Pictographic}\p{Regional_Indicator}️‍]*)\s+(.*)$/u;

// Ett punkt starter med «•», «-» eller «*»; etterfølgende linjer hører til samme punkt.
const BULLET_RE = /^\s*[•\-*]\s+/;

/** Markdown → ren tekst (lenketekst beholdes, utheving fjernes) — til søk og ID. */
function plainText(md) {
  return String(md || '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// Kortere enn dette er ikke en nyhet, men en plassholder («Ingen viktige hendelser.»).
const MIN_POINT_CHARS = 40;

// Overskriften i et punkt er lenketeksten, og den kan bli et helt avsnitt. Kort den ned
// på ordgrense til bibliotek- og repetisjonskortene; hele teksten ligger i snapshotet.
const TITLE_MAX = 160;

function shortTitle(s) {
  const t = String(s || '').trim();
  if (t.length <= TITLE_MAX) return t;
  const cut = t.slice(0, TITLE_MAX);
  return `${cut.slice(0, cut.lastIndexOf(' ')) || cut}…`;
}

/** Del en seksjonskropp i enkeltpunkter (rå markdown per punkt). */
function splitBullets(body) {
  const out = [];
  let cur = null;
  for (const line of String(body || '').split('\n')) {
    if (BULLET_RE.test(line)) {
      if (cur) out.push(cur);
      cur = line.replace(BULLET_RE, '').trim();
    } else if (cur && line.trim()) {
      cur = `${cur} ${line.trim()}`;
    } else if (cur) {
      out.push(cur);
      cur = null;
    }
  }
  if (cur) out.push(cur);
  return out.filter(Boolean);
}

/**
 * Del nyhetsbriefingen (`news_md`) i de syv «## »-seksjonene slik at hver kan
 * vises som eget kort. Returnerer [{ emoji, title, html, points }].
 *
 * `points` er seksjonens enkeltpunkter — den enheten leseren faktisk forholder seg til,
 * og derfor det som kan favorittmerkes og havner i biblioteket. Hvert punkt har en
 * `index` som er GLOBAL for hele briefingen (ikke per seksjon), slik at pin-knappen kan
 * identifisere punktet med `{ date, index }` på samme måte som gåter og quiz. Punktet
 * åpner nesten alltid med en markdown-lenke: lenketeksten er overskriften og URL-en
 * kilden. `html` på seksjonen beholdes som fallback for punktløse seksjoner.
 */
export function splitNewsSections(md) {
  const text = String(md || '').trim();
  if (!text) return [];
  let n = 0;
  return text
    .split(/^##\s+/m)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((part) => {
      const nl = part.indexOf('\n');
      const heading = (nl === -1 ? part : part.slice(0, nl)).trim();
      const body = nl === -1 ? '' : part.slice(nl + 1).trim();
      const m = heading.match(HEADING_EMOJI);
      const section = m ? m[2] : heading;

      const points = splitBullets(body).map((raw) => {
        // Punktet åpner som regel med lenken, og da ER lenketeksten overskriften. Ligger
        // lenken lenger inn i setningen (vanlig i eldre briefinger), brukes den likevel
        // som kilde-URL, men første setning som overskrift.
        const lead = raw.match(/^\s*\[([^\]]+)\]\(([^)\s]+)\)/);
        const any = lead || raw.match(/\[[^\]]+\]\(([^)\s]+)\)/);
        const plain = plainText(raw);
        return {
          index: n++,
          section,
          title: shortTitle(lead ? lead[1] : plain.split(/(?<=[.!?])\s/)[0] || plain),
          url: lead ? lead[2] : any ? any[1] : null,
          text: plain,
          html: marked.parseInline(raw),
          // Tomme seksjoner skrives som «Ingen viktige hendelser.» — en plassholder, ikke
          // en nyhet. Den skal verken kunne favorittmerkes eller havne i biblioteket.
          pinnable: plain.length >= MIN_POINT_CHARS,
        };
      });

      return { emoji: m ? m[1] : '', title: section, html: renderMarkdown(body), points };
    });
}

/** Alle nyhetspunkter i en briefing, flatet ut (rekkefølgen bærer `index`). */
export function newsPoints(md) {
  return splitNewsSections(md).flatMap((s) => s.points);
}

// Merkede deler i en studie: «**Hva som ble gjort:** …» fram til neste «**…:**».
const STUDY_PART_RE = /\*\*\s*(.+?)\s*:\*\*\s*([\s\S]*?)(?=\n\s*\*\*|$)/g;

/**
 * Claude skriver kategorien som visningsnavn («Søvn og stress»), mens id-en i
 * `research_items` er en slug (`sovn_stress`). Godta begge former.
 */
function normalizeCategory(raw) {
  const val = String(raw || '').replace(/[*_]/g, ' ').trim().toLowerCase();
  if (!val) return null;
  const hit = RESEARCH_CATEGORIES.find(
    (c) => c.label.toLowerCase() === val || c.id.replace(/_/g, ' ') === val,
  );
  return hit ? hit.id : val;
}

/**
 * Del forskningsbriefingen (`research_md`) per studie. Hver studie er
 * `## [tittel](url)` etterfulgt av merkede avsnitt (Kategori/Hva/Resultat/Relevans).
 * Returnerer [{ title, url, category, parts: [{ label, html }], html }] der
 * `category` er 'medisin'/'trening'/'kosthold' (null for gamle briefinger uten
 * etikett) og `parts` er de øvrige merkede avsnittene (tom hvis ingen merkede
 * deler → bruk `html`-fallback).
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
      // Vanlig form: «[tittel](url)». Claude dropper av og til den innledende
      // «[», så «[» er valgfri — ellers ville tittelen + rå-URL rendret som tekst
      // (ikke-brytbar lenke → sprengt kortbredde).
      const link = heading.match(/^\[?(.*?)\]\((.*?)\)\s*$/);

      const parts = [];
      let category = null;
      STUDY_PART_RE.lastIndex = 0;
      let m;
      while ((m = STUDY_PART_RE.exec(body)) !== null) {
        const label = m[1].trim();
        const raw = m[2].trim();
        if (/^kategori$/i.test(label)) {
          category = normalizeCategory(raw);
          continue;
        }
        parts.push({ label, html: marked.parseInline(raw), text: raw });
      }

      return {
        title: link ? link[1] : heading,
        url: link ? link[2] : null,
        category,
        parts,
        html: renderMarkdown(body),
      };
    });
}

// Teaser-prioritet for tittellisten på nyhetssiden: leserens «hva betyr dette»
// først, deretter funnet, deretter legacy-etikettene fra arkiverte briefinger.
const TEASER_LABELS = ['hva det betyr for deg', 'resultat', 'relevans', 'hva som ble gjort'];

/**
 * Beste én-avsnitts-teaser for en studie fra splitResearch(). Returnerer
 * { label, html, text } eller null (studier uten merkede deler).
 */
export function studyTeaser(study) {
  const parts = study?.parts || [];
  for (const want of TEASER_LABELS) {
    const p = parts.find((x) => x.label.toLowerCase() === want);
    if (p) return p;
  }
  return parts[0] || null;
}
