// Biblioteket — ALT vi noen gang har vist av nyheter og forskning, søkbart.
//
// Todelt modell, bevisst:
//   • BIBLIOTEKET utledes fra briefing-arkivet ved forespørsel. Ingenting kopieres inn i
//     lageret: briefinger er immutable, så arkivet ER fasiten. Det gjør at biblioteket
//     aldri kan komme i utakt, og at det fylles av seg selv hver dag uten skrivetilgang
//     (web monterer briefing-volumet read-only).
//   • FAVORITTER (`saved.json`) er kun brukerens egen merking + notat og tagger. Stjernen
//     er altså ikke lenger «husk denne i det hele tatt», men «denne er viktig for meg» —
//     et filter over biblioteket, ikke inngangsbilletten til det.
//
// Indeksert i BIBLIOTEKET (/lagret): nyhetspunkter, forskningsstudier og boktips. IKKE gåter og quiz i
// sin helhet (banken er hundrevis av spørsmål og hører hjemme i dagens briefing, ikke i et
// oppslagsverk) — de finnes der kun når de er favorittmerket.
//
// REPETISJONSPOOLEN er noe annet og bredere: den dekker alt vi noen gang har vist,
// inkludert gåter og quiz (`reviewPool()` under). Repetisjon skal ikke være begrenset til
// det du har rukket å pinne.

import { listDates, getBriefing, splitResearch, newsPoints, briefingStamp } from './briefings.js';
import { buildId, buildSearchText, escapeHtml, readSaved, isDue } from './saved.js';

// 27 dagsfiler i dag, ~5 studier + ~2 boktips hver. Å parse alt per forespørsel er billig,
// men det skjer flere ganger per sidevisning — så vi cacher til arkivet faktisk endrer seg.
let cache = { stamp: null, items: [] };

/** Alt som har vært vist av nyheter, studier og boktips, nyeste briefing først. */
export async function libraryEntries() {
  const stamp = await briefingStamp();
  if (cache.stamp === stamp) return cache.items;

  const byId = new Map();
  const add = (item) => {
    if (byId.has(item.id)) return; // første (nyeste) visning vinner
    item.searchText = buildSearchText(item);
    byId.set(item.id, item);
  };

  for (const date of await listDates()) {
    const b = await getBriefing(date);
    if (!b) continue;

    const meta = b.research_items || [];
    for (const st of b.research_md ? splitResearch(b.research_md) : []) {
      if (!st.url) continue;
      const m = meta.find((it) => it.url === st.url);
      add({
        id: buildId('study', { url: st.url }),
        type: 'study',
        date,
        url: st.url,
        title: st.title,
        category: st.category || m?.category || null,
        journal: m?.journal || null,
        snapshot: { parts: st.parts },
      });
    }

    // Nyhetspunkter: ett punkt = én oppføring, på linje med en studie. Alt vi har vist
    // er søkbart uten at du måtte pinne det i går — «hva var det NRK skrev om Ekofisk?»
    // er nøyaktig det biblioteket er til for. Nyheter er den største typen (~19/dag mot
    // ~5 studier), derfor er type-fanen på /lagret den viktigste filtreringen.
    for (const p of b.news_md ? newsPoints(b.news_md) : []) {
      if (!p.pinnable) continue;
      add({
        id: buildId('news', p),
        type: 'news',
        date,
        url: p.url,
        title: p.title,
        category: null,
        journal: p.section || null,
        snapshot: { parts: [{ label: 'Punktet', text: p.text, html: p.html }] },
      });
    }

    // Boktips: én bok = én oppføring. `why`-teksten er begrunnelsen Claude ga den dagen,
    // og er det eneste søkbare innholdet — derfor er den snapshotets eneste del.
    for (const bk of b.learning?.books || []) {
      if (!bk?.title) continue;
      add({
        id: buildId('book', bk),
        type: 'book',
        date,
        url: null,
        title: bk.title,
        author: bk.author || null,
        category: null,
        journal: [bk.author, bk.year].filter(Boolean).join(' · ') || null,
        snapshot: bk.why
          ? { parts: [{ label: 'Hvorfor', text: bk.why, html: escapeHtml(bk.why) }] }
          : { parts: [] },
      });
    }
  }

  cache = { stamp, items: [...byId.values()] };
  return cache.items;
}

