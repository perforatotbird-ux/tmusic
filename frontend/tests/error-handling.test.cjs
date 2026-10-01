const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path')
const {create}=require('zustand')
const source=name=>fs.readFileSync(path.join(__dirname,'../src',name),'utf8')
const noop=()=>{}, logger={info:noop,debug:noop,warn:noop,error:noop}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const tick=()=>new Promise(setImmediate)
function load(name,ctx={},storeName){vm.createContext(ctx);let text=source(name).replace(/^import .*\n/gm,'').replace(/export /g,'');if(storeName)text=text.replace(`const ${storeName} =`,'globalThis.store =');vm.runInContext(text,ctx);return ctx}
function auth(api,storage){return load('store/authStore.js',{create,authApi:api,sessionStorage:storage,createLogger:()=>logger,bustCache:noop,usePlayerStore:{getState:()=>({reset:noop,hydrate:noop})},useFavoritesStore:{getState:()=>({reset:noop})}},'useAuthStore').store}
function storage(){const values=new Map();return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}}
function client(fetch){return load('api/client.js',{fetch,createLogger:()=>logger,retryAfterSeconds:()=>1,performance:{now:()=>0},document:{cookie:''},window:{dispatchEvent:noop},CustomEvent:function(){},setTimeout:(f)=>f()})}
function response(status,body){return {status,ok:status<400,headers:{get:()=> 'application/json'},json:async()=>body}}

