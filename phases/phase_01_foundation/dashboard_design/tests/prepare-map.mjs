/** Retain detailed North American coastline without shipping the entire 50m atlas. */
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
const require=createRequire(import.meta.url);
const {feature}=require('topojson-client');
const atlas=require('world-atlas/countries-50m.json');
const countries=feature(atlas,atlas.objects.countries).features.filter(c=>['840','124','484'].includes(String(c.id)));
writeFileSync(new URL('../src/northAmerica.json',import.meta.url),JSON.stringify(countries));
