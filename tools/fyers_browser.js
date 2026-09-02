#!/usr/bin/env node
/**
 * fyers_browser.js - CDP driver for the Fyers OAuth flow using the local
 * `.fyers-edge` Edge profile (passed Cloudflare, has Fyers login context).
 *
 *   node fyers_browser.js auth <app_id> <secret> <uri> <tout_min> <out_file>
 *
 * Flow: open generate-authcode (inline Fyers login SPA) -> auto-fill client ID
 * -> user enters 4-digit PIN in the browser (or FYERS_PIN env auto-types it)
 * -> SPA redirects to redirect_uri?auth_code=... -> capture code -> exchange
 * (in-page fetch, uses the browser's Cloudflare clearance) -> save tokens ->
 * probe /data/options-chain-v3.
 *
 * Environment: FYERS_CLIENT_ID, FYERS_PIN (optional)
 */
"use strict";

const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PROFILE = "D:\\dsh\\DSH\\.fyers-edge";
const PORT = 9333;
const API = "https://api-t1.fyers.in/api/v3";
const DATA = "https://api-t1.fyers.in/data";

let ws = null;
let msgId = 0;
const pending = new Map();

function log(...a) { console.log("[driver]", ...a); }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function connect(url) {
  return new Promise((resolve, reject) => {
    ws = new WebSocket(url);
    ws.onopen = () => resolve();
    ws.onerror = () => reject(new Error("ws connect failed"));
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.id !== undefined && pending.has(m.id)) {
        const h = pending.get(m.id);
        pending.delete(m.id);
        if (m.error) h.reject(new Error(JSON.stringify(m.error)));
        else h.resolve(m.result);
      }
    };
  });
}

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evalJS(expr) {
  const r = await send("Runtime.evaluate", {
    expression: expr, returnByValue: true, awaitPromise: true,
  });
  if (r.exceptionDetails) throw new Error("eval failed: " + JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result && r.result.value;
}

async function waitFor(fn, timeoutMs, pollMs = 700, label = "cond") {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try { const v = await fn(); if (v) return v; } catch (e) {}
    await sleep(pollMs);
  }
  throw new Error("timeout: " + label);
}

async function findPageTarget() {
  const res = await fetch("http://127.0.0.1:" + PORT + "/json");
  const list = await res.json();
  return (list.filter((t) => t.type === "page")[0]) || null;
}

async function launchEdge(url) {
  spawn(EDGE, [
    "--user-data-dir=" + PROFILE,
    "--remote-debugging-port=" + PORT,
    "--no-first-run", "--no-default-browser-check",
    "--window-size=1360,900",
    "--disable-features=msEdgeFirstRunExperience",
    "--disable-session-crashed-bubble",
    url,
  ], { detached: false, stdio: "ignore" });
  await waitFor(async () => {
    try { const r = await fetch("http://127.0.0.1:" + PORT + "/json/version"); return r.ok; } catch { return false; }
  }, 30000, 500, "edge up");
  const target = await waitFor(findPageTarget, 15000, 400, "page");
  await connect(target.webSocketDebuggerUrl);
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Input.enable").catch(() => {});
}

async function state() {
  const loc = await evalJS("location.href");
  let body = "";
  try { body = (await evalJS("document.body ? document.body.innerText.slice(0, 700) : ''")) || ""; } catch {}
  let title = "";
  try { title = await evalJS("document.title || ''"); } catch {}
  return { loc, title, body };
}

