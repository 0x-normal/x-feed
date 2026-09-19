import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html=readFileSync(new URL('../../x_engine/dashboard.html',import.meta.url),'utf8');
const source=html.split('<script>')[1].split('function render()')[0];
const runtime=vm.createContext({URL});
vm.runInContext(source,runtime);
const original={id:'1',author:'writer',name:'Writer',text:'First paragraph\n\nThe complete ending <safe>.'};
function render(item){return vm.runInContext(`card(${JSON.stringify(item)})`,runtime)}
const common={target:'watcher',timestamp:1,context:JSON.stringify({original})};
const repost=render({...common,kind:'repost',text:'RT @writer: First paragraph...'});
assert.ok(!repost.includes('RT @writer: First paragraph...'));
assert.equal(repost.split('The complete ending &lt;safe&gt;.').length-1,1);
assert.ok(repost.includes('First paragraph\n\nThe complete ending'));
const quote=render({...common,kind:'quote',text:'My complete commentary\n\nAnother paragraph.'});
assert.ok(quote.includes('My complete commentary\n\nAnother paragraph.'));
assert.ok(quote.includes('The complete ending &lt;safe&gt;.'));
const nested=render({...common,kind:'repost',text:'Short RT...',context:JSON.stringify({original:{...original,quoted:{...original,id:'2',text:'Nested quoted caption'}}})});
assert.ok(nested.includes('Nested quoted caption'));
const anchors=nested.match(/<\/?a(?:\s[^>]*|)>/g)||[];
let depth=0;
for(const tag of anchors){depth+=tag.startsWith('</')?-1:1;assert.ok(depth>=0&&depth<=1,'Quote links must not be nested');}
assert.equal(depth,0);
assert.ok(render({kind:'repost',target:'watcher',timestamp:1,text:'Only available caption',context:'{}'}).includes('Only available caption'));
const shortPost=render({kind:'post',target:'watcher',timestamp:1,text:'Short post',context:'{}'});
assert.ok(!shortPost.includes('data-read-more'));
const longText='Interesting long post '.repeat(30);
const longPost=render({kind:'post',target:'watcher',timestamp:1,text:longText,context:'{}'});
assert.ok(longPost.includes('expandable collapsed'));
assert.ok(longPost.includes('data-read-more'));
assert.ok(longPost.includes('Read more'));
const longQuote=render({...common,kind:'quote',text:'Commentary',context:JSON.stringify({original:{...original,text:longText}})});
assert.ok(longQuote.includes('context-text expandable collapsed'));
console.log('Full repost and quote caption rendering passed');
