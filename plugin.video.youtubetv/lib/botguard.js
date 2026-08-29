// A proof-of-origin token, minted with nothing but node.
//   node botguard.js <visitorData|videoId>  ->  {"token":"...","ttl":43200}
// BotGuard checks for a browser before it will run, so give it one first.
globalThis.window = globalThis;
globalThis.self = globalThis;

// The smallest browser BotGuard will accept. Built by running the VM and
// adding only what it asked for, one error at a time.
const ORIGIN = 'https://tv.youtube.com';

function element(tag) {
  const e = {
    tagName: String(tag || 'div').toUpperCase(),
    nodeName: String(tag || 'div').toUpperCase(),
    nodeType: 1, style: {}, dataset: {}, children: [], childNodes: [],
    attributes: {}, innerHTML: '', textContent: '', className: '', id: '',
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
    removeAttribute(k) { delete this.attributes[k]; },
    hasAttribute(k) { return k in this.attributes; },
    appendChild(c) { this.children.push(c); this.childNodes.push(c); return c; },
    removeChild(c) {
      const i = this.children.indexOf(c);
      if (i >= 0) { this.children.splice(i, 1); this.childNodes.splice(i, 1); }
      return c;
    },
    insertBefore(c) { return this.appendChild(c); },
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    getBoundingClientRect() { return {top:0,left:0,right:0,bottom:0,width:0,height:0,x:0,y:0}; },
    getContext() { return null; },
    contains() { return false; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    focus() {}, blur() {}, click() {}, remove() {},
  };
  return e;
}

const documentElement = element('html');
const body = element('body');
documentElement.appendChild(body);

globalThis.document = {
  documentElement, body, head: element('head'),
  nodeType: 9, readyState: 'complete', visibilityState: 'visible', hidden: false,
  cookie: '', referrer: '', title: '', domain: 'tv.youtube.com',
  URL: ORIGIN + '/', baseURI: ORIGIN + '/',
  location: null,               // filled below
  createElement: element,
  createElementNS: (_ns, tag) => element(tag),
  createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
  createDocumentFragment: () => element('#fragment'),
  getElementById: () => null,
  getElementsByTagName: () => [],
  getElementsByClassName: () => [],
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
  hasFocus: () => true,
};

globalThis.location = {
  href: ORIGIN + '/', origin: ORIGIN, protocol: 'https:', host: 'tv.youtube.com',
  hostname: 'tv.youtube.com', port: '', pathname: '/', search: '', hash: '',
  toString() { return this.href; },
};
globalThis.document.location = globalThis.location;

globalThis.navigator = {
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0',
  appVersion: '5.0 (X11)', appName: 'Netscape', appCodeName: 'Mozilla',
  platform: 'Linux x86_64', product: 'Gecko', productSub: '20100101',
  vendor: '', vendorSub: '', language: 'en-US', languages: ['en-US', 'en'],
  onLine: true, cookieEnabled: true, doNotTrack: null,
  hardwareConcurrency: 8, deviceMemory: 8, maxTouchPoints: 0,
  webdriver: false, plugins: [], mimeTypes: [],
  javaEnabled: () => false,
  sendBeacon: () => true,
  permissions: { query: () => Promise.resolve({ state: 'prompt' }) },
};

globalThis.screen = {
  width: 1920, height: 1080, availWidth: 1920, availHeight: 1080,
  colorDepth: 24, pixelDepth: 24, orientation: { type: 'landscape-primary', angle: 0 },
};

globalThis.history = { length: 1, state: null, pushState() {}, replaceState() {}, go() {}, back() {}, forward() {} };
globalThis.localStorage = globalThis.sessionStorage = {
  _v: {},
  getItem(k) { return k in this._v ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
  removeItem(k) { delete this._v[k]; },
  clear() { this._v = {}; },
  key() { return null; }, get length() { return Object.keys(this._v).length; },
};

globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.dispatchEvent = () => true;
globalThis.matchMedia = () => ({ matches: false, media: '', addListener() {}, removeListener() {},
                                addEventListener() {}, removeEventListener() {} });
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.performance = globalThis.performance || { now: () => Date.now(), timeOrigin: Date.now() };
globalThis.innerWidth = 1920; globalThis.innerHeight = 1080;
globalThis.outerWidth = 1920; globalThis.outerHeight = 1080;
globalThis.devicePixelRatio = 1;
globalThis.origin = ORIGIN;
globalThis.isSecureContext = true;
globalThis.XMLHttpRequest = function () {
  return { open() {}, send() {}, setRequestHeader() {}, addEventListener() {},
           readyState: 0, status: 0, responseText: '' };
};

// Node 19 exposes crypto on the global; Node 18 does not, and BotGuard calls
// getRandomValues. Without this the snapshot comes back as the string
// "E:v is not a function" -- an error where a response should be, which
// GenerateIT then answers with a token anyway, so the failure is silent
// unless the response is checked for its leading "$".
if (!globalThis.crypto) {
  try { globalThis.crypto = require('crypto').webcrypto; } catch (e) { /* older node */ }
}
if (!globalThis.btoa) globalThis.btoa = s => Buffer.from(s, 'binary').toString('base64');
if (!globalThis.atob) globalThis.atob = s => Buffer.from(s, 'base64').toString('binary');
if (!globalThis.structuredClone) globalThis.structuredClone = v => JSON.parse(JSON.stringify(v));

// --- the flow -------------------------------------------------------------
// Ask BotGuard for a challenge, run it, and exchange the result for a
// proof-of-origin token. Verified end to end: a token from here plays where
// a token lifted from a browser capture used to be required.
const https = require('https');
const REQUEST_KEY = 'O43z0dpjhgX20SCx4KAo';
const GOOG = 'https://jnn-pa.googleapis.com/$rpc/google.internal.waa.v1.Waa';
const UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0';

function post(url, payload) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const u = new URL(url);
    const req = https.request({
      hostname: u.hostname, path: u.pathname, method: 'POST',
      headers: {
        'content-type': 'application/json+protobuf',
        'content-length': Buffer.byteLength(body), 'user-agent': UA,
        'x-goog-api-key': 'AIzaSyDyT5W0Jh49F30Pqqtyfdf7pDLFKLJoAnw',
        'x-user-agent': 'grpc-web-javascript/0.1',
      },
    }, res => {
      let out = '';
      res.on('data', d => out += d);
      res.on('end', () => res.statusCode === 200 ? resolve(out)
        : reject(new Error('HTTP ' + res.statusCode + ': ' + out.slice(0, 120))));
    });
    req.on('error', reject);
    req.end(body);
  });
}