function extractCode(url) {
  const m = url.match(/[?&#]auth_code=([^&#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

/* ---- login page helpers (inline SPA) ---- */

async function pageBody() { return (await state()).body; }

async function clickTabClientId() {
  return await evalJS(`(function(){
    const els = Array.from(document.querySelectorAll('label, span, div, button'));
    const el = els.find(e => /client id/i.test(e.textContent || '') && (e.textContent||'').length < 40);
    if (!el) return false;
    el.click();
    return true;
  })()`);
}

async function fillFirstInput(value) {
  return await evalJS(`(function(){
    const inputs = Array.from(document.querySelectorAll('input')).filter(i => i.offsetParent !== null && !i.readOnly && !i.disabled);
    const t = inputs[0];
    if (!t) return false;
    t.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(t, '${value}');
    t.dispatchEvent(new Event('input', {bubbles:true}));
    t.dispatchEvent(new Event('change', {bubbles:true}));
    return true;
  })()`);
}

async function clickContinue() {
  return await evalJS(`(function(){
    const els = Array.from(document.querySelectorAll('button, input[type=submit]'));
    const b = els.find(e => /continue|login|submit|verify/i.test(e.textContent || e.value || ''));
    if (!b) return false;
    b.click();
    return true;
  })()`);
}

async function typePin(pin) {
  // The PIN UI is four visible number inputs (maxLength=1). Fill each with a digit.
  const digits = String(pin).split("");
  for (let i = 0; i < digits.length; i++) {
    const ok = await evalJS(FILL_pinBox(i, digits[i]));
    if (!ok) return false;
    await sleep(150);
  }
  return true;
}

/* ---- steps ---- */

async function waitCfClear() {
  // Poll a lightweight request until Cloudflare stops 1015-ing
  const t0 = Date.now();
  while (Date.now() - t0 < 10 * 60000) {
    try {
      const txt = await evalJS("fetch('" + API + "/profile', {method:'GET'}).then(r=>r.text()).catch(e=>'NETERR:'+e.message)");
      if (txt && !/1015|Access denied|rate limited/i.test(txt)) return true;
      log("CF still rate limiting, waiting 25s...");
    } catch (e) { log("cf-ping err", e.message); }
    await sleep(25000);
  }
  throw new Error("Cloudflare did not lift the rate limit");
}

async function smartLoginWalk(clientId, pin) {
  const body = await pageBody();
  const hasLogin = /login to fyers|mobile number|client id/i.test(body);
  const hasPin = /enter your 4-digit pin|4-digit pin|confirm pin/i.test(body);
  const hasOtp = /6-digit otp|confirm otp|enter.{0,6}otp/i.test(body);
  if (hasOtp) {
    const otp = process.env.FYERS_OTP || "";
    if (otp) {
      const ok = await evalJS(PIN_FILLER(otp));
      await sleep(1200);
      const clicked = await evalJS(CLICK_BY_TEXT("confirm otp"));
      await sleep(2600);
      return ok ? "otp-typed(" + clicked + ")" : "otp-fail";
    }
    return "otp-needed";
  }
  if (hasPin) {
    if (pin) {
      await typePin(pin);
      await sleep(1200);
      const ok = await evalJS(CLICK_BY_TEXT("confirm password|confirm pin|^login\\.{0,2}$"));
      await sleep(2000);
      return ok ? "pin-typed" : "pin-wait";
    }
    return "pin-needed";
  }
  if (hasLogin && clientId) {
    // 1) switch to the Client ID tab (radio clientId_rb)
    await evalJS("(function(){var rb=document.querySelector('input[name=loginType][value=clientId_rb]'); if(rb){rb.click();return true;} return false;})()");
    await sleep(800);
    // 2) focus the visible Client ID text input, clear it
    const focused = await evalJS("(function(){var inputs=Array.from(document.querySelectorAll('input')).filter(i=>i.offsetParent!==null&&!i.readOnly&&!i.disabled&&i.type==='text');var t=inputs.find(i=>(i.placeholder||'').toLowerCase().indexOf('client id')>=0);if(!t)return false;t.focus();const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(t,'');t.dispatchEvent(new Event('input',{bubbles:true}));return true;})()");
    // 3) type the client id with real key events
    if (focused) {
      for (const ch of String(clientId)) {
        await send("Input.dispatchKeyEvent", { type: "char", text: ch, key: ch });
        await sleep(50);
      }
    }
    await sleep(1000);
    // 4) click the login-with-client-id button
    const clicked = await evalJS(CLICK_BY_TEXT("login with client id"));
    await sleep(2600);
    return "client-id-typed(" + focused + "," + clicked + ")";
  }
  return "waiting";
}

const FILL_INPUT_BY_PLACEHOLDER = (ph, value) => 
  ("(function(){const inputs=Array.from(document.querySelectorAll('input')).filter(i=>i.offsetParent!==null&&!i.readOnly&&!i.disabled);const t=inputs.find(i=>(i.placeholder||'').toLowerCase().indexOf('" + ph + "')>=0);if(!t)return false;t.focus();const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(t,'" + value + "');t.dispatchEvent(new Event('input',{bubbles:true}));t.dispatchEvent(new Event('change',{bubbles:true}));return true;})()");

const CLICK_BY_TEXT = (re) => 
  ("(function(){const els=Array.from(document.querySelectorAll('button,input[type=submit]'));const b=els.find(e=>new RegExp('" + re + "','i').test(e.textContent||e.value||''));if(!b)return false;b.click();return true;})()");


const PIN_FILLER = (value) => "(function(){var boxes=Array.from(document.querySelectorAll('input[type=number]')).filter(function(i){return i.offsetParent!==null&&!i.readOnly&&!i.disabled&&(i.maxLength===1||i.maxLength===-1);});var digits='"+value+"'.split('');for(var i=0;i<Math.min(digits.length,boxes.length);i++){var t=boxes[i];var s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(t,digits[i]);t.dispatchEvent(new Event('input',{bubbles:true}));}return boxes.length;})()";

const FILL_pinBox = (idx, digit) => 
  ("(function(){const boxes=Array.from(document.querySelectorAll('input[type=number]')).filter(i=>i.offsetParent!==null&&!i.readOnly&&!i.disabled&&(i.maxLength===1||i.maxLength===-1));if(boxes.length<4)return false;const t=boxes[" + idx + "];if(!t)return false;t.focus();const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(t,'" + digit + "');t.dispatchEvent(new Event('input',{bubbles:true}));return true;})()");

async function cmdAuth(appId, secret, redirectUri, timeoutMin, outFile) {
  const clientId = process.env.FYERS_CLIENT_ID || "";
  const pin = process.env.FYERS_PIN || "";
  log("env: clientId=\"" + clientId + "\" pinSet=" + (pin ? "yes" : "no"));
  const appIdHash = crypto.createHash("sha256").update(appId + ":" + secret).digest("hex");
  await launchEdge("about:blank");
  log("waiting for Cloudflare rate limit to clear...");
  await waitCfClear();

  const url = API + "/generate-authcode?client_id=" + encodeURIComponent(appId) +
    "&redirect_uri=" + encodeURIComponent(redirectUri) +
    "&response_type=code&state=dsh1";
  log("navigating to:", url.slice(0, 110) + "...");
  await send("Page.navigate", { url });
  await sleep(4000);

  const deadline = Date.now() + timeoutMin * 60000;
  let code = null;
  let last = "";
  let lastBeat = 0;
  while (Date.now() < deadline) {
    try {
      const st = await state();
      if (Date.now() - lastBeat > 15000) {
        log("heartbeat", new Date().toISOString(), "loc=", st.loc.slice(0, 120), "| body=", st.body.slice(0, 100).replace(/\n/g, " "));
        lastBeat = Date.now();
      }
      // Only treat as a redirect when the host actually changed
      // (the SPA URL keeps query params that contain the redirect URI).
      const host = (() => { try { return new URL(st.loc).hostname; } catch { return ""; } })();
      const atRedirect = host !== "" && host !== "api-t1.fyers.in" && host !== "login.fyers.in" && host !== "myfyers.in";
      if (atRedirect) {
        code = extractCode(st.loc) || extractCode(await evalJS("location.href"));
        if (code) break;
        const alt = await evalJS("(function(){var m=(document.body.innerText||'').match(/auth_code[=:]\s*(\w{10,})/i); return m?m[1]:null;})()");
        if (alt) { code = alt; break; }
        log("at redirect page, no code yet: " + st.loc.slice(0, 160));
      } else {
        const act = await smartLoginWalk(clientId, pin);
        if (act !== last) { log("login walk:", act); last = act; }
      }
    } catch (e) { /* page may be navigating */ }
    await sleep(1200);
  }
  if (!code) throw new Error("no auth_code captured within timeout - login incomplete");
  log("auth_code captured (len " + code.length + ")");

  const exch = await evalJS(`fetch('${API}/validate-authcode', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({grant_type:'authorization_code', appIdHash:'${appIdHash}', code:${JSON.stringify(code)}})
  }).then(r => r.json())`);
  log("exchange:", JSON.stringify(exch).slice(0, 300));
  if (!exch || exch.s !== "ok" || !exch.access_token) throw new Error("exchange failed: " + JSON.stringify(exch).slice(0, 300));

  const tokenData = {
    access_token: exch.access_token,
    refresh_token: exch.refresh_token || null,
    issued_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 22 * 3600 * 1000).toISOString(),
    app_id: appId,
    client_id: clientId,
  };
  fs.writeFileSync(outFile, JSON.stringify(tokenData, null, 2));

  const probe = await evalJS(`fetch('${DATA}/options-chain-v3', {
    method: 'POST', headers: {'Content-Type':'application/json', 'Authorization':'${tokenData.access_token}'},
    body: JSON.stringify({symbol:'NSE:NIFTY50-INDEX', strikecount:3, timestamp:'', greeks:'1'})
  }).then(r => r.json())`);
  log("probe:", JSON.stringify(probe).slice(0, 1200));
  log("TOKENS SAVED -> " + outFile);
}

(async () => {
  const [cmd, a, b, c, d, e] = process.argv.slice(2);
  try {
    if (cmd === "auth") {
      await cmdAuth(a, b, c, parseFloat(d || "10"), e || ".fyers_token.json");
      process.exit(0);
    } else {
      log("usage: node fyers_browser.js auth <app_id> <secret> <redirect_uri> <tout_min> <out>");
      process.exit(2);
    }
  } catch (err) {
    log("FAIL:", err.message);
    process.exit(1);
  }
})();
