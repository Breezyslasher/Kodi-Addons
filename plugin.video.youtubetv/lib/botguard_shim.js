// The browser BotGuard needs, in ES5 so a pure-Python interpreter can
// parse it, and installed on the global object explicitly.
//
// Not with `var`: node 21 and later expose `navigator` as a read-only
// accessor, so `var navigator = {...}` at eval scope is silently ignored
// and the VM goes on reading Node.js/22 as the user agent. Every name here
// is defined rather than declared, so the same file behaves the same way
// under node and under a pure-Python interpreter.
var __global = (function () { return this; })() || this;
function __define(name, value) {
  try {
    Object.defineProperty(__global, name,
                          {value: value, writable: true, configurable: true});
  } catch (e) {
    try { __global[name] = value; } catch (e2) {}
  }
}
// The same browser lib/botguard.js builds, written in ES5 so a pure-Python
// interpreter can parse it. No arrow functions, no shorthand methods, no
// getters in object literals, no const/let.
var ORIGIN = 'https://tv.youtube.com';
function element(tag) {
  var name = String(tag || 'div').toUpperCase();
  return {
    tagName: name, nodeName: name, nodeType: 1, style: {}, dataset: {},
    children: [], childNodes: [], attributes: {}, innerHTML: '',
    textContent: '', className: '', id: '',
    setAttribute: function (k, v) { this.attributes[k] = String(v); },
    getAttribute: function (k) { return k in this.attributes ? this.attributes[k] : null; },
    removeAttribute: function (k) { delete this.attributes[k]; },
    hasAttribute: function (k) { return k in this.attributes; },
    appendChild: function (c) { this.children.push(c); this.childNodes.push(c); return c; },
    removeChild: function (c) { return c; },
    insertBefore: function (c) { return this.appendChild(c); },
    addEventListener: function () {}, removeEventListener: function () {},
    dispatchEvent: function () { return true; },
    getBoundingClientRect: function () {
      return {top:0,left:0,right:0,bottom:0,width:0,height:0,x:0,y:0}; },
    getContext: function () { return null; },
    contains: function () { return false; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    focus: function () {}, blur: function () {}, click: function () {},
    remove: function () {}
  };
}
var __documentElement = element('html'), __body = element('body');
__documentElement.appendChild(__body);
var __document = {
  documentElement: __documentElement, body: __body, head: element('head'),
  nodeType: 9, readyState: 'complete', visibilityState: 'visible', hidden: false,
  cookie: '', referrer: '', title: '', domain: 'tv.youtube.com',
  URL: ORIGIN + '/', baseURI: ORIGIN + '/', location: null,
  createElement: element,
  createElementNS: function (ns, tag) { return element(tag); },
  createTextNode: function (t) { return {nodeType: 3, textContent: String(t)}; },
  createDocumentFragment: function () { return element('#fragment'); },
  getElementById: function () { return null; },
  getElementsByTagName: function () { return []; },
  getElementsByClassName: function () { return []; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {}, removeEventListener: function () {},
  dispatchEvent: function () { return true; },
  hasFocus: function () { return true; }
};
var __location = {
  href: ORIGIN + '/', origin: ORIGIN, protocol: 'https:', host: 'tv.youtube.com',
  hostname: 'tv.youtube.com', port: '', pathname: '/', search: '', hash: '',
  toString: function () { return this.href; }
};
__document.location = __location;
var __navigator = {
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0',
  appVersion: '5.0 (X11)', appName: 'Netscape', appCodeName: 'Mozilla',
  platform: 'Linux x86_64', product: 'Gecko', productSub: '20100101',
  vendor: '', vendorSub: '', language: 'en-US', languages: ['en-US', 'en'],
  onLine: true, cookieEnabled: true, doNotTrack: null,
  hardwareConcurrency: 8, deviceMemory: 8, maxTouchPoints: 0,
  webdriver: false, plugins: [], mimeTypes: [],
  javaEnabled: function () { return false; },
  sendBeacon: function () { return true; }
};
var __screen = {width: 1920, height: 1080, availWidth: 1920, availHeight: 1080,
              colorDepth: 24, pixelDepth: 24,
              orientation: {type: 'landscape-primary', angle: 0}};
var __history = {length: 1, state: null, pushState: function () {},
               replaceState: function () {}, go: function () {},
               back: function () {}, forward: function () {}};
var __localStorage = {_v: {},
  getItem: function (k) { return k in this._v ? this._v[k] : null; },
  setItem: function (k, v) { this._v[k] = String(v); },
  removeItem: function (k) { delete this._v[k]; },
  clear: function () { this._v = {}; }, key: function () { return null; },
  length: 0};
var __sessionStorage = __localStorage;
function addEventListener() {}
function removeEventListener() {}
function dispatchEvent() { return true; }
function matchMedia() {
  return {matches: false, media: '', addListener: function () {},
          removeListener: function () {}, addEventListener: function () {},
          removeEventListener: function () {}};
}
var __performance = {now: function () { return Date.now(); },
                   timeOrigin: Date.now()};
var __innerWidth = 1920, __innerHeight = 1080, __outerWidth = 1920, __outerHeight = 1080;
var __devicePixelRatio = 1, __origin = ORIGIN, __isSecureContext = true;
function XMLHttpRequest() {
  return {open: function () {}, send: function () {},
          setRequestHeader: function () {}, addEventListener: function () {},
          readyState: 0, status: 0, responseText: ''};
}
// No WebCrypto in a pure-Python interpreter. BotGuard calls getRandomValues
// and fails silently without it -- the snapshot comes back "E:v is not a
// function" -- so give it one seeded by the host.
// Real entropy is handed in from Python; Math.random is the fallback so
// the shim still works if nothing seeds it.
var __entropy = [], __entropyAt = 0;
function __seedEntropy(bytes) { __entropy = bytes; __entropyAt = 0; }
var __crypto = {
  getRandomValues: function (a) {
    for (var i = 0; i < a.length; i++)
      a[i] = __entropyAt < __entropy.length
        ? __entropy[__entropyAt++] : Math.floor(Math.random() * 256);
    return a;
  }
};
// Timers, run to completion by the driver rather than by an event loop.
var __timers = [], __timerId = 0, __drainErrors = [];
function setTimeout(fn, ms) { __timers.push(fn); return ++__timerId; }
function clearTimeout(id) {}
function setInterval() { return ++__timerId; }
function clearInterval(id) {}
function requestAnimationFrame(cb) { return setTimeout(cb, 0); }
function cancelAnimationFrame(id) {}
function __drain(limit) {
  var n = 0;
  while (__timers.length && n < (limit || 5000)) {
    var fn = __timers.shift();
    n++;
    try { fn(); } catch (e) { __drainErrors.push(String(e && e.message || e)); }
  }
  return n;
}
// base64, in JS, so nothing crosses the Python boundary where a js2py
// String would arrive as an object Python cannot concatenate.
var __b64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
function btoa(input) {
  var s = String(input), out = '', i = 0, a, b, c;
  while (i < s.length) {
    a = s.charCodeAt(i++); b = s.charCodeAt(i++); c = s.charCodeAt(i++);
    out += __b64.charAt(a >> 2);
    out += __b64.charAt(((a & 3) << 4) | ((isNaN(b) ? 0 : b) >> 4));
    out += isNaN(b) ? '=' : __b64.charAt(((b & 15) << 2) | ((isNaN(c) ? 0 : c) >> 6));
    out += isNaN(c) ? '=' : __b64.charAt(c & 63);
  }
  return out;
}
function atob(input) {
  var s = String(input).replace(/[=]+$/, '').replace(/-/g, '+').replace(/_/g, '/');
  var out = '', bits = 0, held = 0, i, v;
  for (i = 0; i < s.length; i++) {
    v = __b64.indexOf(s.charAt(i));
    if (v < 0) continue;
    held = (held << 6) | v; bits += 6;
    if (bits >= 8) { bits -= 8; out += String.fromCharCode((held >> bits) & 255); }
  }
  return out;
}
function structuredClone(v) { return JSON.parse(JSON.stringify(v)); }
// ES5 has no Symbol and js2py is an ES5 interpreter. Every use of it in the
// VM but one is guarded by typeof, and falls back to length-based
// iteration; the unguarded one is a property key. Unique strings serve for
// both.
var __symbols = 0;
function Symbol(d) {
  return '@@sym:' + (d === undefined ? '' : d) + ':' + (__symbols++);
}
Symbol.iterator = '@@iterator';
Symbol.asyncIterator = '@@asyncIterator';
Symbol.dispose = '@@dispose';
Symbol.toStringTag = '@@toStringTag';
Symbol.hasInstance = '@@hasInstance';
Symbol['for'] = function (k) { return '@@for:' + k; };
// js2py is ES5.1. Everything the VM might reach for that arrived with ES6,
// written in ES5. Typed arrays and Object.defineProperty it already has.
if (typeof Object.assign !== 'function') {
  Object.assign = function (target) {
    var i, k, src;
    for (i = 1; i < arguments.length; i++) {
      src = arguments[i];
      if (src == null) continue;
      for (k in src) if (Object.prototype.hasOwnProperty.call(src, k))
        target[k] = src[k];
    }
    return target;
  };
}
if (typeof Array.from !== 'function') {
  Array.from = function (x, fn) {
    var out = [], i;
    if (x == null) return out;
    for (i = 0; i < x.length; i++) out.push(fn ? fn(x[i], i) : x[i]);
    return out;
  };
}
if (typeof Array.of !== 'function') {
  Array.of = function () { return Array.prototype.slice.call(arguments); };
}
function Map() {
  this._k = []; this._v = [];
}
Map.prototype._i = function (k) {
  for (var i = 0; i < this._k.length; i++) if (this._k[i] === k) return i;
  return -1;
};
Map.prototype.get = function (k) {
  var i = this._i(k); return i < 0 ? undefined : this._v[i]; };
Map.prototype.set = function (k, v) {
  var i = this._i(k);
  if (i < 0) { this._k.push(k); this._v.push(v); } else this._v[i] = v;
  this.size = this._k.length; return this; };
Map.prototype.has = function (k) { return this._i(k) >= 0; };
Map.prototype['delete'] = function (k) {
  var i = this._i(k);
  if (i < 0) return false;
  this._k.splice(i, 1); this._v.splice(i, 1); this.size = this._k.length;
  return true; };
Map.prototype.clear = function () { this._k = []; this._v = []; this.size = 0; };
Map.prototype.forEach = function (fn, self) {
  for (var i = 0; i < this._k.length; i++) fn.call(self, this._v[i], this._k[i], this); };
Map.prototype.keys = function () { return this._k.slice(); };
Map.prototype.values = function () { return this._v.slice(); };
var WeakMap = Map;
function Set(init) {
  this._k = [];
  if (init) for (var i = 0; i < init.length; i++) this.add(init[i]);
}
Set.prototype.has = function (v) {
  for (var i = 0; i < this._k.length; i++) if (this._k[i] === v) return true;
  return false; };
Set.prototype.add = function (v) {
  if (!this.has(v)) this._k.push(v);
  this.size = this._k.length; return this; };
Set.prototype['delete'] = function (v) {
  for (var i = 0; i < this._k.length; i++) if (this._k[i] === v) {
    this._k.splice(i, 1); this.size = this._k.length; return true; }
  return false; };
Set.prototype.clear = function () { this._k = []; this.size = 0; };
Set.prototype.forEach = function (fn, self) {
  for (var i = 0; i < this._k.length; i++) fn.call(self, this._k[i], this._k[i], this); };
Set.prototype.values = function () { return this._k.slice(); };
var WeakSet = Set;
// A Promise that settles as soon as it is told to, drained by __drain
// rather than by an event loop.
function Promise(executor) {
  var self = this;
  this._state = 0; this._value = undefined; this._waiting = [];
  function settle(state, value) {
    if (self._state) return;
    self._state = state; self._value = value;
    for (var i = 0; i < self._waiting.length; i++) __timers.push(self._waiting[i]);
    self._waiting = [];
  }
  try {
    executor(function (v) { settle(1, v); }, function (e) { settle(2, e); });
  } catch (e) { settle(2, e); }
}
Promise.prototype.then = function (onOk, onNo) {
  var self = this;
  return new Promise(function (resolve, reject) {
    function run() {
      try {
        if (self._state === 1) resolve(onOk ? onOk(self._value) : self._value);
        else if (onNo) resolve(onNo(self._value));
        else reject(self._value);
      } catch (e) { reject(e); }
    }
    if (self._state) __timers.push(run); else self._waiting.push(run);
  });
};
Promise.prototype['catch'] = function (fn) { return this.then(null, fn); };
Promise.resolve = function (v) {
  return new Promise(function (r) { r(v); }); };
Promise.reject = function (e) {
  return new Promise(function (_, j) { j(e); }); };
Promise.all = function (list) {
  return new Promise(function (resolve) {
    var out = [], left = list.length, i;
    if (!left) return resolve(out);
    for (i = 0; i < list.length; i++) (function (n) {
      Promise.resolve(list[n]).then(function (v) {
        out[n] = v; if (!--left) resolve(out); });
    })(i);
  });
};
var Reflect = {
  get: function (o, k) { return o[k]; },
  set: function (o, k, v) { o[k] = v; return true; },
  has: function (o, k) { return k in o; },
  ownKeys: function (o) { return Object.getOwnPropertyNames(o); },
  apply: function (f, t, a) { return f.apply(t, a); },
  getPrototypeOf: function (o) { return Object.getPrototypeOf(o); },
  defineProperty: function (o, k, d) { Object.defineProperty(o, k, d); return true; }
};
function TextEncoder() {}
TextEncoder.prototype.encode = function (s) {
  var out = [], i, c;
  s = String(s);
  for (i = 0; i < s.length; i++) {
    c = s.charCodeAt(i);
    if (c < 128) out.push(c);
    else if (c < 2048) out.push(192 | (c >> 6), 128 | (c & 63));
    else out.push(224 | (c >> 12), 128 | ((c >> 6) & 63), 128 | (c & 63));
  }
  return new Uint8Array(out);
};
function TextDecoder() {}
TextDecoder.prototype.decode = function (a) {
  var out = '', i;
  for (i = 0; i < a.length; i++) out += String.fromCharCode(a[i]);
  return out;
};
// Typed arrays, in ES5. js2py implements them with numpy and numpy is not
// on a Kodi box, so `new Uint8Array(4)` raises a Python NameError -- which
// is not a JavaScript exception, so no try/catch in the VM can catch it and
// it takes the whole mint down. An array-like with a length and numeric
// properties is what getRandomValues wants and what Array.prototype's
// methods work on. Values are masked when the array is built; a write after
// that is not truncated, which ES5 gives no way to do and nothing here
// needs.
function __typedArray(mask, signed, bits) {
  function coerce(v) {
    v = Number(v);
    if (!isFinite(v)) return 0;
    v = (v < 0 ? Math.ceil(v) : Math.floor(v)) & mask;
    if (signed && v >= (mask + 1) / 2) v -= mask + 1;
    return v;
  }
  function Typed(arg) {
    var i, source, length;
    if (arg && typeof arg.length === 'number') {
      source = arg;
      length = arg.length;
    } else {
      length = arg === undefined ? 0 : Math.floor(Number(arg)) || 0;
    }
    this.length = length;
    this.BYTES_PER_ELEMENT = bits / 8;
    for (i = 0; i < length; i++)
      this[i] = source ? coerce(source[i]) : 0;
  }
  Typed.prototype.set = function (source, offset) {
    offset = offset || 0;
    for (var i = 0; i < source.length; i++)
      this[offset + i] = coerce(source[i]);
  };
  Typed.prototype.subarray = function (start, end) {
    var out = [], i;
    end = end === undefined ? this.length : end;
    for (i = start || 0; i < end; i++) out.push(this[i]);
    return new Typed(out);
  };
  Typed.prototype.slice = Typed.prototype.subarray;
  Typed.prototype.join = function (sep) {
    return Array.prototype.join.call(this, sep);
  };
  Typed.BYTES_PER_ELEMENT = bits / 8;
  return Typed;
}

if (typeof Object.setPrototypeOf !== 'function') {
  Object.setPrototypeOf = function (o, p) { o.__proto__ = p; return o; };
}

// Install every one of them on the global object.
__define('document', __document);
__define('location', __location);
__define('navigator', __navigator);
__define('screen', __screen);
__define('history', __history);
__define('localStorage', __localStorage);
__define('sessionStorage', __sessionStorage);
__define('performance', __performance);
__define('crypto', __crypto);
__define('__seedEntropy', __seedEntropy);
// Plain assignment, not __define: the engine keeps its built-ins as scope
// bindings rather than properties of the global object, so defining a
// property of `this` leaves the name resolving to the numpy one.
Uint8Array = __typedArray(255, false, 8);
Uint8ClampedArray = __typedArray(255, false, 8);
Int8Array = __typedArray(255, true, 8);
Uint16Array = __typedArray(65535, false, 16);
Int16Array = __typedArray(65535, true, 16);
Uint32Array = __typedArray(4294967295, false, 32);
Int32Array = __typedArray(4294967295, true, 32);
__define('Uint8Array', Uint8Array);
__define('Uint8ClampedArray', Uint8ClampedArray);
__define('Int8Array', Int8Array);
__define('Uint16Array', Uint16Array);
__define('Int16Array', Int16Array);
__define('Uint32Array', Uint32Array);
__define('Int32Array', Int32Array);
__define('innerWidth', __innerWidth);
__define('innerHeight', __innerHeight);
__define('outerWidth', __outerWidth);
__define('outerHeight', __outerHeight);
__define('devicePixelRatio', __devicePixelRatio);
__define('origin', __origin);
__define('isSecureContext', __isSecureContext);
__define('documentElement', __documentElement);
__define('body', __body);
__define('element', element);
__define('matchMedia', matchMedia);
__define('XMLHttpRequest', XMLHttpRequest);
__define('addEventListener', addEventListener);
__define('removeEventListener', removeEventListener);
__define('dispatchEvent', dispatchEvent);
__define('requestAnimationFrame', requestAnimationFrame);
__define('cancelAnimationFrame', cancelAnimationFrame);
__define('setTimeout', setTimeout);
__define('clearTimeout', clearTimeout);
__define('setInterval', setInterval);
__define('clearInterval', clearInterval);
__define('btoa', btoa);
__define('atob', atob);
__define('structuredClone', structuredClone);
__define('Symbol', Symbol);
__define('Map', Map);
__define('Set', Set);
__define('WeakMap', WeakMap);
__define('WeakSet', WeakSet);
__define('Promise', Promise);
__define('Reflect', Reflect);
__define('TextEncoder', TextEncoder);
__define('TextDecoder', TextDecoder);
__define('__drain', __drain);
__define('__timers', __timers);
__define('__drainErrors', __drainErrors);