const get = url => new Promise((resolve, reject) => {
  https.get(url, { headers: { 'user-agent': UA } }, res => {
    if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode));
    let out = ''; res.on('data', d => out += d); res.on('end', () => resolve(out));
  }).on('error', reject);
});

// Google's own descrambler: base64, then plus 97 to every byte.
const descramble = s =>
  Buffer.from(Buffer.from(s, 'base64').map(b => (b + 97) & 0xFF)).toString('utf8');

async function once(binding) {
  const created = JSON.parse(await post(GOOG + '/Create', [REQUEST_KEY]));
  const raw = created.find(v => typeof v === 'string');
  if (!raw) throw new Error('Create returned no challenge');
  const [, wrappedScript, wrappedUrl, , program, globalName] =
    JSON.parse(descramble(raw));

  let interpreter = Array.isArray(wrappedScript)
    ? wrappedScript.find(v => typeof v === 'string' && v.length > 1000) : null;
  if (!interpreter) {
    let url = Array.isArray(wrappedUrl) ? wrappedUrl.find(v => typeof v === 'string') : wrappedUrl;
    if (!url) throw new Error('no interpreter in the challenge');
    interpreter = await get(url.startsWith('//') ? 'https:' + url : url);
  }
  (0, eval)(interpreter);

  const vm = globalThis[globalName];
  if (!vm || !vm.a) throw new Error('the interpreter did not register ' + globalName);

  const noop = () => {};
  const fns = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('the vm never set itself up')), 30000);
    vm.a(program, (a, s, p, c) => { clearTimeout(t); resolve({ a, s, p, c }); },
         true, undefined, noop, [[], []], undefined, false, [noop, noop, noop, noop, noop]);
  });

  const response = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('the snapshot never returned')), 30000);
    fns.a(r => { clearTimeout(t); resolve(r); }, [undefined, undefined, [], undefined]);
  });

  // A failed snapshot is a string beginning "E:", and GenerateIT answers one
  // with a token anyway -- so the failure is silent unless it is checked for.
  // It fails about one run in three, which is why the caller retries.
  if (!String(response).startsWith('$'))
    throw new Error('snapshot failed: ' + String(response).slice(0, 90));

  const it = JSON.parse(await post(GOOG + '/GenerateIT', [REQUEST_KEY, String(response)]));
  const token = Array.isArray(it) ? it.find(v => typeof v === 'string') : null;
  const ttl = Array.isArray(it) ? it.find(v => typeof v === 'number') : 0;
  if (!token) throw new Error('GenerateIT returned no token');
  return { token, ttl: ttl || 43200 };
}

(async () => {
  const binding = process.argv[2];
  if (!binding) throw new Error('no content binding given');
  let last;
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const { token, ttl } = await once(binding);
      console.log(JSON.stringify({ token, ttl }));
      return;
    } catch (e) {
      last = e;
      console.error('attempt ' + attempt + ': ' + (e && e.message || e));
    }
  }
  throw last;
})().catch(e => { console.error(String(e && e.message || e)); process.exit(1); });
