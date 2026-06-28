import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { marked } from 'marked';

// Mappa generatoren skriver dagsfilene til (delt volum i produksjon).
const DIR = process.env.BRIEFING_DIR || '/data/briefings';

/**
 * Claude-output bruker «•»-punkter; normaliser til «- » så marked tolker dem
 * som liste. Returnerer HTML klar for `set:html`.
 */
export function renderMarkdown(md) {
  return marked.parse(String(md || '').replace(/^\s*•\s+/gm, '- '));
}

/** Alle datoer med en briefing, nyeste først (ISO-datoer sorteres som tekst). */
export async function listDates() {
  try {
    const files = await readdir(DIR);
    return files
      .filter((f) => f.endsWith('.json'))
      .map((f) => f.replace(/\.json$/, ''))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

/** Les én dagsfil. Returnerer null hvis den ikke finnes / er ugyldig. */
export async function getBriefing(date) {
  try {
    const raw = await readFile(path.join(DIR, `${date}.json`), 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
