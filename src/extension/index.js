'use strict';

const fs = require('fs');
const path = require('path');

const PROTOCOL_VERSION = '1.0';
const POLL_INTERVAL_MS = 2000;
const processedIds = new Set();

// Vortex reports "Mods can't be deployed." as the test-runner notification of
// its own "valid-activator" check. The id is stable: test_runner builds it as
// "test-" + check.id (src/renderer/src/extensions/test_runner/index.ts).
const VORTEX_DEPLOY_FAILURE_ID = 'test-valid-activator';
const STAGING_FIX_NOTIFICATION_ID = 'ovls-staging-fix';
const STAGING_FIX_APPLY_DELAY_MS = 1000;

// Tag file Vortex reads before deploying; name is fixed in
// mod_management/stagingDirectory.ts (STAGING_DIR_TAG).
const STAGING_DIR_TAG = '__vortex_staging_folder';

// Vortex redux action from mod_management/actions/settings.ts.
const SET_MOD_INSTALL_PATH = 'SET_MOD_INSTALL_PATH';

const offeredStagingFixes = new Set();

const crypto = require('crypto');

const MAX_SAFE_INTEGER = 9007199254740991;
const MIN_SAFE_INTEGER = -9007199254740991;

function isAsciiKey(key) {
  for (let i = 0; i < key.length; i++) {
    if (key.charCodeAt(i) > 127) return false;
  }
  return true;
}

function validateValue(value) {
  if (typeof value === 'boolean') return true;
  if (typeof value === 'number') {
    if (!Number.isInteger(value)) throw new Error('Float not allowed');
    if (value < MIN_SAFE_INTEGER || value > MAX_SAFE_INTEGER) throw new Error('Integer out of safe range');
    if (!isFinite(value)) throw new Error('NaN/Infinity not allowed');
    return true;
  }
  if (typeof value === 'string') return true;
  if (value === null) return true;
  if (Array.isArray(value)) {
    for (const item of value) validateValue(item);
    return true;
  }
  if (typeof value === 'object') {
    for (const k of Object.keys(value)) {
      if (typeof k !== 'string') throw new Error('Key must be string');
      if (!isAsciiKey(k)) throw new Error('Non-ASCII key not allowed: ' + k);
      validateValue(value[k]);
    }
    return true;
  }
  throw new Error('Unsupported type: ' + typeof value);
}

function canonicalJson(obj) {
  validateValue(obj);
  const sortedKeys = Object.keys(obj).sort();
  const parts = [];
  for (const key of sortedKeys) {
    parts.push(JSON.stringify(key) + ':' + serializeValue(obj[key]));
  }
  return '{' + parts.join(',') + '}';
}

function serializeValue(value) {
  if (value === null) return 'null';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return '[' + value.map(serializeValue).join(',') + ']';
  }
  if (typeof value === 'object') {
    const sortedKeys = Object.keys(value).sort();
    const parts = [];
    for (const key of sortedKeys) {
      parts.push(JSON.stringify(key) + ':' + serializeValue(value[key]));
    }
    return '{' + parts.join(',') + '}';
  }
  throw new Error('Unsupported type in serialization');
}

function computeChecksum(obj) {
  // Exclude checksum field
  const objCopy = {};
  for (const k of Object.keys(obj)) {
    if (k !== 'checksum') objCopy[k] = obj[k];
  }
  const canonical = canonicalJson(objCopy);
  const hash = crypto.createHash('sha256').update(canonical, 'utf8').digest('hex');
  return 'sha256:' + hash;
}

function verifyChecksum(obj, expectedChecksum) {
  const computed = computeChecksum(obj);
  return computed === expectedChecksum;
}

// P0.6: directory fsync is an additional durability guarantee, not a functional
// precondition of the IPC. EPERM/ENOTSUP/EINVAL mean the current environment cannot
// fsync directories (observed under Proton/Wine 11.0). In that case the IPC keeps
// working with a REDUCED durability guarantee and a warning MUST be logged.
// Every other error (EIO, EISDIR, unknown codes) is re-thrown unchanged.
const DIRECTORY_FSYNC_UNSUPPORTED_CODES = ['EPERM', 'ENOTSUP', 'EINVAL'];

function isDirectoryFsyncUnsupported(error) {
  return !!error && typeof error.code === 'string' &&
    DIRECTORY_FSYNC_UNSUPPORTED_CODES.indexOf(error.code) !== -1;
}

// Returns { synced, degraded, code, dir, operation }.
// Throws for any error that is not a recognised directory-fsync unavailability.
function fsyncDirectory(dirPath, operation, context) {
  let fd = -1;
  try {
    fd = fs.openSync(dirPath, 'r');
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = -1;
    return { synced: true, degraded: false, code: null, dir: dirPath, operation: operation };
  } catch (error) {
    if (fd !== -1) {
      try { fs.closeSync(fd); } catch (closeError) {}
    }
    if (!isDirectoryFsyncUnsupported(error)) {
      throw error;
    }
    log(context, 'warn',
      'directory fsync unavailable: code=' + error.code +
      ' dir=' + dirPath +
      ' operation=' + operation +
      ' — durability guarantee reduced; power-loss durability is NOT guaranteed');
    return {
      synced: false,
      degraded: true,
      code: error.code,
      dir: dirPath,
      operation: operation
    };
  }
}

function writeJson(filePath, value, context, operation) {
  const suffix = crypto.randomBytes(8).toString('hex');
  const tmp = filePath + '.' + process.pid + '.' + Date.now() + '.' + suffix + '.tmp';
  const data = JSON.stringify(value, null, 2);
  let fd = -1;
  const parentDir = path.dirname(filePath);
  const op = operation || 'atomic write';
  try {
    fd = fs.openSync(tmp, 'wx');
    fs.writeSync(fd, data);
    // File fsync is mandatory and is never suppressed.
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = -1;
    fsyncDirectory(parentDir, op + ': fsync parent after tmp creation', context);
    fs.renameSync(tmp, filePath);
    fsyncDirectory(parentDir, op + ': fsync parent after rename', context);
  } catch (error) {
    if (fd !== -1) {
      try { fs.closeSync(fd); } catch (e) {}
    }
    try { fs.unlinkSync(tmp); } catch (e) {}
    throw error;
  }
}

// P0.4: reject request ids that cannot be used safely as a single file name
// component. Prevents path traversal such as "../../etc/passwd" from reaching
// path.join() through ACK_CONFIRMED / ACK / request contents.
function isSafeRequestId(value) {
  if (typeof value !== 'string') return false;
  if (value.length === 0 || value.length > 200) return false;
  if (value === '.' || value === '..') return false;
  if (value.indexOf('/') !== -1 || value.indexOf('\\') !== -1) return false;
  if (value.indexOf('\0') !== -1) return false;
  return /^[A-Za-z0-9._-]+$/.test(value);
}

// P0.4: read a dedup record. Returns null when the file is absent.
// Throws when the file exists but cannot be read or parsed.
function readDedupRecord(dedupDir, requestId) {
  const dedupFile = path.join(dedupDir, requestId + '.json');
  if (!fs.existsSync(dedupFile)) return null;
  const raw = fs.readFileSync(dedupFile, 'utf8');
  const record = JSON.parse(raw);
  if (!record || typeof record !== 'object' || Array.isArray(record)) {
    throw new Error('dedup record is not a JSON object');
  }
  return record;
}

