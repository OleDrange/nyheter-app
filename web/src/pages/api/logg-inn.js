// POST { passphrase } → setter skrivetilgang-cookie. Se web/src/lib/auth.js.
import {
  checkPassphrase, issueCookie, clearCookie, writingEnabled, rateLimited, clearAttempts,
} from '../../lib/auth.js';

export const prerender = false;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });

export async function POST({ request, cookies, clientAddress }) {
  if (!writingEnabled()) {
    return json({ ok: false, error: 'Lagring er ikke konfigurert på serveren.' }, 503);
  }
  if (rateLimited(clientAddress)) {
    return json({ ok: false, error: 'For mange forsøk. Prøv igjen om et kvarter.' }, 429);
  }

  let body = {};
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: 'Ugyldig forespørsel.' }, 400);
  }

  if (!checkPassphrase(body.passphrase)) {
    return json({ ok: false, error: 'Feil kodeord.' }, 401);
  }

  clearAttempts(clientAddress);
  issueCookie(cookies);
  return json({ ok: true });
}

export async function DELETE({ cookies }) {
  clearCookie(cookies);
  return json({ ok: true });
}
