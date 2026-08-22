// Lesing av tilskudds-kunnskapsbasen (forskning.modr.no/tilskudd).
//
// Speiler `textile.js` i form: hele basen er ÉN fil som generatoren skriver om hver
// kjøring, og den er eneste sannhet for stoffliste, gruppenavn og dommer — nettsiden har
// ingen egen kopi av registeret, så et nytt stoff i `supplement_topics.py` dukker opp her
// uten kodeendring.
//
// Filen kan mangle helt (før første generatorkjøring). Alle funksjonene under skal da
// returnere tomme strukturer, ikke kaste.
import fs from 'node:fs/promises';
import path from 'node:path';
import { renderMarkdown } from './briefings.js';

const KB_PATH =
  process.env.SUPPLEMENT_KB || path.join(process.cwd(), 'supplement_kb.json');

// Cachen nøkles på mtime + størrelse, av samme grunn som i textile.js: filen skrives
// atomisk, men mtime alene fanger ikke to skrivinger innen samme sekund.
let _cache = { key: null, kb: null };

const EMPTY = {
  version: 1,
  updated: null,
  topics: {},
  studies: {},
  kind_labels: {},
  kind_order: [],
  verdicts: {},
  verdict_order: [],
  strengths: {},
  study_count: 0,
};

export async function getKb() {
  let stat;
  try {
    stat = await fs.stat(KB_PATH);
  } catch {
    return EMPTY;
  }
  const key = `${stat.mtimeMs}:${stat.size}`;
  if (_cache.key === key) return _cache.kb;
  try {
    const kb = JSON.parse(await fs.readFile(KB_PATH, 'utf-8'));
    _cache = { key, kb: { ...EMPTY, ...kb } };
    return _cache.kb;
  } catch {
    return EMPTY;
  }
}

/** Stoffer gruppert etter `kind`, i registerets rekkefølge.
 *
 *  Sorteringen INNAD i en gruppe er bevisst ikke «flest studier først» som på tekstil:
 *  her er et stoff med null studier og målt bevis-gulv ofte det mest interessante på
 *  siden. Vi sorterer på dom først (konkluderte stoffer øverst, i VERDICT_ORDER), så
 *  navn — da havner «Verdt å ta» og «Frarådes» i toppen av hver gruppe, og «for tynt
 *  grunnlag» nederst, uten at et uavklart stoff forsvinner. */
export function topicsByKind(kb) {
  const all = Object.values(kb.topics || {});
  const vorder = kb.verdict_order || [];
  const rank = (t) => {
    const i = vorder.indexOf(t.verdict);
    return i < 0 ? vorder.length : i;
  };
  const groups = (kb.kind_order || []).map((kind) => ({
    kind,
    label: (kb.kind_labels || {})[kind] || kind,
    topics: all
      .filter((t) => t.kind === kind && !t.retired)
      .sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'no')),
  }));
  const retired = all.filter((t) => t.retired && t.studies.length);
  if (retired.length) groups.push({ kind: 'utgatt', label: 'Utgåtte stoffer', topics: retired });
  return groups.filter((g) => g.topics.length);
}

export function getTopic(kb, slug) {
  return (kb.topics || {})[slug] || null;
}

/** Studiene under ett stoff, nyest innrullet først, med ferdig rendret omtale. */
export function topicStudies(kb, topic) {
  return (topic?.studies || [])
    .map((id) => (kb.studies || {})[id])
    .filter(Boolean)
    .map(decorate);
}

/** Alle studier i basen, nyest innrullet først. */
export function allStudies(kb) {
  return Object.values(kb.studies || {})
    .sort((a, b) => String(b.added).localeCompare(String(a.added)) || b.score - a.score)
    .map(decorate);
}

// Omtalen lagres som markdown i Claudes eget format:
//   ## [tittel](url)
//   **Stoffer:** …  **Metode:** …  **Resultat:** …  **Hva det betyr for deg:** …  **Forbehold:** …
// Vi splitter på de merkede etikettene, som splitResearch() gjør for forskningsbriefingen.
// **Stoffer:** hoppes over — den er maskinlesing, ikke innhold.
const LABELS = ['Metode', 'Resultat', 'Hva det betyr for deg', 'Forbehold'];
const LABEL_RE = new RegExp(`^\\*\\*(${LABELS.join('|')}):\\*\\*\\s*`, 'i');