// P0.4: merge-update a dedup record with file locking to prevent TOCTOU.
// Existing fields are preserved; only keys present in `patch` are replaced.
// Returns { ok, missing }. `missing` is true when no record exists and
// options.allowCreate is false; in that case nothing is written.
function mergeDedup(dedupDir, requestId, patch, context, options) {
  const allowCreate = !!(options && options.allowCreate);
  if (!dedupDir || !isSafeRequestId(requestId)) {
    log(context, 'error', 'dedup update rejected: unsafe requestId ' + JSON.stringify(String(requestId)));
    return { ok: false, missing: false };
  }

  // Acquire lock for this requestId to prevent TOCTOU
  const lockFile = path.join(dedupDir, '.lock-' + requestId);
  let lockFd = -1;
  try {
    // Try to acquire exclusive lock using O_EXCL
    try {
      lockFd = fs.openSync(lockFile, 'wx');
    } catch (e) {
      if (e.code === 'EEXIST') {
        // Another process is updating this dedup record, wait and retry
        let retries = 10;
        while (retries-- > 0 && lockFd === -1) {
          try {
            lockFd = fs.openSync(lockFile, 'wx');
          } catch (e2) {
            if (e2.code !== 'EEXIST') throw e2;
            // Wait a bit and retry
            const start = Date.now();
            while (Date.now() - start < 50) {}
          }
        }
        if (lockFd === -1) {
          log(context, 'error', 'dedup lock acquisition failed for ' + requestId);
          return { ok: false, missing: false };
        }
      } else {
        throw e;
      }
    }

    let record = null;
    try {
      record = readDedupRecord(dedupDir, requestId);
    } catch (e) {
      try { fs.closeSync(lockFd); } catch (e2) {}
      try { fs.unlinkSync(lockFile); } catch (e2) {}
      log(context, 'error', 'dedup read failed for ' + requestId + ': ' + (e && e.message ? e.message : String(e)));
      return { ok: false, missing: false };
    }
    if (record === null) {
      if (!allowCreate) {
        try { fs.closeSync(lockFd); } catch (e) {}
        try { fs.unlinkSync(lockFile); } catch (e) {}
        return { ok: false, missing: true };
      }
      record = {
        requestId: String(requestId),
        dedupCreatedAt: new Date().toISOString()
      };
    }
    // Prevent regression: if record is already completed, don't overwrite with 'processing'
    if (record.status === 'completed' && patch && patch.status === 'processing') {
      try { fs.closeSync(lockFd); } catch (e) {}
      try { fs.unlinkSync(lockFile); } catch (e) {}
      log(context, 'warn', 'dedup update rejected: cannot regress completed to processing for ' + requestId);
      return { ok: false, missing: false };
    }
    const merged = Object.assign({}, record);
    if (patch && typeof patch === 'object') {
      Object.keys(patch).forEach(function (key) {
        if (patch[key] !== undefined) merged[key] = patch[key];
      });
    }
    merged.requestId = String(requestId);
    merged.updatedAt = new Date().toISOString();
    const dedupFile = path.join(dedupDir, requestId + '.json');
    try {
      writeJson(dedupFile, merged, context, 'dedup merge update');
    } catch (e) {
      try { fs.closeSync(lockFd); } catch (e2) {}
      try { fs.unlinkSync(lockFile); } catch (e2) {}
      log(context, 'error', 'dedup write failed for ' + requestId + ': ' + (e && e.message ? e.message : String(e)));
      return { ok: false, missing: false };
    }
    try { fs.closeSync(lockFd); } catch (e) {}
    try { fs.unlinkSync(lockFile); } catch (e) {}
    return { ok: true, missing: false };
  } catch (outerError) {
    try { if (lockFd !== -1) fs.closeSync(lockFd); } catch (e) {}
    try { fs.unlinkSync(lockFile); } catch (e) {}
    log(context, 'error', 'dedup merge failed for ' + requestId + ': ' + (outerError && outerError.message ? outerError.message : String(outerError)));
    return { ok: false, missing: false };
  }
}

// P0.3/P0.4: backward-compatible dedup writer built on merge-update.
// Creates the record when absent and never drops fields written earlier
// (result, dedupCreatedAt, ackProcessed, ...).
// Returns true on success, false on failure (caller must handle).
function writeDedup(dedupDir, requestId, status, extra, context) {
  const patch = { status: status };
  if (extra && typeof extra === 'object') {
    Object.keys(extra).forEach(function (key) {
      if (extra[key] !== undefined) patch[key] = extra[key];
    });
  }
  return mergeDedup(dedupDir, requestId, patch, context, { allowCreate: true }).ok;
}

// P0.5: Update processing file status (claimed → executing → completed)
function updateProcessingStatus(processingPath, status, context) {
  try {
    if (!fs.existsSync(processingPath)) return false;
    const raw = fs.readFileSync(processingPath, 'utf8');
    const data = JSON.parse(raw);
    data.status = status;
    data.updatedAt = new Date().toISOString();
    writeJson(processingPath, data, context, 'processing status update (' + status + ')');
    return true;
  } catch (e) {
    // Status update stays best-effort (its boolean contract is preserved), but the
    // failure is reported instead of being silently swallowed.
    log(context, 'error', 'processing status update failed (' + status + '): ' + (e && e.message ? e.message : String(e)));
    return false;
  }
}

function log(context, level, message) {
  try {
    if (context && context.api && typeof context.api.log === 'function') {
      context.api.log(level, 'OVLS9: ' + message);
    } else {
      console.log('OVLS9 [' + level + ']: ' + message);
    }
  } catch (e) {
    try { console.log('OVLS9 [' + level + ']: ' + message); } catch (e2) {}
  }
}

function navigateState(state, requestedPath) {
  let current = state;
  const pathParts = Array.isArray(requestedPath) ? requestedPath : [];
  for (const part of pathParts) {
    if (current && Object.prototype.hasOwnProperty.call(current, part)) {
      current = current[part];
    } else {
      current = null;
      break;
    }
  }
  return current;
}

function isSafePath(requestedPath) {
  if (!Array.isArray(requestedPath)) return false;
  if (requestedPath.length === 0) return true;
  return requestedPath[0] !== 'confidential';
}

// Step 4: Safe claim/rename mechanism
// Uses O_EXCL ('wx' flag) for atomic exclusive creation of processing file.
// renameSync alone is NOT exclusive — it can overwrite. O_EXCL guarantees
// that only one process can create the target file.
function claimRequest(requestPath, processingPath, context, dirs) {
  // 1. Validate source request exists and is readable
  if (!fs.existsSync(requestPath)) {
    throw new Error('CLAIM_SOURCE_MISSING: request file does not exist: ' + requestPath);
  }

  // 2. Read and validate request content
  let raw;
  try {
    raw = fs.readFileSync(requestPath, 'utf8');
  } catch (e) {
    throw new Error('CLAIM_READ_ERROR: cannot read request file: ' + (e.message || e));
  }

  let request;
  try {
    request = JSON.parse(raw);
  } catch (e) {
    throw new Error('CLAIM_INVALID_JSON: request file is not valid JSON: ' + (e.message || e));
  }

  if (!request || typeof request !== 'object' || Array.isArray(request)) {
    throw new Error('CLAIM_INVALID_REQUEST: request must be a JSON object');
  }

  // 3. Exclusive creation of processing file using O_EXCL ('wx')
  // This is atomic at OS level — only one caller succeeds.
  let fd = -1;
  try {
    fd = fs.openSync(processingPath, 'wx');
  } catch (e) {
    if (e.code === 'EEXIST') {
      throw new Error('CLAIM_ALREADY_CLAIMED: processing file already exists: ' + processingPath);
    }
    throw new Error('CLAIM_CREATE_ERROR: cannot create processing file: ' + (e.message || e));
  }

  // 4. Write request content to processing file with claimed status
  try {
    const processingData = Object.assign({}, request, { status: 'claimed' });
    const data = JSON.stringify(processingData, null, 2);
    fs.writeSync(fd, data);
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = -1;
  } catch (e) {
    if (fd !== -1) {
      try { fs.closeSync(fd); } catch (e2) {}
    }
    // Cleanup partial processing file
    try { fs.unlinkSync(processingPath); } catch (e2) {}
    throw new Error('CLAIM_WRITE_ERROR: cannot write processing file: ' + (e.message || e));
  }

  // 5. Remove original request file (best effort — processing file is the authority now)
  try {
    fs.unlinkSync(requestPath);
  } catch (e) {
    // If unlink fails, do not fail — processing file exists.
    // The request file will be cleaned up later or detected as duplicate.
    log(context, 'warn', 'claim: could not remove request file ' + requestPath + ': ' + (e && e.message ? e.message : String(e)));
  }
  // Directory fsync after deletion: EPERM/ENOTSUP/EINVAL degrade with a warning,
  // every other error is propagated to the caller.
  fsyncDirectory(path.dirname(requestPath), 'claim: fsync requests/ after unlink', context);

  // P0.3: Write dedup record with status "processing" (only if not already completed)
  // P0.5: processing file already contains request data; status will be updated by caller
  const rid = (request.id || request.requestId || null);
  // Check if there's already a completed dedup record to avoid regression
  if (rid !== null) {
    const existingDedup = readDedupRecord(dirs.dedupDir, rid);
    if (!existingDedup || existingDedup.status !== 'completed') {
      writeDedup(dirs.dedupDir, rid, 'processing', null, context);
    } else {
      log(context, 'info', 'claim skipped dedup write: request ' + rid + ' already completed');
    }
  }
  return {
    claimed: true,
    requestPath: requestPath,
    processingPath: processingPath,
    requestId: rid
  };
}