/**
 * Biblioteket flettet med favorittene: hver oppføring får `favorite`, og favorittenes
 * notat/tagger/repetisjonstilstand legges oppå. Favoritter som ikke finnes i arkivet
 * (gåter, quiz — og studier fra dagsfiler som måtte forsvinne) legges til bakerst, så
 * ingenting brukeren har merket kan bli usynlig.
 */
export async function libraryItems() {
  const [entries, { items: saved }] = await Promise.all([libraryEntries(), readSaved()]);
  const rest = new Map(saved.map((it) => [it.id, it]));

  const out = entries.map((st) => {
    const fav = rest.get(st.id);
    if (!fav) return { ...st, favorite: false, note: '', tags: [] };
    rest.delete(st.id);
    // Favoritten vinner: den bærer notat, tagger og searchText som inkluderer dem.
    return { ...st, ...fav, favorite: true };
  });

  for (const fav of rest.values()) out.push({ ...fav, favorite: true });
  return out;
}

/**
 * Posisjonen et nyhetspunkt, en gåte, et quizspørsmål eller et boktips har i dagsfila. Pin-knappen
 * identifiserer dem med indeks (de har ingen URL), og biblioteket bærer bare teksten —
 * så den slås opp her. Null hvis dagsfila er borte; da rendres knappen uten indeks og
 * kan kun avmerkes.
 */
export async function resolveIndex(item) {
  if (item.type === 'study' || !item.date) return null;
  const b = await getBriefing(item.date);
  if (item.type === 'news') {
    const pts = b?.news_md ? newsPoints(b.news_md) : [];
    // Lenken er nøkkelen (som i buildId); tittelen er fallback for punkter uten lenke.
    const i = pts.findIndex((p) => (item.url ? p.url === item.url : p.title === item.title));
    return i === -1 ? null : i;
  }
  const src = item.type === 'riddle' ? b?.riddles
    : item.type === 'quiz' ? b?.quiz
    : b?.learning?.books;
  if (!Array.isArray(src)) return null;
  const i = item.type === 'book'
    ? src.findIndex((bk) => bk.title === item.title)
    : src.findIndex((q) => q.question === item.title);
  return i === -1 ? null : i;
}

// ─────────────────────────────────────────────────────────────────────────────
// «Dagens repetisjon»
// ─────────────────────────────────────────────────────────────────────────────
//
// Poolen er ALT arkivet har vist — studier, boktips, gåter og quizspørsmål. Fram til
// 5. august 2026 leste kortet kun `saved.json`, og valgte den mest forfalte oppføringen.
// Begge deler var feil: poolen var 5 favoritter i stedet for ~570 viste oppføringer, og
// «mest forfalt vinner» er en absorberende tilstand — `reps` bumpes bare når man trykker
// «Repetert», så den eldste favoritten (en gåte fra 20. juli) vant hver eneste dag.
//
// Valget er nå DETERMINISTISK PER DATO: samme dag gir samme oppføring ved hver
// forespørsel (SSR rendrer på nytt hele dagen), men ny dag gir ny oppføring — uten noen
// state-fil å persistere eller ta backup av.

const REVIEW_MIN_AGE_DAYS = 7; // du leste det nettopp — det er ikke repetisjon

/** Dagnummer for en YYYY-MM-DD-dato (UTC-midnatt, uavhengig av vertsstidssone). */
const dayOrdinal = (date) => Math.floor(Date.parse(`${date}T00:00:00Z`) / 86400000);

