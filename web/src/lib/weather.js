// Delte vær-hjelpere for værpanelet: MET-symbolkode → norsk tekst/emoji
// + små formatteringsfunksjoner. Speiler _SYMBOL_NO i news_briefing.py.

export const LABEL_NO = {
  clearsky: 'klarvær', fair: 'lettskyet', partlycloudy: 'delvis skyet', cloudy: 'skyet',
  fog: 'tåke', lightrain: 'lett regn', lightrainshowers: 'lette regnbyger', rain: 'regn',
  rainshowers: 'regnbyger', heavyrain: 'kraftig regn', heavyrainshowers: 'kraftige regnbyger',
  lightsleet: 'lett sludd', sleet: 'sludd', sleetshowers: 'sluddbyger', lightsnow: 'lett snø',
  snow: 'snø', snowshowers: 'snøbyger', thunder: 'torden', rainandthunder: 'regn og torden',
  heavyrainandthunder: 'kraftig regn og torden',
};

export function baseOf(code) {
  return String(code || '').replace(/_(day|night|polartwilight)$/, '');
}

export function labelFor(code) {
  if (!code) return '–';
  return LABEL_NO[baseOf(code)] || baseOf(code);
}

export function emojiFor(code) {
  if (!code) return '·';
  const night = /_night$/.test(code);
  const map = {
    clearsky: night ? '🌙' : '☀️', fair: night ? '🌙' : '🌤️',
    partlycloudy: night ? '☁️' : '⛅', cloudy: '☁️', fog: '🌫️',
    lightrain: '🌦️', lightrainshowers: '🌦️', rainshowers: '🌦️',
    rain: '🌧️', heavyrain: '🌧️', heavyrainshowers: '🌧️',
    lightsleet: '🌨️', sleet: '🌨️', sleetshowers: '🌨️',
    lightsnow: '🌨️', snow: '❄️', snowshowers: '🌨️',
    thunder: '⛈️', rainandthunder: '⛈️', heavyrainandthunder: '⛈️',
  };
  return map[baseOf(code)] || '🌡️';
}

// «5 (15)» — vindkast i parentes når det finnes og er høyere enn vinden.
export function fmtWind(wind, gust) {
  if (wind == null) return '–';
  const w = Math.round(wind);
  const g = gust != null ? Math.round(gust) : null;
  return g != null && g > w ? `${w} (${g})` : String(w);
}

export function fmtTemp(t) {
  return t == null ? '–' : `${Math.round(t)}°`;
}

export function fmtMm(v) {
  if (v == null || v === 0) return '0';
  return v.toFixed(1).replace('.', ',');
}

// Rød for plussgrader, blå for minus (som Yr).
export function tempClass(t) {
  if (t == null) return '';
  return Math.round(t) >= 0 ? 'wx-plus' : 'wx-minus';
}
