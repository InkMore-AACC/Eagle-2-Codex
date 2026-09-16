const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[2],'utf8');
const code=html.slice(html.indexOf('let activeHover=null;'),html.indexOf('function prepareVideo'));
let clock=0,next=0,timers=new Map(),videos=[],callbacks={},grid={children:[],replaceChildren(){this.children=[]}},modal={open:false};
const context={editing:null,document:{hidden:false,addEventListener(k,f){callbacks[k]=f}},window:{addEventListener(k,f){callbacks[k]=f}},$:id=>id==='grid'?grid:modal,
 setTimeout(f,n){timers.set(++next,{at:clock+n,f});return next},clearTimeout(id){timers.delete(id)},
 releaseVideo(v){v.paused=true;v.src='';v.released=true},make(tag,text,cls){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});const v={className:cls,paused:true,src:'',classList:{add(){v.visible=true}},setAttribute(){},play(){v.paused=false;return promise},remove(){if(this.card?.video===this)this.card.video=null;this.removed=true},resolve,reject};videos.push(v);return v}};
vm.createContext(context);vm.runInContext(code,context);
function card(){const c={isConnected:true,video:null,querySelector(s){return s==='.card-video'?this.video:{append(v){c.video=v;v.card=c}}}};grid.children.push(c);context.bindCardVideo(c,{id:'video1',name:'示例'});return c}
function tick(n){clock+=n;for(const [k,t] of timers)if(t.at<=clock){timers.delete(k);t.f()}}
(async()=>{
 const c=card();c.onmouseenter();tick(499);assert.equal(videos.length,0);c.onmouseleave();tick(1);assert.equal(videos.length,0);
 c.onmouseenter();tick(500);const first=c.video;assert.ok(first);assert.equal(first.muted,true);assert.equal(first.loop,true);first.onplaying();assert.equal(first.visible,true);
 c.onmouseleave();assert.equal(c.video,null);assert.equal(first.paused,true);assert.equal(first.src,'');first.resolve();await Promise.resolve();assert.equal(first.paused,true);
 c.onmouseenter();tick(500);const old=c.video;c.onmouseleave();c.onmouseenter();tick(500);const newer=c.video;old.resolve();await Promise.resolve();assert.equal(c.video,newer);assert.equal(newer.paused,false);
 const b=card();b.onmouseenter();tick(500);assert.equal(newer.paused,true);assert.equal(c.video,null);assert.ok(b.video);context.clearCards();assert.equal(b.video,null);assert.equal(grid.children.length,0);
 for(const state of ['hidden','modal','editing','disconnected']){const x=card();if(state==='hidden')context.document.hidden=true;if(state==='modal')modal.open=true;if(state==='editing')context.editing={};if(state==='disconnected')x.isConnected=false;x.onmouseenter();tick(500);assert.equal(x.video,null);context.document.hidden=false;modal.open=false;context.editing=null;context.clearCards()}
 const fail=card();fail.onmouseenter();tick(500);fail.video.reject(Error('unsupported'));await Promise.resolve();await Promise.resolve();assert.equal(fail.video,null);
 const pending=card();pending.onmouseenter();context.clearCards();tick(500);assert.equal(pending.video,null);
 assert.ok(!html.includes("make('button','播放视频'"));assert.ok(!html.includes('function videoPlayer('));assert.ok(html.includes("make('span','video','video-mark')"));
 console.log('Hover lifecycle checks passed:',process.argv[2]);
})().catch(e=>{console.error(e);process.exitCode=1});
