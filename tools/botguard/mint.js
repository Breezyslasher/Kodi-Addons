// Mint a YouTube proof-of-origin token, start to finish, with nothing but node.
//
//   node mint.js <contentBinding>
//
// contentBinding is the visitorData for a session-bound token, or a video id
// for a content-bound one. Prints the token on stdout and everything else on
// stderr, so it can be used in a pipe.
//
// The flow, read out of a browser capture rather than a specification:
//
//   POST jnn-pa.googleapis.com/$rpc/…/Waa/Create   [requestKey]
//        -> [null, "<scrambled challenge>"]
//   the challenge descrambles -- base64, then +97 to every byte -- into
//        [messageId, wrappedScript, wrappedUrl, interpreterHash, program,
//         globalName, , clientExperimentsStateBlob]
//   run the interpreter, which registers globalThis[globalName]
//   vm.a(program, setup, true, undefined, telemetry, [[],[]], undefined,
//        false, loggers)                     -> the vm functions
//   asyncSnapshotFunction(cb, [binding, signedTimestamp, signalOutput, skip])
//        -> "$…", and signalOutput[0] becomes the minter factory
//   POST …/Waa/GenerateIT  [requestKey, "$…"] -> ["<integrity token>", ttl, …]
//   factory(integrityTokenBytes) -> mint, mint(bindingBytes) -> the token
const https = require('https');

const REQUEST_KEY = 'O43z0dpjhgX20SCx4KAo';
const GOOG = 'https://jnn-pa.googleapis.com/$rpc/google.internal.waa.v1.Waa';
const UA = 'Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0';
const note = (...a) => console.error(...a);

function post(url, payload) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify(payload);
    const u = new URL(url);
    const req = https.request({
      hostname: u.hostname, path: u.pathname + u.search, method: 'POST',
      headers: {
        'content-type': 'application/json+protobuf',
        'content-length': Buffer.byteLength(body),
        'user-agent': UA,
        'x-goog-api-key': 'AIzaSyDyT5W0Jh49F30Pqqtyfdf7pDLFKLJoAnw',
        'x-user-agent': 'grpc-web-javascript/0.1',
      },
    }, res => {
      let out = '';
      res.on('data', d => out += d);
      res.on('end', () => res.statusCode === 200
        ? resolve(out)
        : reject(new Error('HTTP ' + res.statusCode + ' from ' + u.pathname + ': ' + out.slice(0, 200))));
    });
    req.on('error', reject);
    req.end(body);
  });
}

function get(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'user-agent': UA } }, res => {
      if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode + ' for ' + url));
      let out = '';
      res.on('data', d => out += d);
      res.on('end', () => resolve(out));
    }).on('error', reject);
  });
}

// base64, then every byte plus 97. Google's own descrambler, in one line.
const descramble = s =>
  Buffer.from(Buffer.from(s, 'base64').map(b => (b + 97) & 0xFF)).toString('utf8');

const websafe = b => Buffer.from(b).toString('base64')
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

(async () => {
  const binding = process.argv[2];
  if (!binding) { note('usage: node mint.js <visitorData|videoId>'); process.exit(2); }

  note('asking for a challenge...');
  const created = JSON.parse(await post(GOOG + '/Create', [REQUEST_KEY]));
  const raw = Array.isArray(created) ? created.find(v => typeof v === 'string') : null;
  if (!raw) throw new Error('Create returned no challenge: ' + JSON.stringify(created).slice(0, 200));

  const challenge = JSON.parse(descramble(raw));
  const [, wrappedScript, wrappedUrl, interpreterHash, program, globalName] = challenge;
  note('challenge: hash %s, program %d chars, global %s',
       interpreterHash, (program || '').length, globalName);

  let interpreter = Array.isArray(wrappedScript)
    ? wrappedScript.find(v => typeof v === 'string' && v.length > 1000) : null;
  if (!interpreter) {
    let url = Array.isArray(wrappedUrl) ? wrappedUrl.find(v => typeof v === 'string') : wrappedUrl;
    if (!url) throw new Error('the challenge carries neither interpreter nor url');
    if (url.startsWith('//')) url = 'https:' + url;
    note('fetching the interpreter from %s', url);
    interpreter = await get(url);
  }
  note('interpreter: %d chars', interpreter.length);

  globalThis.window = globalThis;
  globalThis.self = globalThis;
  require('./shim.js');
  (0, eval)(interpreter);

  const vm = globalThis[globalName];
  if (!vm || !vm.a) throw new Error('the interpreter did not register ' + globalName);

  const noop = () => {};
  const fns = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('the vm never set itself up')), 30000);
    vm.a(program, (a, s, p, c) => { clearTimeout(t); resolve({ a, s, p, c }); },
         true, undefined, noop, [[], []], undefined, false,
         [noop, noop, noop, noop, noop]);
  });

  const signalOutput = [];
  const response = await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('the snapshot never returned')), 30000);
    fns.a(r => { clearTimeout(t); resolve(r); },
          [undefined, undefined, signalOutput, undefined]);
  });
  note('botguard response: %d chars, starts %s',
       String(response).length, String(response).slice(0, 12));
  note('signal output: %d slot(s) [%s]',
       signalOutput.length, signalOutput.map(x => typeof x).join(','));

  note('exchanging it for an integrity token...');
  const it = JSON.parse(await post(GOOG + '/GenerateIT', [REQUEST_KEY, String(response)]));
  const integrityToken = Array.isArray(it) ? it[0] : null;
  if (!integrityToken) throw new Error('GenerateIT returned no token: ' + JSON.stringify(it).slice(0, 200));
  note('integrity token: %d chars, good for %s seconds', integrityToken.length, it[1]);

  const factory = signalOutput[0];
  if (typeof factory !== 'function')
    throw new Error('the snapshot produced no minter -- signal output was empty');

  const mint = await factory(new Uint8Array(Buffer.from(integrityToken, 'base64')));
  if (typeof mint !== 'function') throw new Error('the factory returned no mint function');
  const token = await mint(new TextEncoder().encode(binding));
  note('minted %d bytes', token.length);
  console.log(websafe(token));
})().catch(e => { note('failed:', e && (e.message || e)); process.exit(1); });
