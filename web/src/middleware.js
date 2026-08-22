// Host-basert ruting: ett Astro-bygg serverer tre nettsteder.
//
//   nyheter.modr.no   → /            (rotrutene)
//   forskning.modr.no → /forskning/* (skrives om internt)
//   tekstil.modr.no   → /tekstil/*   (skrives om internt)
//
// URL-ene i nettleseren forblir rene på subdomenene (forskning.modr.no/arkiv, ikke
// /forskning/arkiv). `locals.fbase` / `locals.tbase` er prefikset sidene skal bruke i
// interne lenker: '' på sitt eget subdomene, '/forskning' respektive '/tekstil' ved
// direkte sti-tilgang (dev og fallback).
const SITES = [
  { prefix: '/forskning', local: 'fbase', match: (h) => h.startsWith('forskning.') },
  { prefix: '/tekstil', local: 'tbase', match: (h) => h.startsWith('tekstil.') },
];

export function onRequest(context, next) {
  const host = (context.request.headers.get('host') || '').toLowerCase();
  const { pathname } = context.url;

  // Sider som er FELLES for alle vertsnavn skrives ikke om — de spenner over sidene og
  // finnes kun på rot-nivå.
  const isShared = pathname === '/lagret' || pathname.startsWith('/api/');

  const site = SITES.find((s) => s.match(host));
  for (const s of SITES) {
    context.locals[s.local] = site === s ? '' : s.prefix;
  }

  if (site && !isShared && !pathname.startsWith(site.prefix)) {
    return next(pathname === '/' ? site.prefix : `${site.prefix}${pathname}`);
  }
  return next();
}
