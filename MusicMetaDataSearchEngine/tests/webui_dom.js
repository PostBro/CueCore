'use strict';
/* Kleiner DOM-Ersatz für Oberflächen-Tests: lädt web/index.html, baut den Body als Baum, führt das
   Inline-Skript in einem vm-Kontext aus (fetch/localStorage/location gestubbt) und dann ein Szenario.
   Kein jsdom, keine Pakete — nur so viel DOM, wie index.html tatsächlich benutzt.
   Aufruf: node webui_dom.js <config.json>; Ausgabe: eine JSON-Zeile {result, errors, fetches}.
   config: {index, scenario, lang, storage:{k:v}, search, api:{"/pfad": antwort}, confirm} —
   antwort ist der JSON-Body oder {__status, __body} oder {__pending:true} (antwortet nie, außer mit AbortError,
   wenn das Signal der Anfrage abgebrochen wird). */
const fs = require('fs');
const vm = require('vm');

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
const RAW = new Set(['script', 'style']);
const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', '#39': "'" };
const decode = s => s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
  if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
  return ENT[e.toLowerCase()] ?? m;
});
const escText = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/ /g, '&nbsp;');
const escAttr = s => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
const kebab = p => 'data-' + p.replace(/[A-Z]/g, c => '-' + c.toLowerCase());

class Event {
  constructor(type, opts) { opts = opts || {}; this.type = type; this.bubbles = !!opts.bubbles; this.cancelable = !!opts.cancelable;
    this.defaultPrevented = false; this._stop = false; this.target = null; this.currentTarget = null; Object.assign(this, opts.init || {}); }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this._stop = true; }
  stopImmediatePropagation() { this._stop = true; }
}

class Target {
  constructor() { this._ls = {}; }
  addEventListener(type, fn, opts) { if (!fn) return; (this._ls[type] = this._ls[type] || []).push({ fn, once: !!(opts && opts.once) }); }
  removeEventListener(type, fn) { this._ls[type] = (this._ls[type] || []).filter(l => l.fn !== fn); }
  _fire(ev) {
    ev.currentTarget = this;
    for (const l of (this._ls[ev.type] || []).slice()) {
      if (l.once) this.removeEventListener(ev.type, l.fn);
      try { l.fn.call(this, ev); } catch (e) { ERRORS.push('listener ' + ev.type + ': ' + (e && e.stack || e)); }
      if (ev._stop) break;
    }
    const on = this['on' + ev.type];
    if (typeof on === 'function') { try { on.call(this, ev); } catch (e) { ERRORS.push('on' + ev.type + ': ' + (e && e.stack || e)); } }
  }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    const path = [];
    for (let n = this; n; n = n.parentNode || (n === DOC ? WIN : null)) { path.push(n); if (n === WIN) break; }
    for (const n of (ev.bubbles ? path : [this])) { n._fire(ev); if (ev._stop) break; }
    return !ev.defaultPrevented;
  }
}

class Node extends Target {
  constructor() { super(); this.parentNode = null; this.childNodes = []; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() { const p = this.parentNode; if (!p) return null; return p.childNodes[p.childNodes.indexOf(this) + 1] || null; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === DOC; }
  _adopt(n) {
    if (typeof n === 'string') n = new Text(n);
    if (n.nodeType === 11) return n.childNodes.splice(0).map(c => { c.parentNode = null; return c; });
    if (n.parentNode) n.parentNode.removeChild(n);
    return [n];
  }
  appendChild(n) { for (const c of this._adopt(n)) { c.parentNode = this; this.childNodes.push(c); } return n; }
  insertBefore(n, ref) {
    if (!ref) return this.appendChild(n);
    for (const c of this._adopt(n)) { c.parentNode = this; this.childNodes.splice(this.childNodes.indexOf(ref), 0, c); }
    return n;
  }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; } return n; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  append(...ns) { ns.forEach(n => this.appendChild(n)); }
  prepend(...ns) { const first = this.firstChild; ns.forEach(n => this.insertBefore(n, first)); }
  contains(n) { for (; n; n = n.parentNode) if (n === this) return true; return false; }
}