/** FNV-1a — stabil «tilfeldig» rekkefølge uten avhengigheter. */
function hash32(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

let extrasCache = { stamp: null, items: [] };

/**
 * Gåtene og quizspørsmålene arkivet har vist. Egen funksjon (og egen cache) fordi de
 * bevisst IKKE skal inn i biblioteket på /lagret — der ville de druknet oppslagsverket.
 */
async function archiveDrills() {
  const stamp = await briefingStamp();
  if (extrasCache.stamp === stamp) return extrasCache.items;

  const byId = new Map();
  const add = (item) => {
    if (byId.has(item.id)) return; // første (nyeste) visning vinner
    item.searchText = buildSearchText(item);
    byId.set(item.id, item);
  };
  const part = (label, text) => ({ label, text, html: escapeHtml(text) });

  for (const date of await listDates()) {
    const b = await getBriefing(date);
    if (!b) continue;

    for (const rd of b.riddles || []) {
      if (!rd?.question) continue;
      add({
        id: buildId('riddle', rd),
        type: 'riddle',
        date,
        url: null,
        title: rd.question,
        category: null,
        journal: rd.level ? `Nivå ${rd.level}` : null,
        snapshot: {
          parts: [
            part('Fasit', rd.answer),
            ...(rd.explanation ? [part('Løsningsvei', rd.explanation)] : []),
          ],
        },
      });
    }

    for (const q of b.quiz || []) {
      if (!q?.question) continue;
      add({
        id: buildId('quiz', q),
        type: 'quiz',
        date,
        url: null,
        title: q.question,
        category: null,
        journal: q.category || null,
        snapshot: { parts: [part('Fasit', q.answer)] },
      });
    }
  }

  extrasCache = { stamp, items: [...byId.values()] };
  return extrasCache.items;
}

/**
 * Alt som kan komme tilbake som repetisjon: bibliotek + gåter/quiz fra arkivet.
 *
 * Nyhetspunkter holdes UTE, selv om de er i biblioteket: en tre uker gammel nyhet er
 * ikke noe å repetere, og med ~19 punkter i døgnet ville de utgjort flertallet av poolen
 * og fortrengt studiene og boktipsene kortet er til for. Har du favorittmerket et
 * nyhetspunkt, sier du det motsatte — og favoritter går uansett gjennom `saved.json`
 * i `dailyReview()`, ikke gjennom denne poolen.
 */
export async function reviewPool() {
  const [entries, drills] = await Promise.all([libraryEntries(), archiveDrills()]);
  return [...entries.filter((it) => it.type !== 'news'), ...drills];
}

/**
 * Dagens repetisjon for briefingdatoen `today` (YYYY-MM-DD), eller null.
 *
 * Favoritter beholder ekte spaced repetition — men får hver tredje dag, og roterer seg
 * imellom. Uten et slikt tak ville én evig forfalt favoritt (ingen er forpliktet til å
 * trykke «Repetert») okkupert plassen for alltid, som er nøyaktig feilen dette erstatter;
 * og med en håndfull favoritter mot ~550 viste oppføringer skal hovedvekten ligge i
 * arkivet.
 */
export async function dailyReview(today) {
  const [pool, { items: saved }] = await Promise.all([reviewPool(), readSaved()]);
  const ord = dayOrdinal(today);
  const favById = new Map(saved.map((it) => [it.id, it]));
  const merge = (it) => {
    const fav = favById.get(it.id);
    return fav ? { ...it, ...fav, favorite: true } : { ...it, favorite: false };
  };

  const favDue = saved.filter((it) => isDue(it, Date.parse(`${today}T12:00:00Z`)));
  if (favDue.length && ord % 3 === 0) {
    const anchor = (it) => Date.parse(it.lastReview || it.savedAt);
    favDue.sort((a, b) => anchor(a) - anchor(b)); // mest forfalt først
    const chosen = favDue[Math.floor(ord / 3) % favDue.length];
    // Favoritten bærer sitt eget snapshot, men arkivet er fasiten når den finnes der.
    return merge(pool.find((it) => it.id === chosen.id) || chosen);
  }

  const cutoff = ord - REVIEW_MIN_AGE_DAYS;
  let best = null;
  for (const it of pool) {
    if (!it.date || dayOrdinal(it.date) > cutoff) continue;
    const key = hash32(`${ord}:${it.id}`);
    if (!best || key > best.key) best = { key, it };
  }
  return best ? merge(best.it) : null;
}

/** Tellinger til fanene: totalt, favoritter, og per type. */
export function libraryCounts(items) {
  const counts = { total: items.length, favorite: 0, study: 0, news: 0, riddle: 0, quiz: 0, book: 0 };
  for (const it of items) {
    if (it.favorite) counts.favorite += 1;
    if (counts[it.type] !== undefined) counts[it.type] += 1;
  }
  return counts;
}
