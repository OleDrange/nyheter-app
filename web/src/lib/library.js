// Biblioteket — ALT vi noen gang har vist av forskning, søkbart.
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
// Indeksert: forskningsstudier og boktips. IKKE gåter og quiz i sin helhet (banken er
// hundrevis av spørsmål og hører hjemme i dagens briefing, ikke i et oppslagsverk) — de
// finnes her kun når de er favorittmerket. Skulle det endres, er det bare å indeksere
// `b.riddles`/`b.quiz` i `buildIndex()` under.

import { listDates, getBriefing, splitResearch, briefingStamp } from './briefings.js';
import { buildId, buildSearchText, escapeHtml, readSaved } from './saved.js';

// 27 dagsfiler i dag, ~5 studier + ~2 boktips hver. Å parse alt per forespørsel er billig,
// men det skjer flere ganger per sidevisning — så vi cacher til arkivet faktisk endrer seg.
let cache = { stamp: null, items: [] };

/** Alt som har vært vist av studier og boktips, nyeste briefing først. */
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
 * Posisjonen en gåte, et quizspørsmål eller et boktips har i dagsfila. Pin-knappen
 * identifiserer dem med indeks (de har ingen URL), og biblioteket bærer bare teksten —
 * så den slås opp her. Null hvis dagsfila er borte; da rendres knappen uten indeks og
 * kan kun avmerkes.
 */
export async function resolveIndex(item) {
  if (item.type === 'study' || !item.date) return null;
  const b = await getBriefing(item.date);
  const src = item.type === 'riddle' ? b?.riddles
    : item.type === 'quiz' ? b?.quiz
    : b?.learning?.books;
  if (!Array.isArray(src)) return null;
  const i = item.type === 'book'
    ? src.findIndex((bk) => bk.title === item.title)
    : src.findIndex((q) => q.question === item.title);
  return i === -1 ? null : i;
}

/** Tellinger til fanene: totalt, favoritter, og per type. */
export function libraryCounts(items) {
  const counts = { total: items.length, favorite: 0, study: 0, riddle: 0, quiz: 0, book: 0 };
  for (const it of items) {
    if (it.favorite) counts.favorite += 1;
    if (counts[it.type] !== undefined) counts[it.type] += 1;
  }
  return counts;
}
