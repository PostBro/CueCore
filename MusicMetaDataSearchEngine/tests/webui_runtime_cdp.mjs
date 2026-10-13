// CDP-Treiber für tests/test_webui_runtime.py (node, ohne Pakete): lädt jeden Tab in DE und EN, wechselt
// je Sprache einmal über den Knopf in der Rail und klickt danach jeden Tab durch. Gesammelt wird jede
// Exception, auch die, die ein catch verschluckt (Debugger pausiert auf allen Exceptions), jeder
// console.error und jede Fehlerzeile im Log (z.B. 500 vom Backend). Zum Schluss öffnet er den Editor der
// Track-Datenbank; je Schritt hält er Elemente fest, die hidden tragen und trotzdem gerendert werden.
// Ausgabe: JSON auf stdout.
//   node webui_runtime_cdp.mjs <basis-url> <devtools-port>
const [, , BASE, PORT] = process.argv;
const TOOLS = ['home', 'resolve', 'tagger', 'edl', 'db', 'gvl', 'settings'];

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = targets.find(t => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((ok, err) => { ws.onopen = ok; ws.onerror = err; });

let seq = 0;
const pending = new Map();
const errors = [];
const inflight = new Set();
let where = 'start', loaded = false, lastNet = Date.now();
const send = (method, params = {}) => new Promise((ok, err) => {
  const id = ++seq; pending.set(id, { ok, err }); ws.send(JSON.stringify({ id, method, params }));
});
ws.onmessage = ev => {
  const m = JSON.parse(ev.data), p = m.params;
  if (m.id) {
    const h = pending.get(m.id); pending.delete(m.id);
    if (h) m.error ? h.err(new Error(`${JSON.stringify(m.error)}`)) : h.ok(m.result);
    return;
  }
  switch (m.method) {
    case 'Debugger.paused':         // reason 'exception' oder 'promiseRejection', gefangen oder nicht
      errors.push({ where, kind: p.reason, text: (p.data && (p.data.description || p.data.value)) || '' });
      send('Debugger.resume');
      break;
    case 'Runtime.exceptionThrown':
      errors.push({ where, kind: 'uncaught', text: p.exceptionDetails.exception?.description || p.exceptionDetails.text });
      break;
    case 'Runtime.consoleAPICalled':
      if (p.type === 'error' || p.type === 'assert')
        errors.push({ where, kind: `console.${p.type}`, text: p.args.map(a => a.value ?? a.description).join(' ') });
      break;
    case 'Log.entryAdded':          // Netzfehler (4xx/5xx), CSP u.ä.; das fehlende favicon.ico ist kein Befund
      if (p.entry.level === 'error' && !/\/favicon\.ico$/.test(p.entry.url || ''))
        errors.push({ where, kind: `log.${p.entry.source}`, text: `${p.entry.text} ${p.entry.url || ''}`.trim() });
      break;
    case 'Network.requestWillBeSent': inflight.add(p.requestId); lastNet = Date.now(); break;
    case 'Network.loadingFinished':
    case 'Network.loadingFailed': inflight.delete(p.requestId); lastNet = Date.now(); break;
    case 'Page.loadEventFired': loaded = true; break;
  }
};

const sleep = ms => new Promise(r => setTimeout(r, ms));
// Ruhe = Seite geladen, kein Request offen, 400 ms nichts Neues (renderHome holt nacheinander, nicht parallel).
async function settle(timeout = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    if (loaded && inflight.size === 0 && Date.now() - lastNet > 400) return;
    await sleep(50);
  }
  errors.push({ where, kind: 'timeout', text: `nach ${timeout} ms noch ${inflight.size} Request(s) offen` });
}
async function ev(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(`${where}: ${expression}: ${r.exceptionDetails.text}`);
  return r.result.value;
}
async function go(url) { loaded = false; lastNet = Date.now(); await send('Page.navigate', { url }); await settle(); }
async function click(selector) { await ev(`document.querySelector(${JSON.stringify(selector)}).click(), true`); lastNet = Date.now(); await settle(); }

// Elemente mit hidden-Attribut, die trotzdem gerendert werden: eine Klasse mit display:flex/grid schlägt das
// [hidden] des Browsers (Regeln der Seite gehen vor, egal wie spezifisch) — so stand unter dem Editor ein leerer Kasten.
const HIDDEN_SHOWN = `[...document.querySelectorAll('[hidden]')].filter(e => getComputedStyle(e).display !== 'none')
  .map(e => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className ? '.' + String(e.className).trim().split(/\\s+/).join('.') : ''))`;

const steps = [];
async function record(lang, tool, via) {
  steps.push({ lang, tool, via, where, ...await ev(`(() => {
    const txt = s => { const e = document.querySelector(s); return e ? e.textContent.trim() : null; };
    return {
      hiddenShown: ${HIDDEN_SHOWN},
      shown: [...document.querySelectorAll('section[id^="tool-"]')].filter(s => !s.hidden).map(s => s.id),
      langbtn: txt('#langbtn'),
      rail: { ver: txt('#st-ver'), rows: txt('#st-rows'), songs: txt('#st-songs'), index: txt('#st-index'), src: txt('#st-src') },
      home: [...document.querySelectorAll('#homehealth > *')].map(r => [...r.querySelectorAll('.st')].map(x => x.textContent.trim()).join(' | ') || r.textContent.trim()),
    };
  })()`) });
}

await send('Page.enable');
await send('Runtime.enable');
await send('Log.enable');
await send('Network.enable');
await send('Debugger.enable');
await send('Debugger.setPauseOnExceptions', { state: 'all' });

for (const lang of ['de', 'en']) {
  where = `${lang}: vorbereiten`;
  await go(`${BASE}/api/health`);
  await ev(`localStorage.clear(); localStorage.setItem('cuecore_lang', '${lang}'); localStorage.setItem('cuecore_onboarded', '1'); true`);
  for (const tool of TOOLS) {
    where = `${lang}: Tab ${tool} geladen`;
    await go(tool === 'home' ? `${BASE}/` : `${BASE}/?tool=${tool}`);
    await record(lang, tool, 'load');
  }
  const other = lang === 'de' ? 'en' : 'de';
  where = `${lang}: zurück zum Start`;
  await click('.nav button[data-tool="home"]');
  where = `${lang} -> ${other}: Sprachknopf`;
  await click('#langbtn');
  await record(other, 'home', 'switch');
  for (const tool of TOOLS) {
    where = `${lang} -> ${other}: Tab ${tool} geklickt`;
    await click(`.nav button[data-tool="${tool}"]`);
    await record(other, tool, 'click');
  }
}

// Editor der Track-Datenbank (Doppelklick auf die erste Zeile des Seeds): sein Kasten für die Auto-Suche trägt
// hidden, bis gesucht wurde
where = 'Editor der Track-Datenbank';
await go(`${BASE}/?tool=db`);
const editor = await ev(`(() => {
  const row = document.querySelector('#dbresult tr.etrow');
  if (row) row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
  return { opened: !!document.querySelector('.emodal .dbedit'), hiddenShown: ${HIDDEN_SHOWN} };
})()`);

ws.close();
console.log(JSON.stringify({ steps, errors, editor }));