class Text extends Node {
  constructor(data) { super(); this.nodeType = 3; this.data = String(data); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  get nodeValue() { return this.data; }
}

class Element extends Node {
  constructor(tag) {
    super(); this.nodeType = 1; this.localName = tag.toLowerCase(); this.tagName = tag.toUpperCase(); this.attrs = new Map();
    this.style = {}; this.scrollLeft = 0; this.scrollTop = 0;
    const self = this;
    this.dataset = new Proxy({}, {
      get: (_, p) => typeof p === 'string' ? (self.getAttribute(kebab(p)) ?? undefined) : undefined,
      set: (_, p, v) => { self.setAttribute(kebab(p), v); return true; },
      deleteProperty: (_, p) => { self.removeAttribute(kebab(p)); return true; },
      has: (_, p) => self.hasAttribute(kebab(p)),
    });
  }
  getAttribute(n) { n = n.toLowerCase(); return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n.toLowerCase(), String(v)); }
  removeAttribute(n) { this.attrs.delete(n.toLowerCase()); }
  hasAttribute(n) { return this.attrs.has(n.toLowerCase()); }
  toggleAttribute(n, on) { on = on === undefined ? !this.hasAttribute(n) : on; on ? this.setAttribute(n, '') : this.removeAttribute(n); return on; }
  _bool(n, v) { v ? this.setAttribute(n, '') : this.removeAttribute(n); }
  get id() { return this.getAttribute('id') || ''; } set id(v) { this.setAttribute('id', v); }
  get className() { return this.getAttribute('class') || ''; } set className(v) { this.setAttribute('class', v); }
  get classList() {
    const self = this, get = () => self.className.split(/\s+/).filter(Boolean), put = a => self.setAttribute('class', a.join(' '));
    return {
      add: (...c) => put([...new Set([...get(), ...c])]),
      remove: (...c) => put(get().filter(x => !c.includes(x))),
      contains: c => get().includes(c),
      toggle: (c, on) => { const has = get().includes(c); on = on === undefined ? !has : !!on; on ? put([...new Set([...get(), c])]) : put(get().filter(x => x !== c)); return on; },
    };
  }
  get hidden() { return this.hasAttribute('hidden'); } set hidden(v) { this._bool('hidden', v); }
  get disabled() { return this.hasAttribute('disabled'); } set disabled(v) { this._bool('disabled', v); }
  get readOnly() { return this.hasAttribute('readonly'); } set readOnly(v) { this._bool('readonly', v); }
  get checked() { return this._checked ?? this.hasAttribute('checked'); } set checked(v) { this._checked = !!v; }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.localName === 'select') { const o = this.querySelector('option'); return o ? (o.getAttribute('value') ?? o.textContent) : ''; }
    if (this.localName === 'textarea') return this.textContent;
    return this.getAttribute('value') ?? '';
  }
  set value(v) { this._value = v == null ? '' : String(v); }
  get files() { return this._files || { length: 0 }; } set files(v) { this._files = v; }
  get tabIndex() { return +(this.getAttribute('tabindex') ?? -1); } set tabIndex(v) { this.setAttribute('tabindex', v); }
  get colSpan() { return +(this.getAttribute('colspan') ?? 1); } set colSpan(v) { this.setAttribute('colspan', v); }
  get children() { return this.childNodes.filter(c => c.nodeType === 1); }
  get firstElementChild() { return this.children[0] || null; }
  get textContent() { return this.childNodes.map(c => c.nodeType === 8 ? '' : c.textContent).join(''); }
  set textContent(v) { this.childNodes.forEach(c => { c.parentNode = null; }); this.childNodes = []; if (v != null && String(v) !== '') this.appendChild(new Text(v)); }
  get innerText() { return this.textContent; } set innerText(v) { this.textContent = v; }
  get innerHTML() { return this.childNodes.map(serialize).join(''); }
  set innerHTML(v) { this.textContent = ''; parseInto(this, String(v ?? '')); }
  get outerHTML() { return serialize(this); }
  get scrollWidth() { return 0; } get clientWidth() { return 0; }
  getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }; }
  focus() { DOC.activeElement = this; this.dispatchEvent(new Event('focus')); this.dispatchEvent(new Event('focusin', { bubbles: true })); }
  blur() { if (DOC.activeElement === this) DOC.activeElement = DOC.body; }
  scrollIntoView() {}
  click() { if (this.disabled) return; this.dispatchEvent(new Event('click', { bubbles: true, cancelable: true })); }
  requestSubmit() { this.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); }
  matches(sel) { return parseSel(sel).some(cx => matchComplex(this, cx, cx.length - 1)); }
  closest(sel) { for (let n = this; n && n.nodeType === 1; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel) { const cxs = parseSel(sel), out = []; walk(this, e => { if (cxs.some(cx => matchComplex(e, cx, cx.length - 1))) out.push(e); }); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
for (const p of ['title', 'placeholder', 'type', 'href', 'target', 'rel', 'lang', 'alt', 'src', 'name', 'autocomplete', 'inputMode']) {
  Object.defineProperty(Element.prototype, p, { get() { return this.getAttribute(p) ?? ''; }, set(v) { this.setAttribute(p, v); } });
}

function walk(root, fn) { for (const c of root.childNodes) if (c.nodeType === 1) { fn(c); walk(c, fn); } }
function serialize(n) {
  if (n.nodeType === 3) return escText(n.data);
  if (n.nodeType !== 1) return '';
  const a = [...n.attrs].map(([k, v]) => v === '' ? ' ' + k : ` ${k}="${escAttr(v)}"`).join('');
  if (VOID.has(n.localName)) return `<${n.localName}${a}>`;
  return `<${n.localName}${a}>${n.childNodes.map(serialize).join('')}</${n.localName}>`;
}

/* ---- HTML-Parser (für index.html und innerHTML-Fragmente) ---- */
function parseInto(root, html) {
  const stack = [root]; let i = 0;
  const top = () => stack[stack.length - 1];
  while (i < html.length) {
    const lt = html.indexOf('<', i);
    if (lt < 0) { top().appendChild(new Text(decode(html.slice(i)))); break; }
    if (lt > i) top().appendChild(new Text(decode(html.slice(i, lt))));
    if (html.startsWith('<!--', lt)) { const e = html.indexOf('-->', lt); i = e < 0 ? html.length : e + 3; continue; }
    if (html[lt + 1] === '!') { i = html.indexOf('>', lt) + 1; continue; }
    if (html[lt + 1] === '/') {
      const e = html.indexOf('>', lt); const name = html.slice(lt + 2, e).trim().toLowerCase();
      for (let k = stack.length - 1; k > 0; k--) if (stack[k].localName === name) { stack.length = k; break; }
      i = e + 1; continue;
    }
    const m = /^<([a-zA-Z][\w-]*)/.exec(html.slice(lt));
    if (!m) { top().appendChild(new Text('<')); i = lt + 1; continue; }
    const el = new Element(m[1]); let j = lt + m[0].length, selfClose = false;
    for (;;) {
      while (/\s/.test(html[j])) j++;
      if (html[j] === '>') { j++; break; }
      if (html.startsWith('/>', j)) { selfClose = true; j += 2; break; }
      const am = /^([^\s=>\/]+)(?:\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+)))?/.exec(html.slice(j));
      if (!am) { j++; continue; }
      el.setAttribute(am[1], am[2] === undefined ? '' : decode(am[3] ?? am[4] ?? am[5] ?? ''));
      j += am[0].length;
    }
    top().appendChild(el); i = j;
    if (RAW.has(el.localName)) {
      const e = html.toLowerCase().indexOf('</' + el.localName, i);
      el.appendChild(new Text(html.slice(i, e < 0 ? html.length : e)));
      i = e < 0 ? html.length : html.indexOf('>', e) + 1; continue;
    }
    if (!selfClose && !VOID.has(el.localName)) stack.push(el);
  }
}

