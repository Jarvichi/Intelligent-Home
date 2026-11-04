/* Run with Node 22 against a local Chromium DevTools endpoint. No app data or credentials. */
const fs = require('fs'), path = require('path');
const host = process.env.IH_BROWSER_CDP || 'http://127.0.0.1:9222';
const output = process.env.IH_READER_OUTPUT_DIR || '/tmp/ih-reader-previews';
const ui = path.resolve(__dirname, '../app/ui');
const source = ['---','title: "Example note"','type: guide','share: false','---','# Example note','',
  'Read **clear text**, *emphasis*, ~~old wording~~ and `inline code`. Visit [[notes/second|Second note]] or [the second page](second.md).','',
  '## Checklist','- [x] Read the note','- [ ] Keep the source','  - Nested detail','',
  '3. Third item','4. Fourth item','',
  '> A useful quotation.','>','> More context.','',
  '## Comparison','| Setting | Value | Reason |','| --- | --- | --- |',
  '| Friendly label | **Enabled** | Everything remains visible |',
  '| Escaped pipe | a \\| b | ``code|value`` | Extra retained |','',
  '## Code sample','```text','<example> is text, not HTML','a very long value: '+ 'x'.repeat(240),'```','',
  '## Links','[Help](https://example.invalid/help) and [reference][ref].',
  '[unsafe](javascript:alert(1)) and [encoded](data:text/html,example) stay inert.',
  '![Preview](https://example.invalid/private-image.png) and ![[files/image.png]] are not loaded.',
  '<img src="https://example.invalid/tracker.png" onerror="window.readerUnsafe=true">',
  '<script>window.readerUnsafe=true</script>','',
  '### Final section','All content is also retained in the exact source view.','',
  '[ref]: https://example.invalid/reference',''].join('\n');