function decorate(study) {
  const lines = String(study.writeup || '').split('\n');
  const parts = [];
  let current = null;
  for (const line of lines) {
    if (/^##\s/.test(line) || /^\*\*Stoffer:\*\*/i.test(line)) {
      current = null;
      continue;
    }
    const m = line.match(LABEL_RE);
    if (m) {
      current = { label: m[1], text: line.slice(m[0].length).trim() };
      parts.push(current);
    } else if (current && line.trim()) {
      current.text += ' ' + line.trim();
    }
  }
  return {
    ...study,
    parts: parts.map((p) => ({ ...p, html: renderMarkdown(p.text) })),
    // Fallback hvis omtalen ikke følger formatet — en uformatert studie er bedre enn
    // en tom.
    html: parts.length ? null : renderMarkdown(study.writeup || ''),
    doi: (String(study.url || '').match(/10\.\d{4,9}\/\S+/) || [null])[0],
  };
}

/** Alle doseringene i basen, gruppert på dom.
 *
 *  Dette er tilskuddssidens motstykke til tekstilens kravspesifikasjon, men snudd:
 *  der kravene skulle UT til en leverandør, skal dette INN i et medisinskap. Derfor
 *  grupperes det på dommen (hva skal jeg faktisk ta?) og ikke på evidensstyrke. */
const DOSE_VERDICTS = ['ta', 'vurder'];
const STRENGTH_RANK = { sterk: 0, moderat: 1, svak: 2 };

export function doseGroups(kb) {
  const rows = [];
  for (const topic of Object.values(kb.topics || {})) {
    if (topic.retired) continue;
    for (const d of topic.dose || []) {
      rows.push({
        ...d,
        verdict: topic.verdict,
        topic: { slug: topic.slug, name: topic.name, kind: topic.kind },
      });
    }
  }
  return (kb.verdict_order || [])
    .filter((v) => DOSE_VERDICTS.includes(v))
    .map((verdict) => ({
      verdict,
      label: (kb.verdicts || {})[verdict] || verdict,
      rows: rows
        .filter((r) => r.verdict === verdict)
        .sort(
          (a, b) =>
            (STRENGTH_RANK[a.strength] ?? 3) - (STRENGTH_RANK[b.strength] ?? 3) ||
            a.topic.name.localeCompare(b.topic.name, 'no'),
        ),
    }))
    .filter((g) => g.rows.length);
}

/** Stoffene som er dokumentert uten effekt eller frarådes — «ikke-lista».
 *  Egen funksjon fordi den er halve poenget med oppslagsverket: å kunne si nei raskt. */
export function skipList(kb) {
  return Object.values(kb.topics || {})
    .filter((t) => !t.retired && (t.verdict === 'dropp' || t.verdict === 'risiko'))
    .sort(
      (a, b) =>
        (a.verdict === 'risiko' ? 0 : 1) - (b.verdict === 'risiko' ? 0 : 1) ||
        a.name.localeCompare(b.name, 'no'),
    );
}

/** Interaksjoner og øvre grenser, samlet per stoff. Stoffer uten utelates. */
export function interactionList(kb) {
  return Object.values(kb.topics || {})
    .filter((t) => !t.retired && (t.interactions || []).length)
    .sort((a, b) => a.name.localeCompare(b.name, 'no'))
    .map((t) => ({ slug: t.slug, name: t.name, interactions: t.interactions }));
}

/** Fordelingen av dommer — statuslinjen på forsiden. */
export function verdictCounts(kb) {
  const counts = {};
  for (const t of Object.values(kb.topics || {})) {
    if (t.retired) continue;
    counts[t.verdict] = (counts[t.verdict] || 0) + 1;
  }
  return (kb.verdict_order || []).map((v) => ({
    id: v,
    label: (kb.verdicts || {})[v] || v,
    count: counts[v] || 0,
  }));
}

/** Markdown-fri variant av en syntesetekst, til korte teasere på kort og i lister.
 *  `measured` er skrevet med **fet** på nøkkeltall, som er riktig i det fullstendige
 *  oppslaget og støy i en to-linjers teaser. */
export function plain(text, max = 220) {
  const s = String(text || '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/[*_`]/g, '')
    .trim();
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}

export const TILSKUDD_URL = 'https://forskning.modr.no/tilskudd';