/* ---- Selektoren: tag, #id, .klasse, [attr], [attr=wert], :not(…), Nachfahre, >, Liste ---- */
const SEL_CACHE = new Map();
function parseSel(sel) {
  if (SEL_CACHE.has(sel)) return SEL_CACHE.get(sel);
  const out = []; let i = 0; const s = sel.trim();
  let cx = [], comp = newComp(), comb = null, any = false;
  function newComp() { return { tag: null, id: null, cls: [], attrs: [], nots: [] }; }
  const push = () => { if (any) { cx.push({ comp, comb }); comp = newComp(); any = false; comb = ' '; } };
  while (i < s.length) {
    const c = s[i];
    if (c === ',') { push(); out.push(cx); cx = []; comb = null; i++; while (s[i] === ' ') i++; continue; }
    if (c === ' ' || c === '>') {
      while (s[i] === ' ') i++;
      if (s[i] === '>') { i++; while (s[i] === ' ') i++; push(); comb = '>'; continue; }
      if (s[i] === ',') continue;
      push(); continue;
    }
    if (c === '#' || c === '.') { const m = /^[\w-]+/.exec(s.slice(i + 1)); (c === '#' ? (comp.id = m[0]) : comp.cls.push(m[0])); i += 1 + m[0].length; any = true; continue; }
    if (c === '[') {
      const e = s.indexOf(']', i); const body = s.slice(i + 1, e);
      const m = /^\s*([\w-]+)\s*(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*$/.exec(body);
      comp.attrs.push({ name: m[1].toLowerCase(), op: m[2] || null, val: m[3] ?? m[4] ?? m[5] ?? null }); i = e + 1; any = true; continue;
    }
    if (s.startsWith(':not(', i)) {
      let d = 0, j = i + 4; for (; j < s.length; j++) { if (s[j] === '(') d++; else if (s[j] === ')' && --d === 0) break; }
      comp.nots.push(parseSel(s.slice(i + 5, j))); i = j + 1; any = true; continue;
    }
    if (c === '*') { i++; any = true; continue; }
    const m = /^[a-zA-Z][\w-]*/.exec(s.slice(i));
    if (!m) throw new Error('Selektor nicht unterstützt: ' + sel);
    comp.tag = m[0].toLowerCase(); i += m[0].length; any = true;
  }
  push(); out.push(cx);
  out.forEach(x => { if (x.length) x[0].comb = null; });
  SEL_CACHE.set(sel, out); return out;
}
function matchComp(e, c) {
  if (c.tag && e.localName !== c.tag) return false;
  if (c.id && e.id !== c.id) return false;
  if (c.cls.length) { const have = e.className.split(/\s+/); if (!c.cls.every(x => have.includes(x))) return false; }
  for (const a of c.attrs) {
    const v = e.getAttribute(a.name); if (v === null) return false;
    if (a.op === '=' && v !== a.val) return false;
    if (a.op === '^=' && !v.startsWith(a.val)) return false;
    if (a.op === '*=' && !v.includes(a.val)) return false;
  }
  for (const n of c.nots) if (n.some(cx => matchComplex(e, cx, cx.length - 1))) return false;
  return true;
}
function matchComplex(e, cx, i) {
  if (!matchComp(e, cx[i].comp)) return false;
  if (i === 0) return true;
  if (cx[i].comb === '>') { const p = e.parentElement; return !!p && matchComplex(p, cx, i - 1); }
  for (let p = e.parentElement; p; p = p.parentElement) if (matchComplex(p, cx, i - 1)) return true;
  return false;
}

/* ---- Dokument + Fenster ---- */
class Document extends Node {
  constructor() { super(); this.nodeType = 9; this.activeElement = null; }
  get documentElement() { return this.childNodes.find(c => c.nodeType === 1) || null; }
  get head() { return this.querySelector('head'); }
  get body() { return this.querySelector('body'); }
  createElement(t) { return new Element(t); }
  createTextNode(s) { return new Text(s); }
  createDocumentFragment() { const f = new Node(); f.nodeType = 11; return f; }
  getElementById(id) { return this.querySelector('#' + id); }
  querySelectorAll(sel) { return Element.prototype.querySelectorAll.call(this, sel); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  createRange() { return { selectNodeContents() {} }; }
}

const ERRORS = [], FETCHES = [];
let DOC, WIN;

async function main() {
  const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const html = fs.readFileSync(cfg.index, 'utf8');
  DOC = new Document();
  parseInto(DOC, html.replace(/^<!DOCTYPE[^>]*>/i, ''));
  DOC.activeElement = DOC.body;
  const scripts = DOC.querySelectorAll('script').filter(s => !s.hasAttribute('src'));

  const store = new Map(Object.entries(cfg.storage || {}));
  const localStorage = {
    getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k), clear: () => store.clear(), get length() { return store.size; },
  };
  if (cfg.lang) store.set('cuecore_lang', cfg.lang);
  const api = Object.assign({
    '/api/health': { ready: true, rows: 85224, songs: 66423, source_version: '2026-09-15', sources: {}, index: { present: false } },
    '/api/settings': { groups: [], paths: [{ key: 'lc_verified_dir', value: '/Users/x/LC verified', group: 'ordner', label: 'LC-verified-Ordner', hint: '' }],
      texts: [], keys: [], configured: {}, partners: [], partner_fields: {} },
    '/api/labels/lists': { files: [], shipped: 12, attached: 0 },
    '/api/index/status': { present: false },
    '/api/db/songs': { total: 0, total_all: 0, rows: [], index_rows: [], missing_counts: {}, index_present: false },
  }, cfg.api || {});

  WIN = new Target();
  const sandbox = {
    document: DOC, localStorage, sessionStorage: localStorage, console: {
      log: () => {}, info: () => {}, debug: () => {},
      warn: (...a) => ERRORS.push('console.warn: ' + a.map(String).join(' ')),
      error: (...a) => ERRORS.push('console.error: ' + a.map(String).join(' ')),
    },
    location: { search: cfg.search || '', href: 'http://127.0.0.1/' + (cfg.search || ''), origin: 'http://127.0.0.1', pathname: '/' },
    navigator: { clipboard: { writeText: async () => {} }, language: 'de-DE' },
    setTimeout: (f, ms, ...a) => setTimeout(() => { try { f(...a); } catch (e) { ERRORS.push('timer: ' + (e && e.stack || e)); } }, ms),
    clearTimeout, setInterval, clearInterval, setImmediate, queueMicrotask,
    requestAnimationFrame: f => setTimeout(() => f(Date.now()), 0),
    URLSearchParams, URL, Blob, TextEncoder, TextDecoder, Event, CustomEvent: Event, AbortController,
    alert: m => { sandbox.__alerts.push(String(m)); }, confirm: m => { sandbox.__alerts.push(String(m)); return cfg.confirm !== false; },
    prompt: () => null, __alerts: [], __api: api,
    addEventListener: (t, f, o) => WIN.addEventListener(t, f, o), removeEventListener: (t, f) => WIN.removeEventListener(t, f),
    dispatchEvent: e => WIN.dispatchEvent(e),
    getComputedStyle: e => e.style,
    matchMedia: q => { const m = /max-width:\s*(\d+)px/.exec(q); return { matches: !!m && (cfg.width || 1400) <= +m[1], addEventListener() {}, addListener() {} }; },
    innerWidth: cfg.width || 1400, innerHeight: 900,
    FileReader: class { readAsDataURL() {} readAsText() {} },
    DataTransfer: class { constructor() { this.items = { add() {} }; this.files = { length: 0 }; } },
  };
  sandbox.fetch = (url, opts) => {
    const u = new URL(String(url), 'http://127.0.0.1');
    FETCHES.push({ path: u.pathname, search: u.search, method: (opts && opts.method) || 'GET', body: opts && opts.body || null });
    let spec = api[(opts && opts.method || 'GET') + ' ' + u.pathname] ?? api[u.pathname];
    if (typeof spec === 'function') spec = spec(u, opts);
    if (spec === undefined) spec = { __status: 404, __body: { detail: 'Not Found' } };
    // Abbrechen wie im Browser: ein abgebrochenes Signal weist die Anfrage mit AbortError ab (auch eine hängende)
    const signal = opts && opts.signal, aborted = () => Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' });
    if (signal && signal.aborted) return Promise.reject(aborted());
    if (spec && spec.__pending) return new Promise((_, rej) => { if (signal) signal.addEventListener('abort', () => rej(aborted())); });
    if (spec && spec.__throw) return new Promise((_, rej) => setImmediate(() => rej(new TypeError('Failed to fetch'))));
    const status = (spec && spec.__status) || 200, body = spec && '__body' in spec ? spec.__body : spec;
    return new Promise(res => setImmediate(() => res({
      ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(body)),
      text: async () => JSON.stringify(body), blob: async () => new Blob([JSON.stringify(body)]),
    })));
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext('var window = globalThis, self = globalThis;', ctx);
  process.on('unhandledRejection', e => ERRORS.push('unhandledRejection: ' + (e && e.stack || e)));

  const settle = async (n = 30) => { for (let k = 0; k < n; k++) await new Promise(r => setImmediate(r)); };
  sandbox.settle = settle;
  sandbox.sleep = ms => new Promise(r => setTimeout(r, ms));
  sandbox.qa = s => DOC.querySelectorAll(s);
  sandbox.txt = s => { const e = typeof s === 'string' ? DOC.querySelector(s) : s; return e ? e.textContent.replace(/\s+/g, ' ').trim() : null; };
  sandbox.texts = s => DOC.querySelectorAll(s).map(e => e.textContent.replace(/\s+/g, ' ').trim());
  sandbox.click = s => { const e = typeof s === 'string' ? DOC.querySelector(s) : s; if (!e) throw new Error('nicht gefunden: ' + s); e.click(); };
  sandbox.fire = (s, type, init) => { const e = typeof s === 'string' ? DOC.querySelector(s) : s; e.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, init })); };
  sandbox.visibleText = () => { const parts = []; (function w(n, hid) { for (const c of n.childNodes) {
    if (c.nodeType === 3) { if (!hid && c.data.trim()) parts.push(c.data.trim()); }
    else if (c.nodeType === 1 && !RAW.has(c.localName)) w(c, hid || c.hidden || (c.style && c.style.display === 'none'));
  } })(DOC.body, false); return parts; };
  sandbox.attrTexts = () => { const out = []; walk(DOC.body, e => { if (e.closest('[hidden]')) return;
    for (const a of ['title', 'placeholder', 'aria-label', 'data-tip']) { const v = e.getAttribute(a); if (v) out.push(v); } }); return out; };

  try {
    for (const s of scripts) vm.runInContext(s.textContent, ctx, { filename: 'index.html' });
  } catch (e) { ERRORS.push('script: ' + (e && e.stack || e)); }
  await settle();
  let result = null;
  try {
    result = await vm.runInContext('(async () => {\n' + (cfg.scenario || 'return null;') + '\n})()', ctx, { filename: 'scenario.js' });
  } catch (e) { ERRORS.push('scenario: ' + (e && e.stack || e)); }
  process.stdout.write(JSON.stringify({ result: result === undefined ? null : result, errors: ERRORS, fetches: FETCHES, alerts: sandbox.__alerts }) + '\n');
  process.exit(0);
}
main();