const second = '---\ntitle: "Second note"\nshare: false\n---\n# Second note\n\nLinked note content.\n';
const fixtures = {'notes/example.md': {path:'notes/example.md',title:'Example note',content:source},'notes/second.md':{path:'notes/second.md',title:'Second note',content:second}};
const assert = (value, message) => { if (!value) throw Error(message); };
(async () => {
 fs.mkdirSync(output,{recursive:true});
 for (const width of [393,1280]) for (const theme of ['light','dark']) {
  const tab=await(await fetch(host+'/json/new?about:blank',{method:'PUT'})).json();
  const ws=new WebSocket(tab.webSocketDebuggerUrl.replace(/ws:\/\/[^/]+/,host.replace('http','ws')));
  let id=0;const pending=new Map();ws.onmessage=event=>{const msg=JSON.parse(event.data);if(msg.id){const cb=pending.get(msg.id);pending.delete(msg.id);msg.error?cb.reject(Error(msg.error.message)):cb.resolve(msg.result);}};
  await new Promise(resolve=>ws.onopen=resolve);
  const send=(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;};
  try {
   await send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<600});
   await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:theme}]});
   await send('Page.navigate',{url:host+'/ih-reader-fixture'});await new Promise(resolve=>setTimeout(resolve,150));
   let html=fs.readFileSync(path.join(ui,'index.html'),'utf8').replace(/<script[\s\S]*?<\/script>/g,'').replace(/<link[^>]*>/g,'');
   html=html.replace('</head>','<style>'+['ih-ui.css','ih-patterns.css','ui.css'].map(file=>fs.readFileSync(path.join(ui,file),'utf8')).join('\n')+'</style></head>');
   const tree=await send('Page.getFrameTree');await send('Page.setDocumentContent',{frameId:tree.frameTree.frame.id,html});
   await evaluate(`window.readerFixtures=${JSON.stringify(fixtures)};window.readerUnsafe=false;window.readerFailNext=false;window.readerDelayNext=false;window.readerWriteAttempts=0;window.fetch=async(url,options={})=>{if(options.method&&options.method!=='GET'){readerWriteAttempts++;throw Error('Writes forbidden');}const parsed=new URL(url),p=parsed.pathname;let value;if(p.endsWith('/api/index'))value={count:2,areas:[{path:'notes',title:'Notes',count:2}],notes:Object.values(readerFixtures).map(({content,...row})=>row)};else if(p.endsWith('/api/read')){if(readerDelayNext){readerDelayNext=false;await new Promise(resolve=>setTimeout(resolve,80));}if(readerFailNext){readerFailNext=false;return {ok:false,status:503};}value=readerFixtures[parsed.searchParams.get('path')];}else value={notes:Object.values(readerFixtures).map(({content,...row})=>row)};return {ok:!!value,status:value?200:404,json:async()=>value};};`);
   await evaluate(fs.readFileSync(path.join(ui,'ih-ui.js'),'utf8'));
   await evaluate(fs.readFileSync(path.join(ui,'markdown.js'),'utf8'));
   await evaluate(fs.readFileSync(path.join(ui,'ui.js'),'utf8'));
   await new Promise(resolve=>setTimeout(resolve,30));
   const result=await evaluate(`(async()=>{
    const check=(value,message)=>{if(!value)throw Error(message);},tick=()=>new Promise(resolve=>setTimeout(resolve,30));
    document.querySelector('[data-path="notes/example.md"]').click();await tick();
    const article=document.querySelector('.note-markdown');check(article,'Readable body missing');
    check(document.querySelector('.note-source pre').textContent===readerFixtures['notes/example.md'].content,'Source changed');
    check(!document.querySelector('.note-source').open,'Source is not collapsed');
    check(!article.textContent.includes('share: false')&&!article.querySelector('h1'),'Front matter or duplicate title visible');
    check(article.querySelector('strong')&&article.querySelector('em')&&article.querySelector('s')&&article.querySelector('blockquote'),'Inline or quote formatting missing');
    check(article.querySelectorAll('.note-task input').length===2&&article.querySelector('.note-task input').checked,'Task state missing');
    check(article.querySelector('ul ul')&&article.querySelector('ol').start===3,'Nested or ordered lists missing');
    check(article.querySelector('table').textContent.includes('Extra retained'),'Extra table data dropped');
    check([...article.querySelectorAll('td')].some(n=>n.textContent==='a | b')&&[...article.querySelectorAll('td code')].some(n=>n.textContent==='code|value'),'Escaped/code pipe split');
    check(!article.querySelector('script,img,iframe,object,embed')&&!readerUnsafe,'Unsafe HTML executed');
    check([...article.querySelectorAll('a')].every(a=>a.getAttribute('href').startsWith('#')||['http:','https:','mailto:'].includes(a.protocol)),'Unsafe link');
    check(article.textContent.includes('Image: Preview'),'Image reference lost');
    check(article.querySelector('pre code').textContent.includes('<example>'),'Fenced code lost');
    const extra=IHBrainMarkdown.render('Setext heading\\n===\\n\\n    indented code',{title:'Other'});check(extra.querySelector('h3')&&extra.querySelector('pre code').textContent==='indented code','Setext/indented code missing');
    const old=document.querySelector('.note-source pre').textContent;
    readerDelayNext=true;document.querySelector('a[data-note-path="notes/second.md"]').click();
    check(document.querySelector('.note-pending')&&document.querySelector('#note').getAttribute('aria-busy')==='true','Pending feedback missing');
    document.querySelector('#back-to-list').click();await new Promise(resolve=>setTimeout(resolve,120));
    check(document.querySelector('#note h2').textContent==='Example note'&&!document.querySelector('.note-pending')&&!document.querySelector('#note').hasAttribute('aria-busy'),'Cancelled note response changed the reader');
    document.querySelector('[data-path="notes/example.md"]').click();await tick();
    document.querySelector('a[data-note-path="notes/second.md"]').click();await tick();
    check(document.querySelector('#note h2').textContent==='Second note','Internal note link failed');
    document.querySelector('[data-previous-note]').click();await tick();check(document.querySelector('.note-source pre').textContent===old,'Previous note/source failed');
    readerFailNext=true;document.querySelector('a[data-note-path="notes/second.md"]').click();await tick();
    check(document.querySelector('.note-error')&&document.querySelector('.note-source pre').textContent===old,'Failure discarded current note');
    document.querySelector('.note-error button').click();await tick();check(document.querySelector('#note h2').textContent==='Second note','Retry failed');
    document.querySelector('[data-previous-note]').click();await tick();
    check(readerWriteAttempts===0,'Unexpected write');
    return {formatted:true,sourceExact:true,safeHTML:true,safeLinks:true,internalNavigation:true,history:true,retry:true,pending:true,staleResponseIgnored:true,tableDataComplete:true,width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,realWrites:false};
   })()`);
   assert(result.width>=result.scroll,'Document overflow');
   for(const [name,selector] of [['reader','.reader'],['table','.note-table'],['code','.note-markdown pre']]) {
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start',behavior:'auto'})`);
    const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,`brain-readable-${width}-${theme}-${name}.png`),Buffer.from(shot.data,'base64'));
   }
   console.log(JSON.stringify({width,theme,...result}));
  } finally {await fetch(host+'/json/close/'+tab.id);ws.close();}
 }
})().catch(error=>{console.error(error.message);process.exit(1);});
