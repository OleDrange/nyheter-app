// Lagrede («pinnede») studier, nyhetspunkter, gåter, quizspørsmål og boktips.
//
// Bor på et EGET volum (`SAVED_DIR`, /state i prod) — ikke på briefing-volumet, som
// nettappen bevisst monterer read-only. Nettappen er eneste prosess eksponert mot
// internett og skal aldri kunne skrive inn i arkivet.
//
// Lagringsform er et FULLT øyeblikksbilde, ikke en referanse til arkivet: briefinger er
// immutable når de først er skrevet, så det er ingenting å holde synkronisert, og et
// snapshot fjerner en hel feilklasse (manglende arkivfil, drift i splitResearch()).

import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';

const SAVED_DIR = process.env.SAVED_DIR || 'state';
const SAVED_FILE = path.join(SAVED_DIR, 'saved.json');

const EMPTY = { version: 1, items: [] };

export const TYPES = ['study', 'news', 'riddle', 'quiz', 'book'];

export const TYPE_LABELS = {
  study: 'Studier', news: 'Nyheter', riddle: 'Gåter', quiz: 'Quiz', book: 'Bøker',
};
export const TYPE_EMOJI = { study: '🔬', news: '📰', riddle: '🧩', quiz: '🧠', book: '📚' };

// Spaced repetition — samme utvidende intervall som quizbanken (_QUIZ_REVIEW_INTERVALS).
// En lagret-liste ingen leser er en kirkegård; dette er det som gjør den til et verktøy.
export const REVIEW_INTERVALS = [7, 30, 90, 180]; // dager

// ─────────────────────────────────────────────────────────────────────────────
// ID — idempotens
// ─────────────────────────────────────────────────────────────────────────────

const sha12 = (s) => crypto.createHash('sha1').update(s, 'utf8').digest('hex').slice(0, 12);

/**
 * Stabil ID, slik at samme sak lagret to ganger blir ÉN oppføring.
 * Studier nøkles på DOI (globalt unik); gåter og quiz har ingen ID og nøkles på en
 * innholdshash av spørsmålsteksten — den overlever også quizens repetisjonsmekanikk.
 */
export function buildId(type, { url, question, title, author } = {}) {
  if (type === 'study') {
    const doi = String(url || '').match(/10\.\d{4,9}\/\S+/);
    return `study:${doi ? doi[0].toLowerCase() : sha12(String(url || title || ''))}`;
  }
  // Nyhetspunkter nøkles på kildelenken: samme sak dekket to dager på rad (generatoren
  // tillater det ved vesentlig ny utvikling) skal bli én oppføring, og URL-en er det
  // eneste stabile ved den — punktteksten omskrives. Punkter uten lenke faller tilbake
  // til en innholdshash av teksten.
  if (type === 'news') {
    return `news:${sha12(String(url || question || title || '').trim().toLowerCase())}`;
  }
  // Bøker har ingen ID og ingen URL: tittel + forfatter er nøkkelen. Forfatteren er med
  // fordi titler går igjen på tvers av bøker; året er det ikke, siden utgave/opptrykk
  // ellers ville gitt to oppføringer for samme bok.
  if (type === 'book') {
    return `book:${sha12(`${String(title || '')} ${String(author || '')}`.trim().toLowerCase())}`;
  }
  return `${type}:${sha12(String(question || title || '').trim().toLowerCase())}`;
}

/**
 * Innholdet i biblioteket er ren tekst fra arkivet, men rendres med `set:html` på
 * /lagret — så det escapes ved indeksering/lagring, ett sted.
 */
export function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** Tagger normaliseres, ellers får vi «protein», «Protein» og «proteiner» som tre filtre. */
export function normalizeTags(tags) {
  const list = Array.isArray(tags)
    ? tags
    : String(tags || '').split(',');
  const seen = new Set();
  for (const raw of list) {
    const t = String(raw || '').trim().toLowerCase().replace(/\s+/g, ' ');
    if (t) seen.add(t.slice(0, 40));
  }
  return [...seen].slice(0, 12);
}

/** Fritekst-indeks: tittel + all brødtekst + notis + tagger. Bygges ved lagring. */
export function buildSearchText(item) {
  const parts = (item.snapshot?.parts || []).map((p) => `${p.label} ${p.text || ''}`);
  return [item.title, item.journal, item.category, ...parts, item.note, ...(item.tags || [])]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

// ─────────────────────────────────────────────────────────────────────────────
// Lesing / skriving
// ─────────────────────────────────────────────────────────────────────────────

export async function readSaved() {
  try {
    const raw = await fs.readFile(SAVED_FILE, 'utf8');
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.items)) return { ...EMPTY };
    return { version: data.version || 1, items: data.items };
  } catch (err) {
    if (err.code === 'ENOENT' || err instanceof SyntaxError) return { ...EMPTY };
    throw err;
  }
}

// Node er én prosess, men to samtidige POST-er kan interleave read-modify-write og miste
// en lagring. Alle mutasjoner går derfor gjennom én seriell kø.
let queue = Promise.resolve();

function serialize(fn) {
  const run = queue.then(fn, fn);
  // Hold køen i live selv om et ledd feiler — ellers står alle senere skrivinger.
  queue = run.then(() => undefined, () => undefined);
  return run;
}

