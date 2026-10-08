const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm'),path=require('path')
function api(statuses){let id=0;const timers=new Map(),calls=[];const ctx={AbortController,setTimeout:f=>{timers.set(++id,f);return id},clearTimeout:id=>timers.delete(id),request:async(url,options)=>{calls.push({url,options});return {status:statuses.shift() || 'processing'}},memoize:()=>{},cacheTtl:{},bustCache:()=>{}};vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/api/stream.js'),'utf8').replace(/^import .*\n/gm,'').replace('export const streamApi =','globalThis.streamApi ='),ctx);return {api:ctx.streamApi,timers,calls}}
const tick=()=>new Promise(setImmediate)
test('preparation can wait over ten minutes without retrigger or skip',async()=>{
 const p=api(['pending',...Array(310).fill('processing'),'ready']);const waiting=p.api.preparePlayback(42)
 await tick();for(let i=0;i<311;i++){for(const [id,f]of [...p.timers]){p.timers.delete(id);f()}await tick()}
 assert.equal(await waiting,'/api/stream/42?representation=mp3')
 assert.equal(p.calls.filter(c=>c.options.method==='POST').length,1);assert.equal(p.timers.size,0)
})
test('abort cancels polling timer and does not send another request',async()=>{
 const p=api(['pending']),ctrl=new AbortController(),waiting=p.api.preparePlayback(42,{signal:ctrl.signal});const rejected=assert.rejects(waiting,e=>e.name==='AbortError')
 await tick();ctrl.abort();await rejected;assert.equal(p.timers.size,0);assert.equal(p.calls.length,1)
})
test('ready, native and failed are terminal statuses',async()=>{
 assert.equal(await api(['ready']).api.preparePlayback(1),'/api/stream/1?representation=mp3')
 assert.equal(await api(['not_needed']).api.preparePlayback(1),'/api/stream/1')
 await assert.rejects(api(['failed']).api.preparePlayback(1),/подготовить/)
})
test('lost job after backend restart becomes retryable failure rather than infinite pending',async()=>{
 // Replace API request in the VM by loading a separate context with a lost-job reply.
 const ctx={request:async()=>({status:'pending',job_active:false}),memoize:()=>{},cacheTtl:{},bustCache:()=>{}}
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/api/stream.js'),'utf8').replace(/^import .*\n/gm,'').replace('export const streamApi =','globalThis.streamApi ='),ctx)
 await assert.rejects(ctx.streamApi.preparePlayback(1),/прервана/)
})
