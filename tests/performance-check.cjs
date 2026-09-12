const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('plugin/web/index.html','utf8').split('<script>')[1].split('</script>')[0];
let callbacks=[];const ctx={display:{},requestAnimationFrame:fn=>(callbacks.push(fn),callbacks.length),layout:()=>{ctx.layouts++},layouts:0};vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function measuredMeta'),source.indexOf('async function edit')),ctx);
for(const name of [false,true])for(const ext of [false,true]){ctx.display={name,ext,tags:false,prompt:false};assert.equal(ctx.measuredMeta({},200),name||ext?30:0)}
vm.runInContext(source.slice(source.indexOf('let layoutFrame='),source.indexOf('function layout(){')),ctx);
for(let i=0;i<100;i++)ctx.scheduleLayout();assert.equal(callbacks.length,1);callbacks[0]();assert.equal(ctx.layouts,1);
console.log('PASS: all four plain-caption display combinations; 100 resize requests coalesced into one layout');
