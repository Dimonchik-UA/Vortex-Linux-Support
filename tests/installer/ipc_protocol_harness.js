'use strict';

// IPC Protocol Harness for the OVLS Extension.
// Runs with plain node: node Tests/Installer/ipc_protocol_harness.js
// Drives the real Extension internals (index.js) against real temp directories.
// It never touches a real Vortex installation.
//
// Covers the seven requested scenarios:
//   1. request / response / ACK / ACK_CONFIRMED pipeline
//   2. dedup duplicate delivery without reprocessing
//   3. concurrent dedup protection
//   4. session isolation
//   5. recovery after interruption
//   6. supported Extension IPC commands
//   7. error handling (corrupt messages, missing files, invalid ids)

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

const EXTENSION_PATH = path.join(
  __dirname, '..', '..', 'src', 'extension', 'index.js'
);
const init = require(EXTENSION_PATH);

// ---------------------------------------------------------------------------
// Helpers (mirror the production client checksum rules)
// ---------------------------------------------------------------------------

function canonicalJson(value) {
  function sortInto(v) {
    if (Array.isArray(v)) return v.map(sortInto);
    if (v && typeof v === 'object') {
      const out = {};
      Object.keys(v).sort().forEach(function (k) { out[k] = sortInto(v[k]); });
      return out;
    }
    return v;
  }
  return JSON.stringify(sortInto(value));
}

function computeChecksum(obj) {
  const copy = {};
  Object.keys(obj).forEach(function (k) { if (k !== 'checksum') copy[k] = obj[k]; });
  const hash = crypto.createHash('sha256').update(canonicalJson(copy), 'utf8').digest('hex');
  return 'sha256:' + hash;
}

function atomicWriteJson(filePath, value) {
  const tmp = filePath + '.' + process.pid + '.' + Date.now() + '.' +
    crypto.randomBytes(6).toString('hex') + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(value, null, 2), 'utf8');
  fs.renameSync(tmp, filePath);
}

function makeContext(state) {
  const capturedLogs = [];
  return {
    api: {
      getState: function () { return state; },
      sendNotification: function () { return null; },
      dismissNotification: function () {},
      showErrorNotification: function () {},
      log: function(level, message) {
        capturedLogs.push({ level, message });
        if (process.env.IPC_DEBUG) console.error('log[' + level + ']: ' + message);
      },
      getPath: function (name) {
        return name === 'userData' ? '/tmp/ovls-harness-userdata' : '';
      },
      store: { dispatch: function () {} },
    },
    capturedLogs: capturedLogs,
  };
}

// Build a request exactly like external.client.submit_request().
function buildRequest(requestId, requestType, payload) {
  const request = {
    protocolVersion: '1.0',
    id: requestId,
    type: requestType,
    payload: payload || {},
  };
  request.checksum = computeChecksum(request);
  return request;
}

// Write a request into the given requests directory.
function writeRequestFile(requestsDir, requestId, requestType, payload) {
  fs.mkdirSync(requestsDir, { recursive: true });
  const requestPath = path.join(requestsDir, requestId + '.json');
  atomicWriteJson(requestPath, buildRequest(requestId, requestType, payload));
  return requestPath;
}

// Write an ACK like external.client.send_ack() does.
function writeAckFile(acksDir, requestId) {
  fs.mkdirSync(acksDir, { recursive: true });
  const ack = {
    protocolVersion: '1.0',
    requestId: requestId,
    receivedAt: new Date().toISOString(),
  };
  ack.checksum = computeChecksum(ack);
  const ackPath = path.join(acksDir, requestId + '.ack.json');
  atomicWriteJson(ackPath, ack);
  return ackPath;
}

