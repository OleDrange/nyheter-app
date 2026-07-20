// Skrive-API for lagrede studier. Alle metoder krever skrivetilgang-cookie.
//   POST   { type, date, url }                                      → upsert (idempotent)
//   DELETE { id }                                                   → fjern, returnerer
//                                                                     oppføringen så
//                                                                     klienten kan angre
//   POST   { type:'riddle'|'quiz', date, index }                    → upsert (idempotent)
//   PATCH  { id, note?, tags? }                                     → notis/tagger
//   PATCH  { id, action:'review' }                                  → marker som repetert
import { saveItem, removeItem, patchItem, reviewItem } from '../../lib/saved.js';
import { isAuthed, writingEnabled } from '../../lib/auth.js';
import { getBriefing, splitResearch } from '../../lib/briefings.js';

export const prerender = false;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });

/** Felles vakt: konfigurert? innlogget? gyldig JSON? */
async function guard(request, cookies) {
  if (!writingEnabled()) {
    return { error: json({ ok: false, error: 'Lagring er ikke konfigurert.' }, 503) };
  }
  if (!isAuthed(cookies)) {
    return { error: json({ ok: false, error: 'Krever kodeord.' }, 401) };
  }
  try {
    return { body: await request.json() };
  } catch {
    return { error: json({ ok: false, error: 'Ugyldig forespørsel.' }, 400) };
  }
}

/**
 * Innholdet UTLEDES fra arkivet (dato + URL), det sendes ikke inn av klienten.
 * To grunner: (1) klienten kan da ikke plante vilkårlig HTML i lageret, som senere
 * rendres med `set:html` — det ville vært en XSS-vei for enhver som har kodeordet;
 * (2) payloaden blir en URL i stedet for et helt studiekort.
 */
async function deriveStudy(date, url) {
  const b = await getBriefing(date);
  if (!b?.research_md) return null;
  const study = splitResearch(b.research_md).find((st) => st.url === url);
  if (!study) return null;
  const item = (b.research_items || []).find((it) => it.url === url);
  return {
    type: 'study',
    date,
    url,
    title: study.title,
    category: study.category || item?.category || null,
    journal: item?.journal || null,
    snapshot: { parts: study.parts },
  };
}

/**
 * Gåter og quiz har ingen URL — de identifiseres med posisjon i dagsfila. Indeksen er
 * stabil fordi briefinger er immutable. Selve ID-en bygges fra spørsmålsteksten
 * (innholdshash) i saveItem(), så samme quizspørsmål lagret på to ulike dager blir
 * én oppføring — også når quizens repetisjonsmekanikk henter det tilbake.
 */
async function deriveIndexed(type, date, index) {
  const b = await getBriefing(date);
  const src = type === 'riddle' ? b?.riddles : b?.quiz;
  const q = Array.isArray(src) ? src[index] : null;
  if (!q) return null;

  const parts = type === 'riddle'
    ? [
        { label: 'Gåte', text: q.question, html: escapeHtml(q.question) },
        { label: 'Fasit', text: q.answer, html: escapeHtml(q.answer) },
        ...(q.explanation
          ? [{ label: 'Løsning', text: q.explanation, html: escapeHtml(q.explanation) }]
          : []),
      ]
    : [
        { label: 'Spørsmål', text: q.question, html: escapeHtml(q.question) },
        { label: 'Svar', text: q.answer, html: escapeHtml(q.answer) },
      ];

  return {
    type,
    date,
    url: null,
    question: q.question,                    // → innholdshash i buildId()
    title: q.question,
    category: type === 'riddle' ? `Nivå ${q.level ?? '?'}` : q.category || null,
    journal: null,
    snapshot: { parts },
  };
}

// Innholdet kommer fra arkivet, ikke fra klienten, men det er ren tekst som skal
// vises via `set:html` på /lagret — så det escapes her, én gang, ved lagring.
function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export async function POST({ request, cookies }) {
  const { body, error } = await guard(request, cookies);
  if (error) return error;

  const type = body.type || 'study';
  let derived = null;

  if (type === 'study') {
    if (!body.date || !body.url) return json({ ok: false, error: 'Mangler dato eller URL.' }, 400);
    derived = await deriveStudy(body.date, body.url);
  } else if (type === 'riddle' || type === 'quiz') {
    const index = Number(body.index);
    if (!body.date || !Number.isInteger(index)) {
      return json({ ok: false, error: 'Mangler dato eller indeks.' }, 400);
    }
    derived = await deriveIndexed(type, body.date, index);
  } else {
    return json({ ok: false, error: 'Ukjent type.' }, 400);
  }

  if (!derived) return json({ ok: false, error: 'Fant den ikke i arkivet.' }, 404);

  const item = await saveItem(derived);
  return json({ ok: true, item });
}

export async function DELETE({ request, cookies }) {
  const { body, error } = await guard(request, cookies);
  if (error) return error;
  if (!body.id) return json({ ok: false, error: 'Mangler id.' }, 400);
  const item = await removeItem(body.id);
  if (!item) return json({ ok: false, error: 'Fant ikke oppføringen.' }, 404);
  return json({ ok: true, item });
}

export async function PATCH({ request, cookies }) {
  const { body, error } = await guard(request, cookies);
  if (error) return error;
  if (!body.id) return json({ ok: false, error: 'Mangler id.' }, 400);
  const item = body.action === 'review'
    ? await reviewItem(body.id)
    : await patchItem(body.id, { note: body.note, tags: body.tags });
  if (!item) return json({ ok: false, error: 'Fant ikke oppføringen.' }, 404);
  return json({ ok: true, item });
}