// Step 5: ACK processing
function handleAck(ackPath, context, dirs) {
  let ack = null;
  let ackId = null;
  try {
    const raw = fs.readFileSync(ackPath, 'utf8');
    try {
      ack = JSON.parse(raw);
    } catch (parseError) {
      log(context, 'warn', 'invalid ACK JSON: ' + parseError.message);
      // Move to processed (invalid)
      const processedName = 'ack.processed.invalid-' + Date.now() + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      return;
    }

    if (!ack || typeof ack !== 'object' || Array.isArray(ack)) {
      log(context, 'warn', 'ACK is not an object');
      const processedName = 'ack.processed.invalid-' + Date.now() + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      return;
    }

    const requestId = (typeof ack.requestId === 'string' && ack.requestId !== '') ? ack.requestId : null;
    ackId = requestId;

    // Validate protocolVersion
    if (ack.protocolVersion !== PROTOCOL_VERSION) {
      log(context, 'warn', 'ACK unsupported protocol version');
      const processedName = 'ack.processed.invalid-' + (requestId || 'unknown') + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      return;
    }

    if (!requestId) {
      log(context, 'warn', 'ACK missing requestId');
      const processedName = 'ack.processed.invalid-' + Date.now() + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      return;
    }

    // P0.4: requestId is used in file paths below; reject traversal-capable ids.
    if (!isSafeRequestId(requestId)) {
      log(context, 'warn', 'ACK has unsafe requestId: ' + JSON.stringify(requestId));
      const processedName = 'ack.processed.invalid-id-' + Date.now() + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      return;
    }

    // Validate checksum if present
    if (ack.checksum) {
      try {
        if (!verifyChecksum(ack, ack.checksum)) {
          log(context, 'warn', 'ACK checksum mismatch for ' + requestId);
          const processedName = 'ack.processed.checksum_mismatch-' + String(requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
          try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
          return;
        }
      } catch (e) {
        log(context, 'warn', 'ACK checksum error: ' + String(e));
        const processedName = 'ack.processed.checksum_error-' + String(requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
        try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e2) {}
        return;
      }
    }

    // Check if response exists
    const responseFile = path.join(dirs.responsesDir, String(requestId) + '.json');
    if (!fs.existsSync(responseFile)) {
      log(context, 'warn', 'ACK for unknown requestId: ' + requestId);
      const processedName = 'ack.processed.unknown-' + String(requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      return;
    }

    // Verify ACK matches existing response (check requestId consistency)
    let responseData = null;
    try {
      responseData = JSON.parse(fs.readFileSync(responseFile, 'utf8'));
    } catch (e) {
      log(context, 'warn', 'cannot read response for ACK verification: ' + String(e));
      const processedName = 'ack.processed.error-' + String(requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e2) {}
      return;
    }

    // R2 fix: verify response.requestId matches ack.requestId
    const responseRequestId = (responseData && responseData.requestId) || null;
    if (typeof responseRequestId !== 'string' || responseRequestId === '' || responseRequestId !== String(requestId)) {
      log(context, 'warn', 'ACK requestId mismatch: ack=' + requestId + ', response=' + responseRequestId);
      const processedName = 'ack.processed.mismatch-' + String(requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
      try { fs.renameSync(ackPath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      return;
    }

    // Check if already acked (idempotent)
    const ackedMarker = path.join(dirs.acksDir, String(requestId) + '.acked');
    if (fs.existsSync(ackedMarker)) {
      // Already acked — idempotent, just delete ACK (step 3)
      log(context, 'info', 'duplicate ACK for ' + requestId + ' (already acked)');
      tryDeleteAck(ackPath, dirs, context);
      return;
    }

    // P0.4 two-phase ACK protocol:
    // Step 1: synchronise dedup/ (ackProcessed=true, ackProcessedAt, fsync)
    const now = new Date().toISOString();
    if (!writeDedup(dirs.dedupDir, requestId, 'completed', {
      ackProcessed: true,
      ackProcessedAt: now
    }, context)) {
      log(context, 'error', 'P0.4: dedup ackProcessed update failed — ACK processing aborted');
      return;
    }

    // Step 2: create acks/<id>.acked (ackedAt = ackProcessedAt, fsync)
    try {
      writeJson(ackedMarker, { requestId: requestId, ackedAt: now }, context, 'create .acked marker');
    } catch (e) {
      log(context, 'error', 'P0.4: failed to create .acked marker — ACK processing aborted: ' + (e && e.message ? e.message : String(e)));
      return;
    }

    // A later delivery of the same requestId can legitimately receive another
    // ACK_CONFIRMED file at the same path. Allow pollOnce() to observe that new
    // confirmation instead of treating the filename as already settled.
    if (dirs.settledConfirmations) {
      dirs.settledConfirmations.delete(requestId + '.ack_confirmed.json');
    }
    if (dirs.deferredConfirmations) {
      dirs.deferredConfirmations.delete(requestId + '.ack_confirmed.json');
    }

    // Step 3: delete incoming acks/<id>.ack.json with directory fsync
    tryDeleteAck(ackPath, dirs, context);
    log(context, 'info', 'ACK processed for ' + requestId);

    // NOTE: Response is NOT deleted here. Deletion is governed by TTL/recovery (future steps).
  } catch (error) {
    log(context, 'error', 'ACK handling failed: ' + String(error));
    try {
      const processedName = 'ack.processed.error-' + Date.now() + '.json';
      fs.renameSync(ackPath, path.join(dirs.processedDir, processedName));
    } catch (e) {}
  }
}

// P0.4: delete incoming ACK file with directory fsync (step 3)
function tryDeleteAck(ackPath, dirs, context) {
  if (!fs.existsSync(ackPath)) return true;
  let removed = false;
  try {
    fs.unlinkSync(ackPath);
    removed = true;
  } catch (e) {
    if (e && e.code === 'ENOENT') return true;
    // Fallback: move to processed if unlink fails (best effort)
    log(context, 'warn', 'P0.4: unlink ack failed, moving to processed: ' + String(e));
    try {
      const processedName = 'ack.processed.' + path.basename(ackPath, '.json') + '.json';
      fs.renameSync(ackPath, path.join(dirs.processedDir, processedName));
    } catch (e2) {}
  }
  // Unsupported directory fsync degrades with a warning; any other error propagates.
  if (removed) {
    fsyncDirectory(dirs.acksDir, 'unlink incoming ACK (acks/<id>.ack.json)', context);
  }
  return removed;
}

// P0.4: delete processing/<id>.json with directory fsync (step 7)
function tryDeleteProcessing(processingPath, dirs, context) {
  if (!fs.existsSync(processingPath)) return true;
  let removed = false;
  try {
    fs.unlinkSync(processingPath);
    removed = true;
  } catch (e) {
    if (e && e.code === 'ENOENT') return true;
    log(context, 'warn', 'P0.4: unlink processing failed: ' + String(e));
  }
  // Unsupported directory fsync degrades with a warning; any other error propagates.
  if (removed) {
    fsyncDirectory(dirs.processingDir, 'unlink processing/<id>.json', context);
  }
  return removed;
}

// P0.4: isolate an invalid ACK_CONFIRMED file without destroying it.
// The file gets an ".invalid" suffix so polling no longer picks it up.
function quarantineInvalidConfirmation(confirmedPath, context) {
  const target = confirmedPath + '.invalid';
  try {
    if (fs.existsSync(target)) {
      fs.renameSync(confirmedPath, confirmedPath + '.' + Date.now() + '.invalid');
    } else {
      fs.renameSync(confirmedPath, target);
    }
  } catch (e) {
    log(context, 'error', 'P0.4: failed to quarantine invalid ACK_CONFIRMED ' + confirmedPath + ': ' + (e && e.message ? e.message : String(e)));
  }
}

function isValidIsoTimestamp(value) {
  if (typeof value !== 'string' || value === '') return false;
  return !isNaN(Date.parse(value));
}

// P0.4 step 5: handle client_state/<id>.ack_confirmed.json
//
// Returns { ok, status, requestId } where status is one of:
//   confirmed         — dedup updated, cleanup executed
//   already_confirmed — dedup already had ackConfirmed=true; cleanup executed
//   orphan            — no dedup record; nothing written, file kept for recovery
//   invalid           — file quarantined with ".invalid"
//   read_error        — dedup unreadable/parse error; retry later
//   dedup_error       — dedup write failed; .acked/processing kept for retry
//
// The confirmation file itself is NOT deleted in P0.
// Specification v2 §6.2/§10.2 assigns its deletion to the 30-day TTL (P1).
function handleAckConfirmed(confirmedPath, context, dirs) {
  const fileName = path.basename(confirmedPath);
  let confirmed = null;
  try {
    const raw = fs.readFileSync(confirmedPath, 'utf8');
    confirmed = JSON.parse(raw);
  } catch (error) {
    log(context, 'warn', 'P0.4: invalid ACK_CONFIRMED ' + fileName + ': ' + (error && error.message ? error.message : String(error)));
    quarantineInvalidConfirmation(confirmedPath, context);
    return { ok: false, status: 'invalid', requestId: null };
  }

  if (!confirmed || typeof confirmed !== 'object' || Array.isArray(confirmed)) {
    log(context, 'warn', 'P0.4: ACK_CONFIRMED ' + fileName + ' is not a JSON object');
    quarantineInvalidConfirmation(confirmedPath, context);
    return { ok: false, status: 'invalid', requestId: null };
  }

  if (confirmed.protocolVersion !== PROTOCOL_VERSION) {
    log(context, 'warn', 'P0.4: ACK_CONFIRMED ' + fileName + ' has unsupported protocolVersion ' + JSON.stringify(confirmed.protocolVersion));
    quarantineInvalidConfirmation(confirmedPath, context);
    return { ok: false, status: 'invalid', requestId: null };
  }

  const requestId = confirmed.requestId;
  if (!isSafeRequestId(requestId)) {
    log(context, 'warn', 'P0.4: ACK_CONFIRMED ' + fileName + ' has unsafe requestId ' + JSON.stringify(String(requestId)));
    quarantineInvalidConfirmation(confirmedPath, context);
    return { ok: false, status: 'invalid', requestId: null };
  }

  // The file name is part of the contract: client_state/<requestId>.ack_confirmed.json.
  // A mismatch means the file cannot be trusted to describe <requestId>.
  if (fileName !== requestId + '.ack_confirmed.json') {
    log(context, 'warn', 'P0.4: ACK_CONFIRMED file name does not match requestId: ' + fileName + ' vs ' + requestId);
    quarantineInvalidConfirmation(confirmedPath, context);
    return { ok: false, status: 'invalid', requestId: null };
  }

  let existing = null;
  try {
    existing = readDedupRecord(dirs.dedupDir, requestId);
  } catch (error) {
    log(context, 'error', 'P0.4: cannot read dedup record for ' + requestId + ': ' + (error && error.message ? error.message : String(error)));
    return { ok: false, status: 'read_error', requestId: requestId };
  }

  if (existing === null) {
    // Orphan confirmation: never create a dedup record from scratch here —
    // that would lose result/dedupCreatedAt and hide an inconsistent state.
    log(context, 'warn', 'P0.4: orphan ACK_CONFIRMED for ' + requestId + ' (no dedup record) — left in place for recovery');
    return { ok: false, status: 'orphan', requestId: requestId };
  }

  const alreadyConfirmed = existing.ackConfirmed === true;
  if (!alreadyConfirmed) {
    // Specification v2 §6.1 step 17 wants the client confirmation time.
    // The client writes "confirmedAt"; fall back to now only when it is absent
    // or not a parseable timestamp.
    const ackConfirmedAt = isValidIsoTimestamp(confirmed.confirmedAt)
      ? confirmed.confirmedAt
      : new Date().toISOString();
    const merged = mergeDedup(dirs.dedupDir, requestId, {
      ackConfirmed: true,
      ackConfirmedAt: ackConfirmedAt
    }, context, { allowCreate: false });
    if (!merged.ok) {
      // Do not touch .acked or processing: a retry must be able to finish.
      log(context, 'error', 'P0.4: dedup ackConfirmed update failed for ' + requestId + ' — cleanup deferred');
      return { ok: false, status: 'dedup_error', requestId: requestId };
    }
  } else {
    log(context, 'info', 'P0.4: ACK_CONFIRMED already recorded for ' + requestId + ' — completing cleanup');
  }

  // Step 6: delete acks/<id>.acked (best effort, no mandatory fsync).
  const ackedMarker = path.join(dirs.acksDir, requestId + '.acked');
  try {
    fs.unlinkSync(ackedMarker);
  } catch (e) {
    if (e && e.code !== 'ENOENT') {
      log(context, 'warn', 'P0.4: could not remove .acked for ' + requestId + ': ' + (e.message ? e.message : String(e)));
    }
  }

  // Step 7: delete processing/<id>.json only after ackConfirmed=true.
  const processingPath = path.join(dirs.processingDir, requestId + '.json');
  try {
    tryDeleteProcessing(processingPath, dirs, context);
  } catch (e) {
    log(context, 'warn', 'P0.4: processing cleanup deferred for ' + requestId + ': ' + (e && e.message ? e.message : String(e)));
  }

  log(context, 'info', 'P0.4: ACK_CONFIRMED processed for ' + requestId + (alreadyConfirmed ? ' (idempotent)' : ''));
  return { ok: true, status: alreadyConfirmed ? 'already_confirmed' : 'confirmed', requestId: requestId };
}

// Vortex 2.10 derives the active game from the active profile; session.gameMode
// holds the detected-games tree, not an id.
function activeGameIdFor(state) {
  const direct = navigateState(state, ['session', 'gameMode']);
  if (typeof direct === 'string' && direct !== '') return direct;

  const profileId = navigateState(state, ['settings', 'profiles', 'activeProfileId']);
  if (typeof profileId === 'string' && profileId !== '') {
    const gameId = navigateState(state, ['persistent', 'profiles', profileId, 'gameId']);
    if (typeof gameId === 'string' && gameId !== '') return gameId;
  }
  return null;
}

// host.json is generated by the OVLS installer next to this file. v1 is
//   { "modsRoot": "Z:\\home\\deck\\Vortex Mods" }
// v2 adds volume anchors generated from the real Steam libraries:
//   { "modsRoot": "...", "volumes": [
//       { "anchor": "Z:\\home\\deck", "modsRoot": "Z:\\home\\deck\\Vortex Mods" } ] }
// Without a valid file the staging fix stays passive.
function hostConfigPath(hostPath) {
  return hostPath || path.join(__dirname, 'host.json');
}

function normalizedWindowsPath(value) {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const normalized = value.trim().replace(/\//g, '\\').replace(/\\+/g, '\\');
  try {
    if (!path.win32.parse(normalized).root) return null;
  } catch (error) {
    return null;
  }
  return normalized.toLowerCase();
}

// Windows paths are case-insensitive and a directory boundary is required:
// "Z:\\sdcard2" must not match the anchor "Z:\\sdcard".
function pathUnderAnchor(filePath, anchor) {
  if (filePath === null || anchor === null) return false;
  if (filePath === anchor) return true;
  const boundary = anchor.endsWith('\\') ? anchor : anchor + '\\';
  return filePath.startsWith(boundary);
}

function volumeForPath(volumes, filePath) {
  const normalized = normalizedWindowsPath(filePath);
  if (normalized === null || !Array.isArray(volumes)) return null;
  let best = null;
  for (const volume of volumes) {
    const anchor = normalizedWindowsPath(volume && volume.anchor);
    if (!anchor || !pathUnderAnchor(normalized, anchor)) continue;
    if (best === null || anchor.length > best.anchor.length) {
      best = { anchor: anchor, modsRoot: volume.modsRoot };
    }
  }
  return best;
}

function readHostConfig(hostPath) {
  const file = hostConfigPath(hostPath);
  try {
    if (!fs.existsSync(file)) return null;
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!parsed || typeof parsed.modsRoot !== 'string' || parsed.modsRoot.trim() === '') {
      return null;
    }
    const config = { modsRoot: parsed.modsRoot };
    if (Object.prototype.hasOwnProperty.call(parsed, 'volumes')) {
      if (!Array.isArray(parsed.volumes) || parsed.volumes.length === 0) return null;
      const volumes = [];
      for (const volume of parsed.volumes) {
        if (!volume
            || typeof volume.anchor !== 'string' || volume.anchor.trim() === ''
            || typeof volume.modsRoot !== 'string' || volume.modsRoot.trim() === '') {
          return null;
        }
        if (normalizedWindowsPath(volume.anchor) === null
            || normalizedWindowsPath(volume.modsRoot) === null) {
          return null;
        }
        volumes.push(volume);
      }
      config.volumes = volumes;
    }
    return config;
  } catch (error) {
    // A broken host.json must only disable the fix, never break the extension.
  }
  return null;
}

function wineDrive(filePath) {
  try {
    const root = path.win32.parse(String(filePath)).root;
    return root === '' ? null : root.toLowerCase();
  } catch (error) {
    return null;
  }
}

// Vortex falls back to "{USERDATA}/{GAME}/mods" when the game has no explicit
// staging folder (mod_management/util/getInstallPath.ts), so an unset value
// still means "staging on the userData drive". Without this the fix would never
// be offered for the common case where the user never saved a staging folder.
function stagingPathFor(context, state, gameId) {
  const explicit = navigateState(state, ['settings', 'mods', 'installPath', gameId]);
  if (typeof explicit === 'string' && explicit !== '') return explicit;
  try {
    if (typeof context.api.getPath === 'function') {
      const userData = context.api.getPath('userData');
      if (typeof userData === 'string' && userData !== '') {
        return path.win32.join(userData, gameId, 'mods');
      }
    }
  } catch (error) {
    // Without userData the staging folder is unknown; the fix stays passive.
  }
  return null;
}

// Returns the staging problem for the active game or null when there is
// nothing OVLS can offer. Never writes anything.
function inspectStaging(context, hostPath) {
  const state = context.api.getState();
  const gameId = activeGameIdFor(state);
  if (!gameId) return { info: null, reason: 'no active game' };

  const gamePath = navigateState(state, ['settings', 'gameMode', 'discovered', gameId, 'path']);
  if (typeof gamePath !== 'string' || gamePath === '') {
    return { info: null, reason: 'game path unknown' };
  }

  const stagingPath = stagingPathFor(context, state, gameId);
  if (stagingPath === null) return { info: null, reason: 'staging path unknown' };

  const host = readHostConfig(hostPath);
  if (!host) {
    // Report the real reason before any v1 drive comparison: a corrupt v2 map
    // must not be logged as "same drive" just because both paths share Z:.
    const mods = navigateState(state, ['persistent', 'mods', gameId]);
    const modCount = mods !== null && typeof mods === 'object' ? Object.keys(mods).length : 0;
    const reason = fs.existsSync(hostConfigPath(hostPath)) ? 'invalid host.json' : 'no host.json';
    return {
      info: { gameId, gamePath, stagingPath, modCount, target: null, reason },
      reason
    };
  }
  let volume = null;
  if (host.volumes) {
    volume = volumeForPath(host.volumes, gamePath);
    if (volume === null) return { info: null, reason: 'unknown volume' };
    const normalizedStaging = normalizedWindowsPath(stagingPath);
    if (normalizedStaging === null) return { info: null, reason: 'staging path unknown' };
    if (pathUnderAnchor(normalizedStaging, volume.anchor)) {
      return { info: null, reason: 'same volume' };
    }
  } else {
    // v1 host.json (or no usable file): keep the original drive comparison.
    const gameDrive = wineDrive(gamePath);
    const stagingDrive = wineDrive(stagingPath);
    if (gameDrive === null || stagingDrive === null || gameDrive === stagingDrive) {
      return { info: null, reason: 'same drive' };
    }
  }

  const mods = navigateState(state, ['persistent', 'mods', gameId]);
  const modCount = mods !== null && typeof mods === 'object' ? Object.keys(mods).length : 0;

  const modsRoot = volume ? volume.modsRoot : host.modsRoot;
  const target = path.win32.join(modsRoot, gameId);
  if (volume) {
    if (!pathUnderAnchor(normalizedWindowsPath(target), volume.anchor)) {
      return { info: null, reason: 'mods root outside volume' };
    }
  } else if (wineDrive(target) !== wineDrive(gamePath)) {
    return {
      info: { gameId, gamePath, stagingPath, modCount, target: null, reason: 'mods root is on another drive' },
      reason: 'mods root is on another drive'
    };
  }
  return { info: { gameId, gamePath, stagingPath, modCount, target, reason: null }, reason: null };
}

function evaluateStagingFix(context, hostPath) {
  return inspectStaging(context, hostPath).info;
}

// Logged once per distinct state so a missing offer can be explained from
// vortex.log without adding an interface.
let lastStagingReport = null;

function reportStagingState(context, hostPath) {
  const state = context.api.getState();
  const inspected = inspectStaging(context, hostPath);
  const info = inspected.info;
  const gameId = activeGameIdFor(state);
  const report = {
    gameId: gameId || null,
    gamePath: info ? info.gamePath : navigateState(state, ['settings', 'gameMode', 'discovered', gameId || '', 'path']) || null,
    stagingPath: info ? info.stagingPath : (gameId ? stagingPathFor(context, state, gameId) : null),
    target: info ? info.target : null,
    modCount: info ? info.modCount : null,
    deployFailed: hadDeployFailure(state),
    offer: info !== null && info.target !== null && info.modCount === 0 ? 'yes' : 'no',
    reason: inspected.reason || 'ok'
  };
  const key = JSON.stringify(report);
  if (key === lastStagingReport) return;
  lastStagingReport = key;
  log(context, 'info', 'staging check: ' + key);
}

function listNotifications(state) {
  const notifications = navigateState(state, ['session', 'notifications']);
  if (Array.isArray(notifications)) return notifications;
  if (notifications && Array.isArray(notifications.notifications)) {
    return notifications.notifications;
  }
  return [];
}

function hadDeployFailure(state) {
  return listNotifications(state).some(function (notification) {
    return notification && notification.id === VORTEX_DEPLOY_FAILURE_ID;
  });
}

function reportStagingFixFailure(context, error) {
  log(context, 'error', 'staging fix failed: ' + String(error && error.message ? error.message : error));
  try {
    if (typeof context.api.showErrorNotification === 'function') {
      context.api.showErrorNotification(
        'OVLS: could not change the mod staging folder',
        error instanceof Error ? error : new Error(String(error))
      );
    }
  } catch (notifyError) {
    // Nothing else to do: the failure is already logged.
  }
}

// Vortex's deployment methods stat the staging folder and read its tag before
// deploying. Pointing the setting at a folder that does not exist yet makes
// every activator fail with ENOENT ("Mods can't be deployed.") until Vortex
// creates it. Create the folder and the same tag Vortex writes itself
// (mod_management/stagingDirectory.ts, writeStagingTag) beforehand.
function prepareStagingTarget(context, info, fsImpl) {
  const io = fsImpl || fs;
  const instanceId = navigateState(context.api.getState(), ['app', 'instanceId']);
  if (typeof instanceId !== 'string' || instanceId === '') {
    throw new Error('Vortex instance id is not available, the staging folder was not prepared');
  }

  io.mkdirSync(info.target, { recursive: true });
  const tagPath = path.win32.join(info.target, STAGING_DIR_TAG);
  if (!io.existsSync(tagPath)) {
    io.writeFileSync(
      tagPath,
      JSON.stringify({ instance: instanceId, game: info.gameId }),
      'utf8'
    );
  }
}

function applyStagingFix(context, info, fsImpl) {
  const api = context.api;
  log(context, 'info', 'applying staging fix: ' + info.gameId + ' -> ' + info.target);
  try {
    prepareStagingTarget(context, info, fsImpl);
    if (!api.store || typeof api.store.dispatch !== 'function') {
      throw new Error('Vortex store is not available, the staging setting was not changed');
    }
    api.store.dispatch({
      type: SET_MOD_INSTALL_PATH,
      payload: { gameId: info.gameId, path: info.target }
    });
  } catch (error) {
    reportStagingFixFailure(context, error);
    return;
  }

  // The action is an internal Vortex detail: verify it really took effect
  // instead of trusting it silently.
  setTimeout(function () {
    try {
      const applied = navigateState(api.getState(), ['settings', 'mods', 'installPath', info.gameId]);
      if (applied !== info.target) {
        reportStagingFixFailure(
          context,
          new Error('Vortex did not apply the staging path (expected ' + info.target + ', got ' + String(applied) + ')')
        );
        return;
      }
      try { api.dismissNotification(STAGING_FIX_NOTIFICATION_ID); } catch (dismissError) {}
      log(context, 'info', 'staging fix applied: ' + applied);
    } catch (error) {
      reportStagingFixFailure(context, error);
    }
  }, STAGING_FIX_APPLY_DELAY_MS);
}

// Offered only when Vortex itself reports that no deployment method works,
// the staging folder is empty and host.json provides a same-drive location.
function maybeOfferStagingFix(context, hostPath) {
  const state = context.api.getState();
  const info = evaluateStagingFix(context, hostPath);
  const deployFailed = hadDeployFailure(state);
  reportStagingState(context, hostPath);
  const shown = listNotifications(state).some(function (notification) {
    return notification && notification.id === STAGING_FIX_NOTIFICATION_ID;
  });
  const canFix =
    info !== null && info.target !== null && info.modCount === 0 && deployFailed;

  if (!canFix) {
    if (shown) {
      try { context.api.dismissNotification(STAGING_FIX_NOTIFICATION_ID); } catch (error) {}
    }
    return;
  }

  // Never nag about the same game + staging folder after the user dismissed it.
  const offerKey = info.gameId + '|' + info.stagingPath;
  if (offeredStagingFixes.has(offerKey)) return;
  offeredStagingFixes.add(offerKey);

  if (typeof context.api.sendNotification !== 'function') return;
  try {
    context.api.sendNotification({
      id: STAGING_FIX_NOTIFICATION_ID,
      type: 'warning',
      title: 'OVLS',
      message:
        'The mod staging folder is not on the same drive as the game (' + info.gameId +
        '), so Vortex cannot deploy mods. Move it to ' + info.target + '?',
      actions: [{
        title: 'Move staging folder',
        action: function () { applyStagingFix(context, info); }
      }]
    });
    log(context, 'info', 'staging fix offered: ' + info.gameId + ' -> ' + info.target);
  } catch (error) {
    log(context, 'warn', 'could not show staging fix notification: ' + String(error));
  }
}

function handleProcessing(filePath, context, dirs) {
  let requestId = null;
  let response = null;

  try {
    const raw = fs.readFileSync(filePath, 'utf8');

    let request;
    try {
      request = JSON.parse(raw);
    } catch (parseError) {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: null,
        status: 'error',
        error: {
          code: 'INVALID_JSON',
          message: 'Request file does not contain valid JSON: ' + parseError.message
        }
      };
      writeJson(path.join(dirs.responsesDir, 'response-' + Date.now() + '.json'), response, context, 'invalid-json error response');
      fs.renameSync(filePath, path.join(dirs.processedDir, 'request.processed.invalid-' + Date.now() + '.json'));
      return;
    }

    requestId = (request && (request.id || request.requestId)) || null;

    // P0.4: requestId is interpolated into response/processed paths below.
    // Reject unsafe ids instead of allowing them to escape the IPC root.
    if (requestId !== null && !isSafeRequestId(String(requestId))) {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: null,
        status: 'error',
        error: {
          code: 'INVALID_REQUEST_ID',
          message: 'Request id must be a safe single path component'
        }
      };
      response.checksum = computeChecksum(response);
      const responseFile = path.join(dirs.responsesDir, 'unknown-' + Date.now() + '.json');
      writeJson(responseFile, response, context, 'invalid-request-id response');
      const processedName = 'request.processed.invalid-id-' + Date.now() + '.json';
      try { fs.renameSync(filePath, path.join(dirs.processedDir, processedName)); } catch (e) {}
      log(context, 'warn', 'rejected request with unsafe id: ' + JSON.stringify(String(requestId)));
      return;
    }

    // P0.5: Update processing status to "executing"
    updateProcessingStatus(filePath, 'executing', context);

    // Verify checksum if present (Step 3)
    // The processing file is a superset of the original request: claimRequest()
    // adds "status" (and later "result"/"resultStoredAt"). The client's
    // checksum covers the request as sent, so verify it against the request
    // fields only, excluding processing metadata (Specification v2 §5.1).
    if (request && request.checksum) {
      try {
        const requestForChecksum = {};
        Object.keys(request).forEach(function (key) {
          if (key !== 'checksum' && key !== 'status' &&
              key !== 'result' && key !== 'resultStoredAt' &&
              key !== 'updatedAt' && key !== 'claimedAt') {
            requestForChecksum[key] = request[key];
          }
        });
        if (!verifyChecksum(requestForChecksum, request.checksum)) {
          response = {
            protocolVersion: PROTOCOL_VERSION,
            requestId: requestId,
            status: 'error',
            error: {
              code: 'CHECKSUM_MISMATCH',
              message: 'Request checksum verification failed'
            }
          };
          const responseFile = path.join(dirs.responsesDir, String(requestId === null ? 'unknown-' + Date.now() : requestId) + '.json');
          response.checksum = computeChecksum(response);
          writeJson(responseFile, response, context, 'response write');
          const processedName = 'request.processed.' + String(requestId === null ? 'unknown' : requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
          fs.renameSync(filePath, path.join(dirs.processedDir, processedName));
          return;
        }
      } catch (e) {
        response = {
          protocolVersion: PROTOCOL_VERSION,
          requestId: requestId,
          status: 'error',
          error: {
            code: 'CHECKSUM_ERROR',
            message: 'Checksum verification error: ' + String(e.message || e)
          }
        };
        const responseFile = path.join(dirs.responsesDir, String(requestId === null ? 'unknown-' + Date.now() : requestId) + '.json');
        response.checksum = computeChecksum(response);
        response.checksum = computeChecksum(response);
          writeJson(responseFile, response, context, 'response write');
        const processedName = 'request.processed.' + String(requestId === null ? 'unknown' : requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
        fs.renameSync(filePath, path.join(dirs.processedDir, processedName));
        return;
      }
    }

    if (!request || typeof request !== 'object' || Array.isArray(request)) {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: requestId,
        status: 'error',
        error: {
          code: 'INVALID_REQUEST',
          message: 'Request must be a JSON object'
        }
      };
    } else if (request.protocolVersion !== PROTOCOL_VERSION) {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: requestId,
        status: 'error',
        error: {
          code: 'UNSUPPORTED_PROTOCOL_VERSION',
          message: 'Expected protocolVersion ' + PROTOCOL_VERSION + ', got ' + JSON.stringify(request.protocolVersion)
        }
      };
    } else if (requestId === null || requestId === undefined || requestId === '') {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: null,
        status: 'error',
        error: {
          code: 'MISSING_REQUEST_ID',
          message: 'Request must contain a non-empty id'
        }
      };
    } else if (typeof request.type !== 'string' || request.type === '') {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: requestId,
        status: 'error',
        error: {
          code: 'MISSING_REQUEST_TYPE',
          message: 'Request must contain a non-empty type'
        }
      };
    } else {
      const payload = (request.payload && typeof request.payload === 'object' && !Array.isArray(request.payload)) ? request.payload : {};

      if (request.type === 'ping') {
        response = {
          protocolVersion: PROTOCOL_VERSION,
          requestId: requestId,
          status: 'success',
          result: {
            message: 'pong',
            timestamp: Date.now()
          }
        };
      } else if (request.type === 'getState') {
        const requestedPath = Array.isArray(payload.path) ? payload.path : ['settings'];
        if (!isSafePath(requestedPath)) {
          response = {
            protocolVersion: PROTOCOL_VERSION,
            requestId: requestId,
            status: 'error',
            error: {
              code: 'UNSAFE_STATE_PATH',
              message: 'Reading confidential state is not allowed'
            }
          };
        } else {
          const state = context.api.getState();
          const selectedValue = navigateState(state, requestedPath);
          response = {
            protocolVersion: PROTOCOL_VERSION,
            requestId: requestId,
            status: 'success',
            result: {
              stateKeys: Object.keys(state || {}),
              selectedPath: requestedPath,
              selectedValue: selectedValue
            }
          };
        }
      } else {
        response = {
          protocolVersion: PROTOCOL_VERSION,
          requestId: requestId,
          status: 'error',
          error: {
            code: 'UNKNOWN_REQUEST_TYPE',
            message: 'Unknown request type: ' + request.type
          }
        };
      }
    }

    const responseFile = path.join(dirs.responsesDir, String(requestId === null ? 'unknown-' + Date.now() : requestId) + '.json');
    response.checksum = computeChecksum(response);
    writeJson(responseFile, response, context, 'response write');
    // P0.5: Update processing status to "completed" before rename
    updateProcessingStatus(filePath, 'completed', context);

    const processedName = 'request.processed.' + String(requestId === null ? 'unknown' : requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
    const processedPath = path.join(dirs.processedDir, processedName);
    fs.renameSync(filePath, processedPath);
    // P0.6: Directory fsync after rename (source and destination)
    fsyncDirectory(path.dirname(filePath), 'rename processing -> processed: fsync source dir', context);
    fsyncDirectory(dirs.processedDir, 'rename processing -> processed: fsync destination dir', context);
    if (requestId !== null) {
      processedIds.add(String(requestId));
    }
    // P0.3: Update dedup record to "completed"
    if (requestId !== null) {
      writeDedup(dirs.dedupDir, requestId, 'completed', {
        result: (response && response.result !== undefined) ? response.result : null,
        resultStoredAt: new Date().toISOString()
      }, context);
    }
    log(context, 'info', 'processed request ' + requestId + ' with status ' + (response ? response.status : 'unknown'));
  } catch (error) {
    try {
      const failureResponse = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: requestId,
        status: 'error',
        error: {
          code: 'EXTENSION_ERROR',
          message: String(error && error.message ? error.message : error)
        }
      };
      const responseFile = path.join(dirs.responsesDir, String(requestId === null ? 'unknown-' + Date.now() : requestId) + '.json');
    failureResponse.checksum = computeChecksum(failureResponse);
    writeJson(responseFile, failureResponse, context, 'extension-error response');
      const processedName = 'request.processed.' + String(requestId === null ? 'unknown' : requestId).replace(/[^a-zA-Z0-9_-]/g, '_') + '.json';
      if (fs.existsSync(filePath)) {
        fs.renameSync(filePath, path.join(dirs.processedDir, processedName));
      }
    } catch (innerError) {
      log(context, 'error', 'failed to write error response: ' + String(innerError));
    }
    log(context, 'error', 'request handling failed: ' + String(error));
  }
}

// P0.4: build the directory layout for a known session.
// Used by createRuntime() and by the integration harness to simulate an
// Extension restart without changing the production session-discovery rules.
function buildRuntimeDirs(ipcDir, sessionId) {
  const sessionsDir = path.join(ipcDir, 'sessions');
  const sessionDir = path.join(sessionsDir, sessionId);
  const requestsDir = path.join(sessionDir, 'requests');
  const processingDir = path.join(sessionDir, 'processing');
  const responsesDir = path.join(sessionDir, 'responses');

  // Global directories (outside session-scoped, per spec v2 §3.3/§4.1)
  const acksDir = path.join(ipcDir, 'acks');
  const dedupDir = path.join(ipcDir, 'dedup');
  const processedDir = path.join(ipcDir, 'processed');
  const quarantineDir = path.join(ipcDir, 'quarantine');
  const clientStateDir = path.join(ipcDir, 'client_state');

  [ipcDir, sessionsDir, sessionDir, requestsDir, processingDir, responsesDir,
   acksDir, dedupDir, processedDir, quarantineDir, clientStateDir].forEach(function (dir) {
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  });

  return {
    ipcDir: ipcDir,
    sessionId: sessionId,
    sessionDir: sessionDir,
    requestsDir: requestsDir,
    processingDir: processingDir,
    responsesDir: responsesDir,
    acksDir: acksDir,
    dedupDir: dedupDir,
    processedDir: processedDir,
    quarantineDir: quarantineDir,
    clientStateDir: clientStateDir,
    // In-process bookkeeping for polling. Lost on restart on purpose: after a
    // restart each retained ACK_CONFIRMED is re-read once, which is safe because
    // handleAckConfirmed is idempotent.
    settledConfirmations: new Set(),
    deferredConfirmations: new Map()
  };
}

function createRuntime(context) {
  // P0.1: Session-scoped directories (sessions/<sessionId>/)
  // P0.2 blocked on OQ-5: sessionId generated for directory naming only,
  // not for decision-making. current_session.json NOT created (blocked).
  const userData = context.api.getPath('userData');
  const ipcDir = path.join(userData, 'OVLS_IPC_V2');
  const sessionId = crypto.randomUUID();
  const dirs = buildRuntimeDirs(ipcDir, sessionId);
  log(context, 'info', 'session-scoped dirs created: ' + dirs.sessionDir);
  log(context, 'info', 'dedup dir: ' + dirs.dedupDir);
  return dirs;
}

function listJsonFiles(dir) {
  return fs.readdirSync(dir)
    .filter(function (name) { return name.endsWith('.json'); })
    .sort()
    .map(function (name) { return path.join(dir, name); });
}

// A completed requestId must never re-enter the operation handler. Reuse its
// existing response when available; if it expired, rebuild a successful
// response from the durable Dedup result. Completed errors have no cached
// result in the current schema, so return DUPLICATE_REQUEST rather than rerun.
function handleCompletedDuplicate(requestPath, requestId, record, context, dirs) {
  const responsePath = path.join(dirs.responsesDir, requestId + '.json');
  let responseIsValid = false;

  if (fs.existsSync(responsePath)) {
    try {
      const existing = JSON.parse(fs.readFileSync(responsePath, 'utf8'));
      responseIsValid = existing && existing.protocolVersion === PROTOCOL_VERSION &&
        existing.requestId === requestId && existing.checksum &&
        verifyChecksum(existing, existing.checksum);
    } catch (e) {
      responseIsValid = false;
    }
  }

  if (!responseIsValid) {
    let response;
    if (record.result !== null && record.result !== undefined) {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: requestId,
        status: 'success',
        result: record.result
      };
    } else {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId: requestId,
        status: 'error',
        error: {
          code: 'DUPLICATE_REQUEST',
          message: 'This requestId has already completed and its result is not cached'
        }
      };
    }
    response.checksum = computeChecksum(response);
    writeJson(responsePath, response, context, 'restore completed duplicate response');
  }

  const processedPath = path.join(
    dirs.processedDir,
    'request.duplicate.' + requestId + '.' + crypto.randomUUID() + '.json'
  );
  fs.renameSync(requestPath, processedPath);
  fsyncDirectory(path.dirname(requestPath), 'archive completed duplicate: fsync requests/', context);
  fsyncDirectory(dirs.processedDir, 'archive completed duplicate: fsync processed/', context);
  log(context, 'info', 'duplicate request ' + requestId + ' served from completed dedup; operation not repeated');
  return { action: 'duplicate_request', file: requestPath, responseFile: responsePath };
}