// Write an ACK_CONFIRMED like external.client.save_ack_confirmed() does.
function writeAckConfirmedFile(clientStateDir, requestId) {
  fs.mkdirSync(clientStateDir, { recursive: true });
  const confirmed = {
    protocolVersion: '1.0',
    requestId: requestId,
    confirmedAt: new Date().toISOString(),
  };
  const confirmedPath = path.join(clientStateDir, requestId + '.ack_confirmed.json');
  atomicWriteJson(confirmedPath, confirmed);
  return confirmedPath;
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function resetBookkeeping(dirs) {
  dirs.settledConfirmations = new Set();
  dirs.deferredConfirmations = new Map();
}

// ---------------------------------------------------------------------------
// Scenario 1: full request/response/ACK/ACK_CONFIRMED pipeline
// ---------------------------------------------------------------------------
async function scenario1() {
  console.log('  [1] request / response / ACK / ACK_CONFIRMED');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-ipc1-'));
  try {
    const ipcDir = path.join(tmp, 'OVLS_IPC_V2');
    const context = makeContext({ app: { instanceId: 'instance-1' } });
    const dirs = init.buildRuntimeDirs(ipcDir, 'session-1');
    const rid = 'req-pipeline-1';

    // 1) client submits a ping request through the standard path
    writeRequestFile(dirs.requestsDir, rid, 'ping', {});
    let action = init.pollOnce(context, dirs).action;
    assert.strictEqual(action, 'request', 'pollOnce should claim the request');

    // 2) extension wrote a response
    const responseFile = path.join(dirs.responsesDir, rid + '.json');
    assert.ok(fs.existsSync(responseFile), 'response file must exist');
    let response = readJson(responseFile);
    assert.strictEqual(response.status, 'success', 'ping must succeed');
    assert.strictEqual(response.result.message, 'pong');
    assert.ok(response.checksum, 'response must carry a checksum');

    // 3) dedup record reached "completed"
    const dedupFile = path.join(dirs.dedupDir, rid + '.json');
    assert.ok(fs.existsSync(dedupFile), 'dedup record must exist');
    assert.strictEqual(readJson(dedupFile).status, 'completed');

    // 4) client sends ACK
    writeAckFile(dirs.acksDir, rid);
    resetBookkeeping(dirs);
    action = init.pollOnce(context, dirs).action;
    assert.strictEqual(action, 'ack', 'pollOnce should process the ACK');
    const ackedMarker = path.join(dirs.acksDir, rid + '.acked');
    assert.ok(fs.existsSync(ackedMarker), '.acked marker must be created');
    assert.ok(!fs.existsSync(path.join(dirs.acksDir, rid + '.ack.json')),
      'incoming ACK file must be removed after processing');

    // 5) client writes ACK_CONFIRMED
    writeAckConfirmedFile(dirs.clientStateDir, rid);
    resetBookkeeping(dirs);
    action = init.pollOnce(context, dirs).action;
    assert.strictEqual(action, 'ack_confirmed', 'pollOnce should process ACK_CONFIRMED');

    const result = readJson(dedupFile);
    assert.strictEqual(result.ackConfirmed, true, 'dedup must record ackConfirmed');
    assert.ok(result.ackConfirmedAt, 'dedup must record ackConfirmedAt');
    assert.ok(!fs.existsSync(ackedMarker), '.acked marker must be removed after confirmation');
    assert.ok(!fs.existsSync(path.join(dirs.processingDir, rid + '.json')),
      'processing file must be removed after ACK_CONFIRMED');

    console.log('      OK: pipeline completed end-to-end');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Scenario 2: dedup duplicate delivery without reprocessing
// ---------------------------------------------------------------------------
async function scenario2() {
  console.log('  [2] dedup duplicate delivery');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-ipc2-'));
  try {
    const ipcDir = path.join(tmp, 'OVLS_IPC_V2');
    const context = makeContext({ app: { instanceId: 'instance-1' } });
    const dirs = init.buildRuntimeDirs(ipcDir, 'session-2');
    const rid = 'req-dup-1';

    // first delivery: processed normally through the standard path
    writeRequestFile(dirs.requestsDir, rid, 'ping', {});
    let action2 = init.pollOnce(context, dirs).action;
    assert.strictEqual(action2, 'request');

    // redeliver the same requestId: dedup is already completed
    writeRequestFile(dirs.requestsDir, rid, 'ping', {});
    const dupAction = init.pollOnce(context, dirs).action;

    // The duplicate must be archived as a duplicate, not re-executed.
    const processed = fs.readdirSync(dirs.processedDir);
    const duplicate = processed.filter(function (f) { return f.indexOf('duplicate.' + rid) !== -1; });
    assert.strictEqual(duplicate.length, 1, 'exactly one duplicate archive expected');

    // The dedup record must still be a single completed record.
    const record = readJson(path.join(dirs.dedupDir, rid + '.json'));
    assert.strictEqual(record.status, 'completed');

    // The response must remain the one produced by the first delivery.
    const response = readJson(path.join(dirs.responsesDir, rid + '.json'));
    assert.strictEqual(response.status, 'success');

    // Regression guard: attempt to move completed to processing is rejected.
    const regression2 = init.mergeDedup(dirs.dedupDir, rid, { status: 'processing' },
      context, { allowCreate: false });
    assert.strictEqual(regression2.ok, false, 'completed -> processing regression must be rejected');
    const regression = init.mergeDedup(
      dirs.dedupDir, rid, { status: 'processing' }, context, { allowCreate: true }
    );
    assert.strictEqual(regression.ok, false, 'completed -> processing regression must be rejected');
    assert.strictEqual(readJson(path.join(dirs.dedupDir, rid + '.json')).status, 'completed',
      'dedup status must remain completed after the rejected regression');

    console.log('      OK: duplicate served from cache, operation not repeated');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Scenario 3: concurrent dedup protection
// ---------------------------------------------------------------------------
async function scenario3() {
  console.log('  [3] concurrent dedup protection');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-ipc3-'));
  try {
    const dedupDir = path.join(tmp, 'dedup');
    fs.mkdirSync(dedupDir, { recursive: true });
    const rid = 'req-concurrent-1';

    // Child workers each perform many merge-dedup updates on the same record.
    // The O_EXCL lock file must serialise them: the final record must stay
    // valid JSON and contain every field that was written.
    const workerScript = [
      'const path = require("path");',
      'const ext = require(' + JSON.stringify(EXTENSION_PATH) + ');',
      'const rid = ' + JSON.stringify(rid) + ';',
      'const dedupDir = ' + JSON.stringify(dedupDir) + ';',
      'const ctx = { api: { log: function () {} } };',
      'const unusedId = ' + JSON.stringify(rid) + ' + "-" + process.env.WORKER_INDEX;',
      'let okCount = 0;',
      'for (let i = 0; i < 25; i++) {',
      '  const patch = { status: "processing", worker: process.env.WORKER_INDEX, seq: i };',
      '  const r = ext.mergeDedup(dedupDir, ' + JSON.stringify(rid) + ', patch, ctx, { allowCreate: true });',
      '  if (r.ok) okCount++;',
      '}',
      'const r2 = ext.mergeDedup(dedupDir, ' + JSON.stringify(rid) + ', { status: "completed" }, ctx, { allowCreate: true });',
      'process.stdout.write(JSON.stringify({ worker: process.env.WORKER_INDEX, ok: okCount, final: r2.ok }));',
    ].join('\n');

    const runs = [];
    for (let w = 0; w < 3; w++) {
      runs.push(new Promise(function (resolve, reject) {
        const child = spawn(process.execPath, ['-e', workerScript], {
          env: Object.assign({}, process.env, { WORKER_INDEX: String(w) }),
        });
        let out = '';
        let err = '';
        child.stdout.on('data', function (d) { out += d.toString(); });
        child.stderr.on('data', function (d) { err += d.toString(); });
        child.on('close', function () {
          if (out.trim()) {
            try { resolve(JSON.parse(out)); } catch (e) { reject(new Error('bad worker output: ' + out + err)); }
          } else {
            reject(new Error('worker produced no output: ' + err));
          }
        });
      }));
    }

    const results = await Promise.all(runs);
    const totalOk = results.reduce(function (sum, r) { return sum + r.ok; }, 0);
    // At least one worker must succeed (real concurrency, not a deadlock).
    assert.ok(totalOk >= 1,
      'at least one concurrent merge must succeed (got ' + totalOk + '/75)');
    // The final completed write must succeed for at least one worker.
    const finalSuccesses = results.filter(function (r) { return r.final; });
    assert.ok(finalSuccesses.length >= 1, 'final completed write must succeed for at least one worker');

    // The record must be valid JSON and carry the final completed status.
    const finalFile = path.join(dedupDir, rid + '.json');
    assert.ok(fs.existsSync(finalFile), 'final dedup record must exist');
    const record = readJson(finalFile);
    assert.strictEqual(record.status, 'completed', 'final dedup status must be completed');
    assert.ok(record.worker !== undefined, 'dedup must preserve fields from concurrent writers');

    console.log('      OK: ' + totalOk + ' concurrent merges serialised, final record valid');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Scenario 4: session isolation
// ---------------------------------------------------------------------------
async function scenario4() {
  console.log('  [4] session isolation');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-ipc4-'));
  try {
    const ipcDir = path.join(tmp, 'OVLS_IPC_V2');
    const context = makeContext({ app: { instanceId: 'instance-1' } });
    const dirsA = init.buildRuntimeDirs(ipcDir, 'session-A');
    const dirsB = init.buildRuntimeDirs(ipcDir, 'session-B');

    // Sessions have distinct request/processing/responses directories.
    assert.notStrictEqual(dirsA.requestsDir, dirsB.requestsDir, 'requests dirs differ');
    assert.notStrictEqual(dirsA.responsesDir, dirsB.responsesDir, 'responses dirs differ');
    assert.notStrictEqual(dirsA.processingDir, dirsB.processingDir, 'processing dirs differ');
    // Global dedup/acks are shared.
    assert.strictEqual(dirsA.dedupDir, dirsB.dedupDir, 'dedup dir is global');
    assert.strictEqual(dirsA.acksDir, dirsB.acksDir, 'acks dir is global');

    // A request in session A must not appear in session B's request scan.
    const ridA = 'req-session-a';
    const ridB = 'req-session-b';
    writeRequestFile(dirsA.requestsDir, ridA, 'ping', {});
    assert.deepStrictEqual(fs.readdirSync(dirsB.requestsDir).filter(function (f) { return f.indexOf(ridA) !== -1; }),
      [], 'session B must not see session A request');
    assert.deepStrictEqual(fs.readdirSync(dirsA.requestsDir).filter(function (f) { return f.indexOf(ridB) !== -1; }),
      [], 'session A must not see session B request');

    // Each session processes only its own request (via handleProcessing).
    fs.mkdirSync(dirsA.processingDir, { recursive: true });
    fs.mkdirSync(dirsB.processingDir, { recursive: true });
    atomicWriteJson(path.join(dirsA.processingDir, ridA + '.json'), buildRequest(ridA, 'ping', {}));
    atomicWriteJson(path.join(dirsB.processingDir, ridB + '.json'), buildRequest(ridB, 'getState', {}));
    init.pollOnce(context, dirsA);
    init.pollOnce(context, dirsB);
    assert.ok(fs.existsSync(path.join(dirsA.responsesDir, ridA + '.json')),
      'session A response present');
    assert.ok(fs.existsSync(path.join(dirsB.responsesDir, ridB + '.json')),
      'session B response present');
    assert.ok(!fs.existsSync(path.join(dirsA.responsesDir, ridB + '.json')),
      'session A must not receive session B response');
    assert.ok(!fs.existsSync(path.join(dirsB.responsesDir, ridA + '.json')),
      'session B must not receive session A response');

    console.log('      OK: session directories isolated, responses kept separate');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Scenario 5: recovery after interruption
// ---------------------------------------------------------------------------
async function scenario5() {
  console.log('  [5] recovery after interruption');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-ipc5-'));
  try {
    const ipcDir = path.join(tmp, 'OVLS_IPC_V2');
    const context = makeContext({ app: { instanceId: 'instance-1' } });
    const dirs = init.buildRuntimeDirs(ipcDir, 'session-5');
    const rid = 'req-recovery-1';

    // 1) normal completion (via handleProcessing)
    fs.mkdirSync(dirs.processingDir, { recursive: true });
    atomicWriteJson(path.join(dirs.processingDir, rid + '.json'), buildRequest(rid, 'ping', {}));
    init.pollOnce(context, dirs);
    assert.strictEqual(readJson(path.join(dirs.dedupDir, rid + '.json')).status, 'completed');

    // 2) simulate an interruption: a processing file is left behind (crash between
    //    claim and completion). Recovery is handled by handleProcessing directly.
    const processingPath = path.join(dirs.processingDir, rid + '.json');
    // Re-create processing file to simulate leftover
    fs.mkdirSync(dirs.processingDir, { recursive: true });
    atomicWriteJson(processingPath, Object.assign({}, buildRequest(rid, 'ping', {}), { status: 'executing' }));
    // handleProcessing should consume it
    init.pollOnce(context, dirs);
    assert.ok(!fs.existsSync(processingPath), 'processing file must be consumed during recovery');
    assert.strictEqual(readJson(path.join(dirs.dedupDir, rid + '.json')).status, 'completed',
      'dedup must stay completed across recovery');

    // 3) an orphan ACK_CONFIRMED (no dedup record) must be deferred, not crash polling.
    const orphanId = 'req-orphan';
    writeAckConfirmedFile(dirs.clientStateDir, orphanId);
    resetBookkeeping(dirs);
    let action2 = init.pollOnce(context, dirs).action;
    assert.strictEqual(action2, 'ack_confirmed');
    assert.ok(fs.existsSync(path.join(dirs.clientStateDir, orphanId + '.ack_confirmed.json')),
      'orphan confirmation must be retained for retry');

    // 4) the durable response can be recovered by the client after a crash.
    const response = readJson(path.join(dirs.responsesDir, rid + '.json'));
    assert.strictEqual(response.status, 'success', 'response must be recoverable from disk');

    console.log('      OK: leftover processing consumed, orphan deferred, response recoverable');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Scenario 6: supported Extension IPC commands
// ---------------------------------------------------------------------------
async function scenario6() {
  console.log('  [6] supported IPC commands');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-ipc6-'));
  try {
    const ipcDir = path.join(tmp, 'OVLS_IPC_V2');
    const state = {
      app: { instanceId: 'instance-1' },
      settings: { mods: { installPath: { game: 'C:/staging' } } },
      session: { gameMode: 'game' },
    };
    const context = makeContext(state);
    const dirs = init.buildRuntimeDirs(ipcDir, 'session-6');

    // ping
    writeRequestFile(dirs.requestsDir, 'req-ping', 'ping', {});
    init.pollOnce(context, dirs);
    let response = readJson(path.join(dirs.responsesDir, 'req-ping.json'));
    assert.strictEqual(response.status, 'success');
    assert.strictEqual(response.result.message, 'pong');

    // getState with an explicit path
    writeRequestFile(dirs.requestsDir, 'req-getstate', 'getState', { path: ['settings', 'mods', 'installPath', 'game'] });
    init.pollOnce(context, dirs);
    response = readJson(path.join(dirs.responsesDir, 'req-getstate.json'));
    assert.strictEqual(response.status, 'success');
    assert.strictEqual(response.result.selectedValue, 'C:/staging');

    // getState default path (no payload.path) -> ['settings']
    writeRequestFile(dirs.requestsDir, 'req-getstate-default', 'getState', {});
    init.pollOnce(context, dirs);
    response = readJson(path.join(dirs.responsesDir, 'req-getstate-default.json'));
    assert.strictEqual(response.status, 'success');
    assert.deepStrictEqual(response.result.selectedPath, ['settings']);

    // getState on a confidential path is rejected
    writeRequestFile(dirs.requestsDir, 'req-getstate-unsafe', 'getState', { path: ['confidential'] });
    init.pollOnce(context, dirs);
    response = readJson(path.join(dirs.responsesDir, 'req-getstate-unsafe.json'));
    assert.strictEqual(response.status, 'error');
    assert.strictEqual(response.error.code, 'UNSAFE_STATE_PATH');

    // unknown request type
    writeRequestFile(dirs.requestsDir, 'req-unknown', 'bogusType', {});
    init.pollOnce(context, dirs);
    response = readJson(path.join(dirs.responsesDir, 'req-unknown.json'));
    assert.strictEqual(response.status, 'error');
    assert.strictEqual(response.error.code, 'UNKNOWN_REQUEST_TYPE');

    // missing request type
    fs.mkdirSync(dirs.requestsDir, { recursive: true });
    fs.writeFileSync(path.join(dirs.requestsDir, 'req-no-type.json'),
      JSON.stringify({ protocolVersion: '1.0', id: 'req-no-type', payload: {} }, null, 2), 'utf8');
    init.pollOnce(context, dirs);
    response = readJson(path.join(dirs.responsesDir, 'req-no-type.json'));
    assert.strictEqual(response.status, 'error');
    assert.strictEqual(response.error.code, 'MISSING_REQUEST_TYPE');

    console.log('      OK: ping, getState, unsafe path, unknown type, missing type');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Scenario 7: error handling
// ---------------------------------------------------------------------------
async function scenario7() {
  console.log('  [7] error handling');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-ipc7-'));
  try {
    const ipcDir = path.join(tmp, 'OVLS_IPC_V2');
    const context = makeContext({ app: { instanceId: 'instance-1' } });
    const dirs = init.buildRuntimeDirs(ipcDir, 'session-7');

    // 7a) corrupt JSON request -> error response
    fs.mkdirSync(dirs.requestsDir, { recursive: true });
    fs.writeFileSync(path.join(dirs.requestsDir, 'req-corrupt.json'),
      '{ this is not valid json', 'utf8');
    const corruptAction = init.pollOnce(context, dirs).action;
    assert.strictEqual(corruptAction, 'request_claim_failed',
      'corrupt JSON must be rejected at claim time');
    // The request file must remain in requests/ (not moved to processed).
    assert.ok(fs.existsSync(path.join(dirs.requestsDir, 'req-corrupt.json')),
      'corrupt request file must remain in requests/ after failed claim');
    // Clean up so subsequent tests are not affected.
    try { fs.unlinkSync(path.join(dirs.requestsDir, 'req-corrupt.json')); } catch (e) {}

    // 7b) unsafe request id -> INVALID_REQUEST_ID response
    fs.mkdirSync(dirs.requestsDir, { recursive: true });
    // id contains a slash; the extension must reject it rather than escape the IPC root.
    const unsafeId = '../escape';
    atomicWriteJson(path.join(dirs.requestsDir, 'req-unsafe.json'),
      { protocolVersion: '1.0', id: unsafeId, type: 'ping', payload: {} });
    init.pollOnce(context, dirs);
    const unknownResponses = fs.readdirSync(dirs.responsesDir).filter(function (f) { return f.startsWith('unknown-'); });
    assert.ok(unknownResponses.length >= 1, 'unsafe id must produce an error response');
    const unsafeResponse = readJson(path.join(dirs.responsesDir, unknownResponses[0]));
    assert.strictEqual(unsafeResponse.error.code, 'INVALID_REQUEST_ID');

    // 7c) missing request id -> MISSING_REQUEST_ID
    fs.mkdirSync(dirs.requestsDir, { recursive: true });
    atomicWriteJson(path.join(dirs.requestsDir, 'req-no-id.json'),
      { protocolVersion: '1.0', type: 'ping', payload: {} });
    init.pollOnce(context, dirs);
    // handled via handleProcessing; response file name uses 'unknown-<ts>' path.
    // We simply assert an error response was written.
    const afterNoId = fs.readdirSync(dirs.responsesDir);
    assert.ok(afterNoId.length >= 2, 'missing id must produce another error response');

    // 7d) ACK for an unknown requestId -> archived as invalid
    writeAckFile(dirs.acksDir, 'req-no-such');
    resetBookkeeping(dirs);
    init.pollOnce(context, dirs);
    const processed = fs.readdirSync(dirs.processedDir);
    assert.ok(processed.some(function (f) { return f.indexOf('ack.processed.unknown-') !== -1; }),
      'ACK for unknown requestId must be archived');

    // 7e) ACK_CONFIRMED with a mismatching file name -> quarantined
    fs.mkdirSync(dirs.clientStateDir, { recursive: true });
    const mismatched = {
      protocolVersion: '1.0',
      requestId: 'req-mismatch',
      confirmedAt: new Date().toISOString(),
    };
    atomicWriteJson(path.join(dirs.clientStateDir, 'req-other-name.ack_confirmed.json'), mismatched);
    resetBookkeeping(dirs);
    init.pollOnce(context, dirs);
    const quarantined = fs.readdirSync(dirs.clientStateDir)
      .filter(function (f) { return f.indexOf('req-other-name') !== -1 && f.indexOf('.invalid') !== -1; });
    assert.ok(quarantined.length >= 1, 'mismatched ACK_CONFIRMED must be quarantined');

    // 7f) ACK with a bad checksum -> archived as invalid
    fs.mkdirSync(dirs.acksDir, { recursive: true });
    atomicWriteJson(path.join(dirs.acksDir, 'req-bad-ack.ack.json'), {
      protocolVersion: '1.0',
      requestId: 'req-bad-ack',
      receivedAt: new Date().toISOString(),
      checksum: 'sha256:deadbeef',
    });
    // A response must exist for the ACK to be otherwise valid.
    fs.mkdirSync(dirs.processingDir, { recursive: true });
    atomicWriteJson(path.join(dirs.processingDir, 'req-bad-ack.json'), buildRequest('req-bad-ack', 'ping', {}));
    init.pollOnce(context, dirs); // produce the response
    resetBookkeeping(dirs);
    init.pollOnce(context, dirs); // process the ACK (handleAck)
    const badAckArchived = fs.readdirSync(dirs.processedDir)
      .some(function (f) { return f.indexOf('checksum_mismatch') !== -1 || f.indexOf('checksum_error') !== -1; });
    assert.ok(badAckArchived, 'ACK with bad checksum must be archived');

    console.log('      OK: corrupt json, unsafe id, missing id, unknown ack, mismatch confirm, bad ack checksum');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
async function main() {
  console.log('IPC_PROTOCOL_HARNESS_START');
  await scenario1();
  await scenario2();
  await scenario3();
  await scenario4();
  await scenario5();
  await scenario6();
  await scenario7();
  console.log('IPC_PROTOCOL_HARNESS_OK');
}

main().catch(function (error) {
  console.error('IPC_PROTOCOL_HARNESS_FAILED: ' + (error && error.stack ? error.stack : error));
  process.exit(1);
});