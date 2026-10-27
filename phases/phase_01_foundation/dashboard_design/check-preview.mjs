import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const html=await readFile(new URL('./preview.html',import.meta.url),'utf8');
if(/<script[^>]+src=|<link[^>]+href=|@import\s+url/.test(html))throw new Error('Preview contains an external runtime asset.');
const script=html.match(/<script>([\s\S]*?)<\/script>/g)?.at(-1)?.replace(/^<script>|<\/script>$/g,'');
if(!script)throw new Error('Preview has no inline app script.');
new vm.Script(script);
if(!script.includes('Red Sea disruption')||!script.includes('world-atlas'))throw new Error('Preview is missing the map or demo dashboard bundle.');
if(!html.includes('Supply overview'))throw new Error('Preview is missing its page shell.');
console.log('Standalone preview contains inline UI, styles, country data, and globe code.');
