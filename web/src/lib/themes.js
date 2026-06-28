// Tema-register — eneste stedet et tema registreres for knappen/menyen.
//
// Legge til et nytt tema:
//   1) Lag en [data-theme="<id>"]-blokk i src/styles/global.css med fargetokens.
//   2) Legg til én linje her med samme id, et visningsnavn og en swatch-farge
//      (prikken i menyen — typisk temaets bakgrunns- eller flatefarge).
// Rekkefølgen i lista er rekkefølgen i menyen (her: lys → mørk).
export const THEMES = [
  { id: 'light', name: 'Lys', swatch: '#ffffff' },
  { id: 'sepia', name: 'Sepia', swatch: '#ecdcbd' },
  { id: 'dim', name: 'Skumring', swatch: '#2d333b' },
  { id: 'dark', name: 'Mørk', swatch: '#161b22' },
  { id: 'midnight', name: 'Midnatt', swatch: '#000000' },
];

// Holdes i synk med den hardkodede nøkkelen i anti-FOUC-skriptet i Base.astro.
export const STORAGE_KEY = 'theme';
export const DEFAULT_THEME = 'light';
