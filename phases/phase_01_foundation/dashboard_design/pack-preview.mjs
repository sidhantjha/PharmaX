import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';

const root=path.dirname(fileURLToPath(import.meta.url));
const result=await build({
  configFile:path.join(root,'vite.config.ts'),
  base:'./',
  logLevel:'error',
  build:{write:false,cssCodeSplit:false,rollupOptions:{output:{format:'iife',name:'PharmaxPreview',inlineDynamicImports:true}}}
});
const output=Array.isArray(result)?result.flatMap(item=>item.output):result.output;
const script=output.find(item=>item.type==='chunk'&&item.isEntry);
const style=output.find(item=>item.type==='asset'&&String(item.fileName).endsWith('.css'));
if(!script||!style)throw new Error('Expected an inlined application script and stylesheet.');
new vm.Script(script.code);
let html=await readFile(path.join(root,'index.html'),'utf8');
html=html.replace(/<script type="module" src="[^"]+"><\/script>/,'');
html=html.replace('</head>',()=>`<style>${String(style.source)}</style></head>`);
html=html.replace('</body>',()=>`<script>${script.code.replace(/<\/script/gi,'<\\/script')}</script></body>`);
await writeFile(path.join(root,'preview.html'),html);
console.log('Created self-contained preview.html');
