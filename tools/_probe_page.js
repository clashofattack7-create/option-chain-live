
const http = require("http");
function getJSON(url){return new Promise((res,rej)=>{http.get(url,r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>res(JSON.parse(d)))}).on('error',rej)})}
(async()=>{
  const list = await getJSON("http://127.0.0.1:9333/json");
  const page = list.filter(t=>t.type==="page")[0];
  if(!page){console.log("NO PAGE");process.exit(1)}
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r=>ws.onopen=r);
  let id=0; const pend=new Map();
  ws.onmessage = ev=>{const m=JSON.parse(ev.data); if(m.id&&pend.has(m.id)){pend.get(m.id)(m.result);pend.delete(m.id)}};
  const send=(method,params={})=>new Promise(r=>{const i=++id;pend.set(i,r);ws.send(JSON.stringify({id:i,method,params}))});
  const ev=async e=>{const r=await send("Runtime.evaluate",{expression:e,returnByValue:true,awaitPromise:true});return r.result?r.result.value:JSON.stringify(r)};
  console.log("URL:", await ev("location.href"));
  console.log("READY:", await ev("document.readyState"));
  console.log("INPUTS:", await ev("JSON.stringify(Array.from(document.querySelectorAll('input')).map(i=>({ph:i.placeholder||'',type:i.type,max:i.maxLength,vis:(i.offsetParent!==null)})))"));
  console.log("LABELS:", await ev("JSON.stringify(Array.from(document.querySelectorAll('label,span')).map(e=>(e.textContent||'').trim()).filter(s=>s&&s.length<30).slice(0,20))"));
  console.log("RADIO:", await ev("JSON.stringify(Array.from(document.querySelectorAll('input[type=radio]')).map(i=>({name:i.name,val:i.value,checked:i.checked})))"));
  console.log("BTN:", await ev("JSON.stringify(Array.from(document.querySelectorAll('button')).map(b=>(b.textContent||'').trim()).filter(Boolean))"));
  process.exit(0);
})();
