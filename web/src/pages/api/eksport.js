// Eksport av lagrede oppføringer — markdown eller JSON.
//
// Respekterer de samme filtrene som /lagret (?q=&type=&kategori=&tag=&sort=), så det du
// ser er det du får ut. Åpent som resten av lesingen.
//
// To formål: (1) listen skal ikke være et fengsel — dataene dine skal kunne tas med ut;
// (2) en gratis ekstra backup ved siden av volum-backupen.
import { readSaved, queryItems, TYPE_LABELS } from '../../lib/saved.js';
import { RESEARCH_CATEGORIES, formatDateNo } from '../../lib/briefings.js';

export const prerender = false;

const catLabel = (id) =>
  RESEARCH_CATEGORIES.find((c) => c.id === id)?.label || id || '';

function toMarkdown(items) {
  const today = new Date().toLocaleDateString('sv-SE');
  const lines = [`# Lagrede oppføringer`, '', `Eksportert ${formatDateNo(today)} · ${items.length} stk.`, ''];

  for (const type of Object.keys(TYPE_LABELS)) {
    const group = items.filter((it) => it.type === type);
    if (!group.length) continue;
    lines.push(`## ${TYPE_LABELS[type]}`, '');

    for (const it of group) {
      lines.push(it.url ? `### [${it.title}](${it.url})` : `### ${it.title}`);
      const meta = [
        it.category && catLabel(it.category),
        it.journal,
        it.date && `fra briefingen ${it.date}`,
        `lagret ${String(it.savedAt).slice(0, 10)}`,
      ].filter(Boolean);
      lines.push(`*${meta.join(' · ')}*`, '');
      if (it.note) lines.push(`> ${it.note.replace(/\n/g, '\n> ')}`, '');
      if ((it.tags || []).length) lines.push(`Tagger: ${it.tags.map((t) => `\`${t}\``).join(', ')}`, '');
      for (const p of it.snapshot?.parts || []) {
        lines.push(`**${p.label}:** ${p.text || ''}`, '');
      }
      lines.push('---', '');
    }
  }
  return lines.join('\n');
}

export async function GET({ url }) {
  const p = url.searchParams;
  const format = p.get('format') === 'json' ? 'json' : 'md';

  const { items } = await readSaved();
  const results = queryItems(items, {
    q: (p.get('q') || '').trim(),
    type: p.get('type') || '',
    category: p.get('kategori') || '',
    tag: p.get('tag') || '',
    sort: p.get('sort') || 'nyest',
  });

  const stamp = new Date().toLocaleDateString('sv-SE');
  const body = format === 'json'
    ? JSON.stringify({ exportedAt: new Date().toISOString(), count: results.length, items: results }, null, 2)
    : toMarkdown(results);

  return new Response(body, {
    headers: {
      'content-type': format === 'json'
        ? 'application/json; charset=utf-8'
        : 'text/markdown; charset=utf-8',
      'content-disposition': `attachment; filename="lagret-${stamp}.${format}"`,
    },
  });
}