const ACK_CONFIRMED_SUFFIX = '.ack_confirmed.json';
const CONFIRM_RETRY_MS = 5000;

// P0.4: one polling tick. Handles exactly one unit of work, preserving the
// existing priority order: processing/ -> acks/ -> client_state/ -> requests/.
// Returns a descriptor so tests and the harness can assert what happened.
function pollOnce(context, dirs) {
  try {
    // Scan processing/ for per-request files
    const processingFiles = listJsonFiles(dirs.processingDir);
    if (processingFiles.length > 0) {
      handleProcessing(processingFiles[0], context, dirs);
      return { action: 'processing', file: processingFiles[0] };
    }

    // Scan acks/ for ACK files
    const ackFiles = listJsonFiles(dirs.acksDir);
    if (ackFiles.length > 0) {
      handleAck(ackFiles[0], context, dirs);
      return { action: 'ack', file: ackFiles[0] };
    }

    // P0.4 step 5: poll client_state/ for ALL per-request ACK_CONFIRMED files.
    // The legacy fixed name "ack_confirmed.json" does not match the suffix and
    // is therefore ignored.
    if (!dirs.settledConfirmations) dirs.settledConfirmations = new Set();
    if (!dirs.deferredConfirmations) dirs.deferredConfirmations = new Map();
    const now = Date.now();
    const confirmedNames = fs.readdirSync(dirs.clientStateDir)
      .filter(function (name) { return name.endsWith(ACK_CONFIRMED_SUFFIX); })
      .sort();
    for (const name of confirmedNames) {
      if (dirs.settledConfirmations.has(name)) continue;
      const nextAttempt = dirs.deferredConfirmations.get(name);
      if (nextAttempt !== undefined && now < nextAttempt) continue;
      dirs.deferredConfirmations.delete(name);
      const confirmedPath = path.join(dirs.clientStateDir, name);
      const result = handleAckConfirmed(confirmedPath, context, dirs);
      if (result.status === 'confirmed' || result.status === 'already_confirmed' || result.status === 'invalid') {
        dirs.settledConfirmations.add(name);
      } else {
        // orphan / read_error / dedup_error: retry after a cooldown, keep the file.
        dirs.deferredConfirmations.set(name, Date.now() + CONFIRM_RETRY_MS);
      }
      return { action: 'ack_confirmed', file: confirmedPath, result: result };
    }

    // Scan requests/ for per-request files (session-scoped only; flat layout not used for requests)
    let requestFiles = listJsonFiles(dirs.requestsDir);
    if (requestFiles.length > 0) {
      const requestPath = requestFiles[0];
      const requestId = path.basename(requestPath, '.json');

      if (isSafeRequestId(requestId)) {
        const existingDedup = readDedupRecord(dirs.dedupDir, requestId);
        if (existingDedup && existingDedup.requestId === requestId && existingDedup.status === 'completed') {
          return handleCompletedDuplicate(requestPath, requestId, existingDedup, context, dirs);
        }
      }

      const processingPath = path.join(dirs.processingDir, requestId + '.json');
      try {
        const claimResult = claimRequest(requestPath, processingPath, context, dirs);
        // P0.3: Write dedup record with status "processing"
        writeDedup(dirs.dedupDir, claimResult.requestId, 'processing', null, context);
        log(context, 'info', 'claimed request ' + claimResult.requestId);
      } catch (claimError) {
        log(context, 'warn', 'could not claim request: ' + String(claimError));
        return { action: 'request_claim_failed', file: requestPath };
      }
      handleProcessing(processingPath, context, dirs);
      return { action: 'request', file: requestPath };
    }

    return { action: 'idle' };
  } catch (error) {
    log(context, 'error', 'polling failed: ' + String(error));
    return { action: 'error', error: String(error) };
  }
}