async function writeSaved(data) {
  await fs.mkdir(SAVED_DIR, { recursive: true });
  const tmp = `${SAVED_FILE}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(data, null, 2), 'utf8');
  await fs.rename(tmp, SAVED_FILE); // atomisk
}

/** Les → endre → skriv, seriellt. `mutate` får dataobjektet og returnerer et resultat. */
function mutation(mutate) {
  return serialize(async () => {
    const data = await readSaved();
    const result = mutate(data);
    await writeSaved(data);
    return result;
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Operasjoner
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Lagre. Idempotent på `id`: lagres samme sak igjen, oppdateres snapshotet, men
 * notis, tagger og opprinnelig lagringstidspunkt beholdes.
 */
export function saveItem(input) {
  const type = TYPES.includes(input.type) ? input.type : 'study';
  const id = input.id || buildId(type, input);
  return mutation((data) => {
    const existing = data.items.find((it) => it.id === id);
    const item = {
      id,
      type,
      date: input.date || existing?.date || null,
      url: input.url || existing?.url || null,
      title: String(input.title || existing?.title || '').slice(0, 400),
      category: input.category || existing?.category || null,
      journal: input.journal || existing?.journal || null,
      snapshot: input.snapshot || existing?.snapshot || null,
      note: existing?.note ?? '',
      tags: existing?.tags ?? [],
      savedAt: existing?.savedAt || new Date().toISOString(),
      // Repetisjonstilstand: `reps` = antall ganger repetert, `lastReview` = sist sett.
      // Nullstilles aldri ved re-lagring — historikken din er verdt mer enn snapshotet.
      reps: existing?.reps ?? 0,
      lastReview: existing?.lastReview || existing?.savedAt || new Date().toISOString(),
    };
    item.searchText = buildSearchText(item);
    if (existing) Object.assign(existing, item);
    else data.items.unshift(item);
    return item;
  });
}

export function removeItem(id) {
  return mutation((data) => {
    const i = data.items.findIndex((it) => it.id === id);
    if (i === -1) return null;
    return data.items.splice(i, 1)[0]; // returneres så klienten kan angre
  });
}

export function patchItem(id, { note, tags }) {
  return mutation((data) => {
    const item = data.items.find((it) => it.id === id);
    if (!item) return null;
    if (note !== undefined) item.note = String(note).slice(0, 2000);
    if (tags !== undefined) item.tags = normalizeTags(tags);
    item.searchText = buildSearchText(item);
    return item;
  });
}

/** Marker som repetert: bumper `reps` og setter `lastReview` til nå. */
export function reviewItem(id) {
  return mutation((data) => {
    const item = data.items.find((it) => it.id === id);
    if (!item) return null;
    item.reps = (item.reps || 0) + 1;
    item.lastReview = new Date().toISOString();
    return item;
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Spaced repetition
// ─────────────────────────────────────────────────────────────────────────────

/** Er oppføringen forfalt til repetisjon? Intervallet utvides med antall repetisjoner. */
export function isDue(item, now = Date.now()) {
  const reps = item.reps || 0;
  const interval = REVIEW_INTERVALS[Math.min(reps, REVIEW_INTERVALS.length - 1)];
  const since = (now - new Date(item.lastReview || item.savedAt).getTime()) / 86400000;
  return since >= interval;
}

// Selve utvelgelsen av dagens repetisjon ligger i `library.js` (`dailyReview()`): den
// trekker fra HELE arkivet, ikke bare favorittene her. `isDue()` brukes derfra til å gi
// favoritter forrang når de er forfalt.

// ─────────────────────────────────────────────────────────────────────────────
// Spørring (siden filtrerer server-side via URL-parametre)
// ─────────────────────────────────────────────────────────────────────────────

/** Sett med lagrede ID-er — brukes til å rendre pin-knappen fylt fra første paint. */
export async function savedIdSet() {
  const { items } = await readSaved();
  return new Set(items.map((it) => it.id));
}

export function queryItems(items, { q, type, category, tag, sort, favorite } = {}) {
  let out = items;
  if (favorite) out = out.filter((it) => it.favorite);
  if (type) out = out.filter((it) => it.type === type);
  if (category) out = out.filter((it) => it.category === category);
  if (tag) out = out.filter((it) => (it.tags || []).includes(tag));
  if (q) {
    // Alle ordene må forekomme (AND) — mer forutsigbart enn delstrengsøk på hele frasen.
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    out = out.filter((it) => {
      const hay = it.searchText || buildSearchText(it);
      return words.every((w) => hay.includes(w));
    });
  }
  // Sorteringsnøkkel: hvilken briefing oppføringen kom fra. Det er den datoen som gjelder
  // for hele biblioteket — `savedAt` finnes bare på favoritter, og ville sortert alt
  // annet vilkårlig. «lagret» sorterer eksplisitt på merketidspunktet i stedet.
  const key = (it) => it.date || String(it.savedAt || '').slice(0, 10);
  const sorted = [...out];
  if (sort === 'eldst') sorted.sort((a, b) => (key(a) < key(b) ? -1 : 1));
  else if (sort === 'tittel') sorted.sort((a, b) => a.title.localeCompare(b.title, 'nb'));
  else if (sort === 'lagret') {
    sorted.sort((a, b) => String(b.savedAt || '').localeCompare(String(a.savedAt || '')));
  } else sorted.sort((a, b) => (key(a) > key(b) ? -1 : 1)); // nyest først
  return sorted;
}

/** Alle tagger i bruk, med antall — driver taggskyen og autofullføring. */
export function tagCounts(items) {
  const counts = new Map();
  for (const it of items) for (const t of it.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'nb'));
}
