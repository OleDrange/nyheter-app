// @ts-check
import { defineConfig } from 'astro/config';
import node from '@astrojs/node';

// SSR-modus: siden rendres ved hver forespørsel og leser dagens JSON fra volumet,
// så nytt innhold vises uten et bygge-steg.
// HOST/PORT settes som miljøvariabler i containeren (HOST=0.0.0.0, PORT=8080).
export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
});
