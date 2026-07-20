// Skrive-API for lagrede studier. Alle metoder krever skrivetilgang-cookie.
//   POST   { type, date, url }                                      → upsert (idempotent)
//   DELETE { id }                                                   → fjern, returnerer
//                                                                     oppføringen så
//                                                                     klienten kan angre
//   PATCH  { id, note?, tags? }                                     → notis/tagger
import { saveItem, removeItem, patchItem } from '../../lib/saved.js';
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

export async function POST({ request, cookies }) {
  const { body, error } = await guard(request, cookies);
  if (error) return error;
  if (!body.date || !body.url) return json({ ok: false, error: 'Mangler dato eller URL.' }, 400);

  const derived = await deriveStudy(body.date, body.url);
  if (!derived) return json({ ok: false, error: 'Fant ikke studien i arkivet.' }, 404);

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
  const item = await patchItem(body.id, { note: body.note, tags: body.tags });
  if (!item) return json({ ok: false, error: 'Fant ikke oppføringen.' }, 404);
  return json({ ok: true, item });
}
