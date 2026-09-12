const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=process.env.EAGLE_TEST_SOURCE||require('path').resolve(__dirname,'../plugin');
const script=fs.readFileSync(source+'/web/index.html','utf8').split('<script>')[1].split('</script>')[0];
new vm.Script(script);
const flush=()=>new Promise(r=>setImmediate(r));
async function selectionQueue(){
 const posts=[],waiting=[],nodes={};
 const c={taskId:'test-task-123',referenceIds:[],referenceSet:new Set(),referenceItems:new Map(),referenceBusy:false,referenceWanted:null,referenceVersion:0,csrf:'test',msg(){},$:id=>nodes[id]??={},readReferences:async()=>{},renderReferences(rows){c.referenceIds=rows.map(x=>x.id)},fetch:async(path,req)=>{const ids=JSON.parse(req.body).ids;posts.push(ids);await new Promise(r=>waiting.push(r));return {ok:true,json:async()=>({ids,items:ids.map(id=>({id}))})}}};
 vm.createContext(c);vm.runInContext(script.slice(script.indexOf('async function saveReferences'),script.indexOf('let csrf=')),c);
 c.toggleReference('one');c.toggleReference('two');waiting.shift()();await flush();
 assert.deepStrictEqual(posts,[['one'],['one','two']]);
 c.$('clearRefs').onclick();waiting.shift()();await flush();assert.deepStrictEqual(posts[2],[]);
 waiting.shift()();await flush();assert.equal(c.referenceIds.length,0);assert.equal(c.referenceBusy,false);
}
async function detailRace(){
 let resolve,replacements=0;
 const c={api:()=>new Promise(r=>resolve=r),editing:null,focused:null,infoContent:i=>i,renderWindow(){},msg(){},$:()=>({replaceChildren(){replacements++},showModal(){}})};
 vm.createContext(c);vm.runInContext(script.slice(script.indexOf('let detailSeq='),script.indexOf('function closeDetail')),c);
 const p=c.detail('one');c.editing={id:'one',input:{value:'draft'}};resolve({id:'two',name:'two'});await p;assert.equal(replacements,0);
}
async function selectionFailureQueue(){
 const posts=[],waiting=[],nodes={};let recover;
 const c={taskId:'test-task-123',referenceIds:[],referenceSet:new Set(),referenceItems:new Map(),referenceBusy:false,referenceWanted:null,referenceVersion:0,csrf:'test',msg(){},$:id=>nodes[id]??={},readReferences:()=>new Promise(r=>recover=r),renderReferences(rows){c.referenceIds=rows.map(x=>x.id)},fetch:async(path,req)=>{const ids=JSON.parse(req.body).ids;posts.push(ids);const first=posts.length===1;await new Promise(r=>waiting.push(r));return {ok:!first,json:async()=>first?{error:'temporary failure'}:{ids,items:ids.map(id=>({id}))}}}};
 vm.createContext(c);vm.runInContext(script.slice(script.indexOf('async function saveReferences'),script.indexOf('let csrf=')),c);
 c.toggleReference('one');c.$('clearRefs').onclick();waiting.shift()();await flush();
 c.toggleReference('two');recover();await flush();assert.deepStrictEqual(posts,[['one'],['two']]);
 waiting.shift()();await flush();assert.equal(c.referenceIds.join(','),'two');assert.equal(c.referenceBusy,false);
}
function cacheWorkingSet(){
 let calls=0;
 const c={display:{name:true,ext:true,tags:true,prompt:true},measured:new Map(),itemMeasurements:new WeakMap(),metadata:i=>i,measureBox:{style:{},replaceChildren(){calls++},firstChild:{getBoundingClientRect:()=>({height:123})}}};
 vm.createContext(c);vm.runInContext(script.slice(script.indexOf('function measuredMeta'),script.indexOf('async function edit')),c);
 const rows=Array.from({length:3001},(_,n)=>({name:String(n),ext:'png',tags:['x'],annotation:'prompt'}));
 rows.forEach(i=>c.measuredMeta(i,220));const before=calls;rows.forEach(i=>c.measuredMeta(i,220));assert.equal(calls-before,0);assert.equal(c.measured.size,3000);
 rows[0].annotation='new';c.measuredMeta(rows[0],220);assert.equal(calls-before,1);
 c.measuredMeta(rows[0],320);assert.equal(calls-before,2);
}
async function detachedEdit(){
 let resolve;
 const c={editing:null,editBusy:false,api:()=>new Promise(r=>resolve=r),msg(){},document:{querySelector(){throw Error('unexpected lookup')}},detailSeq:0};
 vm.createContext(c);vm.runInContext(script.slice(script.indexOf('async function edit'),script.indexOf('const leaveDialog=')),c);
 const p=c.edit('one','name',{isConnected:false});resolve({id:'one'});await p;assert.equal(c.editing,null);
}
(async()=>{await selectionQueue();await selectionFailureQueue();await detailRace();cacheWorkingSet();await detachedEdit();console.log('PASS: queued add/remove/clear including failure recovery; late detail guard; 3001-item repeat layout has zero new DOM measurements; changed metadata remeasured; detached editor rejected')})().catch(e=>{console.error(e);process.exitCode=1});
