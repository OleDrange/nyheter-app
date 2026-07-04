// Host-basert ruting: forskning.modr.no serveres av samme app som nyheter.
// Forespørsler til forskning-subdomenet skrives internt om til /forskning/*-rutene,
// så URL-ene i nettleseren forblir rene (forskning.modr.no/, /arkiv, /b/<dato>).
// `locals.fbase` er prefikset sidene skal bruke i interne forskning-lenker:
// '' på subdomenet, '/forskning' ved direkte sti-tilgang (dev / fallback).
export function onRequest(context, next) {
  const host = (context.request.headers.get('host') || '').toLowerCase();
  const { pathname } = context.url;
  const isForskningHost = host === 'forskning.modr.no' || host.startsWith('forskning.');

  if (isForskningHost && !pathname.startsWith('/forskning')) {
    context.locals.fbase = '';
    return next(pathname === '/' ? '/forskning' : `/forskning${pathname}`);
  }
  context.locals.fbase = isForskningHost ? '' : '/forskning';
  return next();
}