function init(context) {
  setTimeout(function () {
    try {
      const dirs = createRuntime(context);

      try {
        if (fs.existsSync(path.join(dirs.ipcDir, 'processed_ids.json'))) {
          const storedIds = JSON.parse(fs.readFileSync(path.join(dirs.ipcDir, 'processed_ids.json'), 'utf8'));
          if (Array.isArray(storedIds)) {
            storedIds.forEach(function (id) { processedIds.add(String(id)); });
          }
        }
      } catch (error) {
        log(context, 'warn', 'could not load processed_ids.json: ' + String(error));
      }

      const apiStatus = {
        loaded: true,
        loadedAt: new Date().toISOString(),
        protocolVersion: PROTOCOL_VERSION,
        ipcVersion: '9',
        apis: {
          getState: typeof context.api.getState === 'function',
          getPath: typeof context.api.getPath === 'function',
          storeGetState: !!(context.api.store && typeof context.api.store.getState === 'function'),
          eventsOn: !!(context.api.events && typeof context.api.events.on === 'function'),
          eventsEmit: !!(context.api.events && typeof context.api.events.emit === 'function'),
          log: typeof context.api.log === 'function'
        }
      };
      writeJson(path.join(dirs.ipcDir, 'status.json'), apiStatus, context, 'extension status');

      const state = context.api.getState();
      log(context, 'info', 'extension loaded; ipcDir=' + dirs.ipcDir);
      log(context, 'info', 'state keys: ' + Object.keys(state || {}).join(', '));
      log(context, 'info', 'api status: ' + JSON.stringify(apiStatus.apis));

      let isProcessing = false;
      setInterval(function () {
        if (isProcessing) return;
        isProcessing = true;
        try {
          maybeOfferStagingFix(context);
        } catch (error) {
          log(context, 'warn', 'staging check failed: ' + String(error));
        }
        try {
          pollOnce(context, dirs);
        } finally {
          isProcessing = false;
        }
      }, POLL_INTERVAL_MS);
    } catch (error) {
      log(context, 'error', 'initialization failed: ' + String(error));
    }
  }, 3000);

  return true;
}