test('structured 422 has readable field messages, status and no validation input values',async()=>{
 const ctx=client(async()=>response(422,{detail:[{loc:['body','name'],msg:'Invalid name',input:'do-not-expose',ctx:{private:'hidden'}}]}))
 await assert.rejects(ctx.request('/synthetic'),e=>e.name==='ApiError'&&e.status===422&&e.message.includes('body.name: Invalid name')&&!JSON.stringify(e).includes('do-not-expose'))
})
test('object detail message/code are preserved without object coercion',async()=>{
 const ctx=client(async()=>response(409,{detail:{message:'Conflict',code:'conflict'}}))
 await assert.rejects(ctx.request('/synthetic'),e=>e.message==='Conflict'&&e.code==='conflict'&&e.status===409)
})
for(const status of [500,503])test(`server ${status} error never echoes private body`,async()=>{
 const ctx=client(async()=>response(status,{detail:'/private/db/path SELECT synthetic'}))
 await assert.rejects(ctx.request('/synthetic'),e=>e.code==='server_error'&&e.status===status&&!e.message.includes('/private/')&&!JSON.stringify(e).includes('SELECT'))
})
test('network errors are typed and safe',async()=>{
 const ctx=client(async()=>{throw new Error('private synthetic upstream')})
 await assert.rejects(ctx.request('/synthetic'),e=>e.name==='ApiError'&&e.code==='network_error'&&e.status===0&&!e.message.includes('private'))
})
test('AbortError is propagated unchanged',async()=>{
 const abort=Object.assign(new Error('cancelled'),{name:'AbortError'}),ctx=client(async()=>{throw abort})
 await assert.rejects(ctx.request('/synthetic'),e=>e===abort)
})
test('401 preserves auth event and structured status',async()=>{
 const ctx=client(async()=>response(401,{}));let events=0;ctx.window.dispatchEvent=()=>events++
 await assert.rejects(ctx.request('/synthetic'),e=>e.status===401&&e.code==='authentication_required');assert.equal(events,1)
})
test('non JSON error and malformed success get safe fallbacks',async()=>{
 const ctx=client(async()=>({...response(400,null),json:async()=>{throw SyntaxError('private')}}))
 await assert.rejects(ctx.request('/synthetic'),e=>e.status===400&&!e.message.includes('private'))
 ctx.fetch=async()=>({...response(200,null),json:async()=>{throw SyntaxError('private')}})
 await assert.rejects(ctx.request('/synthetic'),e=>e.code==='invalid_response'&&e.status===200)
})
test('CSRF recovery network failure is typed and does not replay the mutation',async()=>{
 let calls=0;const ctx=client(async()=>{if(++calls===1)return {...response(403,{}),clone:()=>({json:async()=>({code:'csrf_cookie_missing'})})};throw Error('synthetic')})
 await assert.rejects(ctx.request('/synthetic',{method:'POST'}),e=>e.code==='network_error');assert.equal(calls,2)
})
test('failed logout is visible, persistent, retryable and never silently authenticates',async()=>{
 const saved=storage();let failing=true,meCalls=0,loginCalls=0
 const api={logout:async()=>{if(failing)throw Error('synthetic')},getMe:async()=>{meCalls++;return {id:1}},login:async()=>loginCalls++}
 const store=auth(api,saved);store.setState({user:{id:1},isAuthenticated:true})
 const result=await store.getState().logout();assert.equal(result.ok,false);assert.equal(result.code,'logout_unconfirmed')
 assert.equal(store.getState().isAuthenticated,false);assert(store.getState().error.includes('не подтверждён'))
 assert.equal(await store.getState().checkAuth(),false)
 assert.equal(await store.getState().loginWithPassword('synthetic','synthetic'),false);assert.equal(meCalls,0);assert.equal(loginCalls,0)
 const reloaded=auth(api,saved);assert.equal(reloaded.getState().logoutPending,true);assert.equal(await reloaded.getState().checkAuth(),false)
 failing=false;assert.equal((await reloaded.getState().logout()).ok,true);assert.equal(saved.getItem('tmusic_logout_unconfirmed'),null)
 assert.equal(await reloaded.getState().loginWithPassword('synthetic','synthetic'),true)
})
test('queued login does not run after preceding logout fails',async()=>{
 const d=deferred();let calls=0;const store=auth({logout:()=>d.promise,login:async()=>calls++,getMe:async()=>({id:2})},storage())
 const logout=store.getState().logout();await tick();const login=store.getState().loginWithPassword('synthetic','synthetic');d.reject(Error('synthetic'))
 assert.equal((await logout).ok,false);assert.equal(await login,false);assert.equal(calls,0)
})
test('duplicate logout while busy has an explicit outcome',async()=>{
 const d=deferred();let calls=0;const store=auth({logout:()=>{calls++;return d.promise}},storage())
 const first=store.getState().logout();await tick();const second=await store.getState().logout();assert.equal(second.code,'logout_in_progress');assert.equal(calls,1);d.resolve();await first
})
test('favorite failure differs from successful unfavorite and refresh clears warning',async()=>{
 let fail=true;const store=load('store/favoritesStore.js',{create,playbackApi:{toggleFavorite:async()=>{if(fail)throw Error('synthetic');return {is_favorite:false}},listFavorites:async()=>({items:[]})}},'useFavoritesStore').store
 const failed=await store.getState().toggle(1);assert.equal(failed.ok,false);assert.equal(store.getState().ids.has(1),false);assert(store.getState().error)
 fail=false;const success=await store.getState().toggle(1);assert.equal(success.ok,true);assert.equal(success.isFavorite,false)
 await store.getState().load({force:true});assert.equal(store.getState().error,null)
})
function album(api){let name='Synthetic',error=null,uncertain=false,busy=false;const ref={current:false},calls=[]
 const ctx={newPlName:name,playlistTarget:{id:41},playlistMutationRef:ref,createUncertain:false,playlistsApi:api,setPlaylistBusy:v=>busy=v,setPlaylistError:v=>error=v,setCreateUncertain:v=>{uncertain=v;ctx.createUncertain=v},setNewPlName:v=>name=v,setAddedFlash:v=>calls.push(v),setPlaylistTarget:noop,setTimeout:noop,clearPlaylistFlashLater:noop}
 const text=source('components/AlbumDetail.jsx'),a=text.indexOf('  const createAndAdd ='),b=text.indexOf('  const releaseInfo =',a)
 vm.createContext(ctx);vm.runInContext(text.slice(a,b).replace('const createAndAdd =','globalThis.action ='),ctx)
 return {ctx,get:()=>({name,error,uncertain,busy,calls,ref})}
}
test('create-and-add makes one atomic POST with selected track',async()=>{
 const args=[];const handler=album({create:async(...a)=>{args.push(a);return {id:7}},addTrack:()=>{throw Error('must not use second mutation')}})
 await handler.ctx.action();assert.deepEqual(args,[['Synthetic',41]]);assert.equal(handler.get().name,'');assert.equal(handler.get().error,null)
})
test('create failure preserves input, releases guard and displays validation error',async()=>{
 const handler=album({create:async()=>{throw Object.assign(Error('Invalid track'),{status:404})}})
 await handler.ctx.action();assert.equal(handler.get().name,'Synthetic');assert.equal(handler.get().error,'Invalid track');assert.equal(handler.get().ref.current,false);assert.equal(handler.get().uncertain,false)
})
test('unknown create result cannot be blindly retried',async()=>{
 let calls=0;const handler=album({create:async()=>{calls++;throw Object.assign(Error('network'),{status:0})}})
 await handler.ctx.action();await handler.ctx.action();assert.equal(calls,1);assert.equal(handler.get().uncertain,true);assert(handler.get().error.includes('Обновите список'))
})
test('create in-flight guard prevents duplicate mutation',async()=>{
 const d=deferred();let calls=0;const handler=album({create:()=>{calls++;return d.promise}})
 const pending=handler.ctx.action();await handler.ctx.action();assert.equal(calls,1);d.resolve({id:1});await pending
})
function admin(api){const values={users:[{id:99}],health:{ok:true}},ctx={adminApi:api,loadSequence:{current:0},setLoading:v=>values.loading=v,setError:v=>values.error=v,setSectionStatus:v=>values.statuses=v,setOverview:v=>values.overview=v,setSettings:noop,setBackups:v=>values.backups=v,setUsers:v=>values.users=v,setAudit:v=>values.audit=v,setHealth:v=>values.health=v}
 const text=source('components/AdminPanel.jsx'),prefix='  const loadAll = useCallback(async () => {',a=text.indexOf(prefix),b=text.indexOf('  }, [])',a)
 vm.createContext(ctx);vm.runInContext('globalThis.action=async()=>{'+text.slice(a+prefix.length,b)+'}',ctx);return {ctx,values}
}
test('admin all failures retain known data but expose stale/errors for each section',async()=>{
 const fail=async()=>{throw Error('synthetic')},a=admin({overview:async()=>({settings:{}}),listBackups:fail,listUsers:fail,listAudit:fail,systemHealth:fail})
 await a.ctx.action();assert.equal(a.values.users[0].id,99);assert.equal(a.values.health.ok,true)
 for(const state of Object.values(a.values.statuses)){assert.equal(state.stale,true);assert.equal(state.loading,false);assert(state.error)}
})
test('admin partial success applies healthy sections and marks failed health stale',async()=>{
 const a=admin({overview:async()=>({settings:{}}),listBackups:async()=>[],listUsers:async()=>[{id:1}],listAudit:async()=>[],systemHealth:async()=>{throw Error('synthetic')}})
 await a.ctx.action();assert.equal(a.values.users[0].id,1);assert.equal(a.values.statuses.users.stale,false);assert.equal(a.values.statuses.health.stale,true)
})
test('admin older refresh cannot overwrite a newer reply',async()=>{
 const first=deferred();let count=0;const a=admin({overview:()=>++count===1?first.promise:Promise.resolve({settings:{},version:2}),listBackups:async()=>[],listUsers:async()=>[],listAudit:async()=>[],systemHealth:async()=>({ok:true})})
 const pending=a.ctx.action();await a.ctx.action();first.resolve({settings:{},version:1});await pending;assert.equal(a.values.overview.version,2)
})
test('admin overview failure also marks all dependent sections stale',async()=>{
 const a=admin({overview:async()=>{throw Error('synthetic failure')}});await a.ctx.action();assert.equal(a.values.loading,false);assert(a.values.error);assert(Object.values(a.values.statuses).every(s=>s.stale&&s.error))
})
test('failed playlist delete keeps UI data and displays an error',async()=>{
 let error=null,deleted=false;const ctx={playlistsApi:{remove:async()=>{throw Error('Synthetic request failed')}},setPanelError:v=>error=v,setPlaylists:()=>deleted=true,openPlId:null,setOpenPlId:noop,setPlTracks:noop}
 const text=source('components/HomeExtraPanels.jsx'),a=text.indexOf('  const handleDeletePlaylist ='),b=text.indexOf('  const toggleOpenPlaylist =',a)
 vm.createContext(ctx);vm.runInContext(text.slice(a,b).replace('const handleDeletePlaylist =','globalThis.action ='),ctx);await ctx.action({id:1});assert.equal(deleted,false);assert.equal(error,'Synthetic request failed')
})
test('created playlist followed by refresh failure reports creation rather than encouraging blind recreate',async()=>{
 let error=null,name='Synthetic';const ctx={newName:name,busy:false,setBusy:noop,setPanelError:v=>error=v,playlistsApi:{create:async()=>({id:1}),list:async()=>{throw Error('synthetic')}},setNewName:v=>name=v,setPlaylists:noop}
 const text=source('components/HomeExtraPanels.jsx'),a=text.indexOf('  const handleCreatePlaylist ='),b=text.indexOf('  const handlePlayPlaylist =',a)
 vm.createContext(ctx);vm.runInContext(text.slice(a,b).replace('const handleCreatePlaylist =','globalThis.action ='),ctx);await ctx.action();assert.equal(name,'');assert(error.includes('Плейлист создан'))
})

test('malformed successful create response is an unknown result, not a safe retry',async()=>{
 const handler=album({create:async()=>{throw Object.assign(Error('Malformed success'),{status:200,code:'invalid_response'})}})
 await handler.ctx.action();assert.equal(handler.get().uncertain,true);assert.equal(handler.get().name,'Synthetic')
})
test('malformed favorite success rolls back and returns failure',async()=>{
 const store=load('store/favoritesStore.js',{create,playbackApi:{toggleFavorite:async()=>null}},'useFavoritesStore').store
 const result=await store.getState().toggle(1);assert.equal(result.ok,false);assert.equal(store.getState().ids.has(1),false);assert(store.getState().error)
})
