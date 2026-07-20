// Skrivebeskyttelse for lagrede studier.
//
// Siden er offentlig og har ingen innlogging. LESING av lagrede studier er åpent for alle;
// kun SKRIVING krever kodeord. Uten dette ville et offentlig skrive-endepunkt ligget ute på
// internett, der hvem som helst kunne slette listen.
//
// Cookien er «<utløp>.<HMAC-SHA256(utløp, SAVE_PASSPHRASE)>» — signaturen gjør at den ikke
// kan forfalskes, og siden nøkkelen er selve kodeordet trengs ingen sesjonslagring: bytter
// man kodeord, blir alle utstedte cookies ugyldige automatisk.

import crypto from 'node:crypto';

const COOKIE = 'lagret_auth';
const MAX_AGE = 60 * 60 * 24 * 365; // ett år — man skal gjøre dette én gang per enhet

const passphrase = () => process.env.SAVE_PASSPHRASE || '';

/** Er skriving i det hele tatt konfigurert? Uten kodeord skjules pin-knappene. */
export const writingEnabled = () => passphrase().length > 0;

const sign = (value) =>
  crypto.createHmac('sha256', passphrase()).update(String(value)).digest('hex');

/** Konstant-tid sammenligning — unngår at responstiden lekker hvor langt et gjett kom. */
function safeEqual(a, b) {
  const bufA = Buffer.from(String(a));
  const bufB = Buffer.from(String(b));
  if (bufA.length !== bufB.length) return false;
  return crypto.timingSafeEqual(bufA, bufB);
}

export function checkPassphrase(input) {
  if (!writingEnabled()) return false;
  return safeEqual(input || '', passphrase());
}

export function issueCookie(cookies) {
  const expires = Date.now() + MAX_AGE * 1000;
  cookies.set(COOKIE, `${expires}.${sign(expires)}`, {
    path: '/',
    httpOnly: true,   // aldri lesbar fra klient-JS
    secure: true,
    sameSite: 'lax',
    maxAge: MAX_AGE,
  });
}

export function clearCookie(cookies) {
  cookies.delete(COOKIE, { path: '/' });
}

/** Har forespørselen gyldig skrivetilgang? */
export function isAuthed(cookies) {
  if (!writingEnabled()) return false;
  const raw = cookies.get(COOKIE)?.value;
  if (!raw) return false;
  const [expires, sig] = String(raw).split('.');
  if (!expires || !sig) return false;
  if (!Number(expires) || Number(expires) < Date.now()) return false;
  return safeEqual(sig, sign(expires));
}

// ─────────────────────────────────────────────────────────────────────────────
// Rate-limiting på innlogging (in-memory — nullstilles ved omstart, som er greit:
// vinduet er kort og formålet er kun å gjøre gjetting upraktisk)
// ─────────────────────────────────────────────────────────────────────────────

const WINDOW_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 10;
const attempts = new Map(); // ip → { count, resetAt }

export function rateLimited(ip) {
  const key = ip || 'ukjent';
  const now = Date.now();
  const rec = attempts.get(key);
  if (!rec || now > rec.resetAt) {
    attempts.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return false;
  }
  rec.count += 1;
  return rec.count > MAX_ATTEMPTS;
}

/** Nullstill telleren ved vellykket innlogging. */
export function clearAttempts(ip) {
  attempts.delete(ip || 'ukjent');
}
