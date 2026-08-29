// Run the cached challenge under node, with the tracer, exactly the way the
// js2py driver runs it.
const fs = require('fs');
const SP = __dirname;
globalThis.window = globalThis; globalThis.self = globalThis;
(0, eval)(fs.readFileSync(SP + '/shim_es5.js', 'utf8'));
globalThis.document.defaultView = globalThis;
globalThis.top = globalThis; globalThis.parent = globalThis;
(0, eval)(fs.readFileSync(SP + '/trace.js', 'utf8'));

// Keep every snippet the VM evals, to line up against js2py's.
const pieces = [];
const realEval = globalThis.eval;
globalThis.eval = function (code) {
  pieces.push(String(code));
  return realEval(code);
};
process.on('exit', () => fs.writeFileSync(SP + '/evalled_node.txt',
  pieces.map((p, i) => i + ' (' + p.length + '): ' +
             p.slice(0, 160).replace(/\n/g, ' ')).join('\n') + '\n'));

const {program, globalName} =
  JSON.parse(fs.readFileSync(SP + '/cached_challenge.json', 'utf8'));
(0, eval)(fs.readFileSync(SP + '/cached_interpreter.js', 'utf8')
  .replaceAll('if(T=v(b,420),T>=k)break;', 'if(T=v(b,420),__rec("op " + T),T>=k)break;')
  .replaceAll('catch(B){l(b,451)?M6(22,B,b,N):F(451,b,B)}', 'catch(B){__rec("VM caught: " + (B && B.message !== undefined ? ("msg " + B.message) : ("bare " + __brief(B))));l(b,451)?M6(22,B,b,N):F(451,b,B)}')
  .replaceAll('function(){return+new Date}', 'function(){var v = +new Date; __rec("fallback F -> " + (typeof v) + " " + __brief(v)); return v}')
  .replaceAll('return this.MF+window.performance.now()', 'return (__rec("MF is " + (typeof this.MF) + " " + __brief(this.MF)), this.MF+window.performance.now())'));
const vm = globalThis[globalName];
if (!vm || !vm.a) throw new Error('no ' + globalName);

let setupResult = null, snapshot = null;
const noop = function () {};
globalThis.__traceOn = true;
vm.a(program, function (a, s, p, c) { setupResult = {a, s, p, c}; },
     true, undefined, noop, [[], []], undefined, false,
     [noop, noop, noop, noop, noop]);
globalThis.__drain();
if (setupResult) {
  setupResult.a(function (r) { snapshot = r; },
                [undefined, undefined, [], undefined]);
  globalThis.__drain();
}
fs.writeFileSync(SP + '/trace_node.txt', globalThis.__trace.join('\n') + '\n');
console.log('node: %d traced calls, snapshot %s',
            globalThis.__trace.length, String(snapshot).slice(0, 30));