init.handleAck = handleAck;
init.handleAckConfirmed = handleAckConfirmed;
init.claimRequest = claimRequest;
init.tryDeleteAck = tryDeleteAck;
init.tryDeleteProcessing = tryDeleteProcessing;
init.updateProcessingStatus = updateProcessingStatus;
init.writeDedup = writeDedup;
init.mergeDedup = mergeDedup;
init.readDedupRecord = readDedupRecord;
init.isSafeRequestId = isSafeRequestId;
init.writeJson = writeJson;
init.fsyncDirectory = fsyncDirectory;
init.isDirectoryFsyncUnsupported = isDirectoryFsyncUnsupported;
init.createRuntime = createRuntime;
init.buildRuntimeDirs = buildRuntimeDirs;
init.pollOnce = pollOnce;
init.PROTOCOL_VERSION = PROTOCOL_VERSION;
// Staging fix internals: exposed for the node test harness only.
init.evaluateStagingFix = evaluateStagingFix;
init.maybeOfferStagingFix = maybeOfferStagingFix;
init.applyStagingFix = applyStagingFix;
init.readHostConfig = readHostConfig;
init.volumeForPath = volumeForPath;
init.pathUnderAnchor = pathUnderAnchor;
init.normalizedWindowsPath = normalizedWindowsPath;
init.wineDrive = wineDrive;
init.activeGameIdFor = activeGameIdFor;
init.listNotifications = listNotifications;
init.resetOfferedStagingFixes = function () {
  offeredStagingFixes.clear();
  lastStagingReport = null;
};
module.exports = init;
