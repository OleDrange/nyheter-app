// Lesing av tekstil-kunnskapsbasen (tekstil.modr.no).
//
// Til forskjell fra nyheter og forskning finnes det ingen dagsfiler her: hele
// kunnskapsbasen er ÉN fil som generatoren skriver om hver kjøring. Den er dermed også
// eneste sannhet for emnelista, gruppenavn og dommene — nettsiden har ingen egen kopi av
// registeret, så et nytt emne i `textile_topics.py` dukker opp her uten kodeendring.
//
// Filen kan mangle helt (før første generatorkjøring). Alle funksjonene under skal da
// returnere tomme strukturer, ikke kaste — siden viser «ingenting generert ennå».
import fs from 'node:fs/promises';
import path from 'node:path';
import { renderMarkdown } from './briefings.js';

const KB_PATH =
  process.env.TEXTILE_KB || path.join(process.cwd(), 'textile_kb.json');

// Cachen nøkles på mtime + størrelse. Filen skrives atomisk (.tmp + rename) av
// generatoren, så vi leser aldri en halvskrevet base — men mtime alene ville ikke fanget
// to skrivinger innen samme sekund, derfor størrelsen i tillegg.
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

/** Emner gruppert etter `kind`, i registerets rekkefølge. Utgåtte emner (fjernet fra
 *  registeret, men fortsatt med evidens) legges sist i sin egen gruppe. */
export function topicsByKind(kb) {
  const all = Object.values(kb.topics || {});
  const order = kb.kind_order || [];
  const groups = order.map((kind) => ({
    kind,
    label: (kb.kind_labels || {})[kind] || kind,
    topics: all
      .filter((t) => t.kind === kind && !t.retired)
      .sort((a, b) => b.studies.length - a.studies.length || a.name.localeCompare(b.name, 'no')),
  }));
  const retired = all.filter((t) => t.retired && t.studies.length);
  if (retired.length) groups.push({ kind: 'utgatt', label: 'Utgåtte emner', topics: retired });
  return groups.filter((g) => g.topics.length);
}

export function getTopic(kb, slug) {
  return (kb.topics || {})[slug] || null;
}

/** Studiene under ett emne, nyest innrullet først, med ferdig rendret omtale. */
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
//   **Emner:** …  **Metode:** …  **Funn:** …  **Hva det betyr for innkjøp:** …  **Forbehold:** …
// Vi splitter på de merkede etikettene, akkurat som splitResearch() gjør for
// forskningsbriefingen, så et oppslag kan vise avsnittene hver for seg i stedet for én
// vegg av tekst. **Emner:** hoppes over — den er maskinlesing, ikke innhold.
const LABELS = ['Metode', 'Funn', 'Hva det betyr for innkjøp', 'Forbehold'];
const LABEL_RE = new RegExp(`^\\*\\*(${LABELS.join('|')}):\\*\\*\\s*`, 'i');

function decorate(study) {
  const lines = String(study.writeup || '').split('\n');
  const parts = [];
  let current = null;
  for (const line of lines) {
    if (/^##\s/.test(line) || /^\*\*Emner:\*\*/i.test(line)) {
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
    // Fallback hvis omtalen ikke følger formatet (skal ikke skje, men en tom studieside
    // er verre enn en uformatert).
    html: parts.length ? null : renderMarkdown(study.writeup || ''),
    doi: (String(study.url || '').match(/10\.\d{4,9}\/\S+/) || [null])[0],
  };
}

/** Hele kravspesifikasjonen, flatet ut på tvers av emner og gruppert på nivå.
 *  Rekkefølgen på nivåene er «mest inngripende først» (utelukkes → dokumenteres →
 *  foretrekkes), og innenfor hvert nivå sorteres sterk evidens øverst. */
const LEVEL_ORDER = ['unngaa', 'dokumenter', 'foretrekk'];
const LEVEL_LABELS = {
  unngaa: 'Utelukkes',
  dokumenter: 'Krever dokumentasjon',
  foretrekk: 'Foretrekkes',
};
const STRENGTH_RANK = { sterk: 0, moderat: 1, svak: 2 };

export function criteriaGroups(kb) {
  const rows = [];
  for (const topic of Object.values(kb.topics || {})) {
    for (const c of topic.criteria || []) {
      rows.push({ ...c, topic: { slug: topic.slug, name: topic.name, kind: topic.kind } });
    }
  }
  return LEVEL_ORDER.map((level) => ({
    level,
    label: LEVEL_LABELS[level],
    rows: rows
      .filter((r) => r.level === level)
      .sort(
        (a, b) =>
          (STRENGTH_RANK[a.strength] ?? 3) - (STRENGTH_RANK[b.strength] ?? 3) ||
          a.topic.name.localeCompare(b.topic.name, 'no'),
      ),
  })).filter((g) => g.rows.length);
}

/** Leverandørspørsmålene, samlet per emne. Emner uten spørsmål utelates. */
export function supplierQuestions(kb) {
  return Object.values(kb.topics || {})
    .filter((t) => (t.questions || []).length)
    .sort((a, b) => a.name.localeCompare(b.name, 'no'))
    .map((t) => ({ slug: t.slug, name: t.name, questions: t.questions }));
}

/** Fordelingen av dommer — brukes til statuslinjen på forsiden. */
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

export const TEKSTIL_URL = 'https://tekstil.modr.no';
