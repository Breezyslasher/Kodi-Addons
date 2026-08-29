// Make the run reproducible, then record every call into a built-in, so the
// same challenge can be run under node and under js2py and the traces
// diffed. The first line that differs is the divergence.
(function () {
  var seed = 123456789;
  function rnd() {
    seed = (1103515245 * seed + 12345) % 2147483648;
    return seed / 2147483648;
  }
  Math.random = function () { return rnd(); };
  var FIXED = 1756400000000;
  var tick = 0;
  Date.now = function () { return FIXED + (tick += 7); };
  if (typeof performance === 'object' && performance)
    performance.now = function () { return (tick += 3); };
  if (typeof crypto === 'object' && crypto)
    crypto.getRandomValues = function (a) {
      for (var i = 0; i < a.length; i++) a[i] = Math.floor(rnd() * 256);
      return a;
    };
})();

var __trace = [], __traceOn = false, __traceMax = 2000000;
// Held before anything is wrapped: __brief must not call a traced method,
// or describing an argument traces the describing.
var __strSlice = String.prototype.slice;
var __arrJoin = Array.prototype.join;
var __arrPush = Array.prototype.push;
var __toStr = Object.prototype.toString;
function __brief(v) {
  try {
    var t = typeof v;
    if (t === 'string')
      return v.length > 32
        ? 'S' + v.length + ':' + __strSlice.call(v, 0, 32) : 'S:' + v;
    if (t === 'number') return 'N:' + v;
    if (t === 'boolean' || t === 'undefined') return String(v);
    if (v === null) return 'null';
    if (t === 'function') return 'fn';
    if (__toStr.call(v) === '[object Array]') return 'A' + v.length;
    return 'O';
  } catch (e) { return '?'; }
}
function __rec(line) {
  if (__traceOn && __trace.length < __traceMax) __trace.push(line);
}
function __wrap(holder, name, label) {
  var f;
  try { f = holder[name]; } catch (e) { return; }
  if (typeof f !== 'function') return;
  var wrapped = function () {
    var args = [], i, r;
    // A marker the Python side can catch and turn into a stack: touching a
    // property of null raises through js2py's own error factory, which is
    // hooked to print the translated line that did it.
    if (label === 'Math.floor' && typeof arguments[0] === 'string') {
      try { null.__whereDidThisComeFrom; } catch (e) {}
    }
    for (i = 0; i < arguments.length; i++)
      __arrPush.call(args, __brief(arguments[i]));
    try { r = f.apply(this, arguments); }
    catch (e) {
      __rec(label + '(' + __arrJoin.call(args, ',') + ') THREW'); throw e;
    }
    __rec(label + '(' + __arrJoin.call(args, ',') + ')=' + __brief(r));
    return r;
  };
  try { holder[name] = wrapped; } catch (e) {}
}
(function () {
  var m = ['floor', 'ceil', 'round', 'abs', 'max', 'min', 'pow', 'sqrt',
           'random', 'log', 'exp', 'imul'], i;
  for (i = 0; i < m.length; i++) __wrap(Math, m[i], 'Math.' + m[i]);
  __wrap(JSON, 'stringify', 'JSON.stringify');
  __wrap(JSON, 'parse', 'JSON.parse');
  __wrap(Object, 'keys', 'Object.keys');
  __wrap(Object, 'getOwnPropertyNames', 'Object.gOPN');
  __wrap(String, 'fromCharCode', 'fromCharCode');
  var sp = ['replace', 'split', 'match', 'indexOf', 'slice', 'substring',
            'toUpperCase', 'toLowerCase', 'charAt'];
  for (i = 0; i < sp.length; i++)
    __wrap(String.prototype, sp[i], 'str.' + sp[i]);
  var ap = ['join', 'sort', 'splice', 'concat', 'reverse', 'indexOf'];
  for (i = 0; i < ap.length; i++)
    __wrap(Array.prototype, ap[i], 'arr.' + ap[i]);
  __wrap(RegExp.prototype, 'exec', 're.exec');
  __wrap(RegExp.prototype, 'test', 're.test');
  __wrap(Number.prototype, 'toString', 'num.toString');
  __define('parseInt', (function (f) {
    return function () {
      var a = [], i;
      for (i = 0; i < arguments.length; i++)
        __arrPush.call(a, __brief(arguments[i]));
      var r = f.apply(null, arguments);
      __rec('parseInt(' + __arrJoin.call(a, ',') + ')=' + __brief(r));
      return r;
    };
  })(parseInt));
})();
// Date, wrapped to say how it was reached: the trace has js2py handing
// Math.floor the date *string* where V8 hands it the timestamp, and the
// interpreter's only use of Date is `+new Date`.
(function (D) {
  function Wrapped(a) {
    if (this instanceof Wrapped) {
      __rec('new Date(' + (arguments.length ? __brief(a) : '') + ')');
      return arguments.length ? new D(a) : new D();
    }
    var s = D();
    __rec('Date() as a plain call =' + __brief(s));
    return s;
  }
  Wrapped.prototype = D.prototype;
  Wrapped.now = D.now;
  Wrapped.parse = D.parse;
  Wrapped.UTC = D.UTC;
  __define('Date', Wrapped);
})(Date);
__rec('performance.now is ' + typeof ((window.performance || {}).now));
