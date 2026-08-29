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
