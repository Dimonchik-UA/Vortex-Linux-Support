'use strict';

// Test harness for the OVLS Extension staging fix. Runs with plain node:
//   node Tests/Installer/staging_fix_harness.js
// It never touches a real Vortex installation.

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const EXTENSION_PATH = path.join(
  __dirname, '..', '..', 'src', 'extension', 'index.js'
);
const init = require(EXTENSION_PATH);
const internals = init;

const MODS_ROOT = 'Z:\\home\\deck\\Vortex Mods';
const GAME_PATH = 'Z:\\home\\deck\\.local\\share\\Steam\\steamapps\\common\\Cyberpunk 2077';
const STAGING_PATH = 'C:\\users\\steamuser\\AppData\\Roaming\\Vortex\\cyberpunk2077\\mods';
const DEPLOY_FAILURE = 'test-valid-activator';
const NOTIFICATION_ID = 'ovls-staging-fix';
const SD_ANCHOR = 'Z:\\run\\media\\deck\\SD';
const SD_MODS_ROOT = SD_ANCHOR + '\\Vortex Mods';
const SD_GAME_PATH = SD_ANCHOR + '\\SteamLibrary\\steamapps\\common\\Cyberpunk 2077';
const HOME_STAGING = MODS_ROOT + '\\other-game\\mods';

function makeHostFile(dir, modsRoot, name) {
  const file = path.join(dir, name || 'host.json');
  fs.writeFileSync(file, JSON.stringify({ modsRoot: modsRoot }), 'utf8');
  return file;
}

function makeV2HostFile(dir, modsRoot, volumes, name) {
  const file = path.join(dir, name || 'host-v2.json');
  fs.writeFileSync(file, JSON.stringify({ modsRoot: modsRoot, volumes: volumes }), 'utf8');
  return file;
}

// Mode used by the Python generator tests: feed a real generated host.json to
// the Extension reader and require the SD-volume target.
function checkGeneratedHostFile(hostFile) {
  const state = makeState({ gamePath: SD_GAME_PATH, stagingPath: HOME_STAGING });
  const info = internals.evaluateStagingFix(makeContext(state).context, hostFile);
  if (!info || info.target !== SD_MODS_ROOT + '\\cyberpunk2077') {
    throw new Error('generated host.json was not consumed as expected: ' + JSON.stringify(info));
  }
  console.log('CROSS_HOST_OK');
}

// Mirrors Vortex 2.10.1: the active game comes from the active profile, and
// session.gameMode holds the detected-games tree. Notifications are nested.
function makeState(options) {
  const opts = options || {};
  const profileId = opts.activeProfileId === undefined ? 'profile-1' : opts.activeProfileId;
  return {
    app: { instanceId: opts.instanceId === undefined ? 'instance-1' : opts.instanceId },
    session: {
      gameMode: { known: [], detectedGames: [] },
      notifications: {
        notifications: opts.notifications === undefined ? [] : opts.notifications
      }
    },
    settings: {
      profiles: { activeProfileId: profileId },
      gameMode: {
        discovered: {
          cyberpunk2077: { path: opts.gamePath || GAME_PATH }
        }
      },
      mods: {
        installPath: { cyberpunk2077: opts.stagingPath || STAGING_PATH }
      }
    },
    persistent: {
      profiles: { 'profile-1': { id: 'profile-1', gameId: 'cyberpunk2077' } },
      mods: { cyberpunk2077: opts.mods === undefined ? {} : opts.mods }
    }
  };
}

function makeContext(state, options) {
  const opts = options || {};
  const sent = [];
  const dismissed = [];
  const errors = [];
  const dispatched = [];
  return {
    context: {
      api: {
        getState: function () { return state; },
        sendNotification: function (notification) { sent.push(notification); return notification.id; },
        dismissNotification: function (id) { dismissed.push(id); },
        showErrorNotification: function (message, error) { errors.push({ message: message, error: error }); },
        log: function () {},
        getPath: function (name) {
          return name === 'userData' ? (opts.userData || '') : '';
        },
        store: {
          dispatch: function (action) {
            dispatched.push(action);
            if (opts.dispatchApplies === false) return;
            if (action && action.type === 'SET_MOD_INSTALL_PATH') {
              state.settings.mods.installPath[action.payload.gameId] = action.payload.path;
            }
          }
        }
      }
    },
    sent: sent,
    dismissed: dismissed,
    errors: errors,
    dispatched: dispatched
  };
}

// Records the filesystem work of applyStagingFix without touching the disk.
function makeFakeFs(options) {
  const opts = options || {};
  const dirs = [];
  const files = {};
  const existing = opts.existing || {};
  Object.keys(existing).forEach(function (key) { files[key] = existing[key]; });
  return {
    mkdirSync: function (dir) {
      if (opts.failMkdir) throw new Error('EPERM: operation not permitted');
      dirs.push(dir);
    },
    existsSync: function (file) { return Object.prototype.hasOwnProperty.call(files, file); },
    writeFileSync: function (file, content) { files[file] = content; },
    dirs: dirs,
    files: files
  };
}

function wait(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ovls-staging-fix-'));
  const hostFile = makeHostFile(tmp, MODS_ROOT);
  const missingHost = path.join(tmp, 'missing-host.json');

  try {
    // evaluateStagingFix: different drives, empty staging, host.json present.
    let state = makeState();
    let info = internals.evaluateStagingFix(makeContext(state).context, hostFile);
    assert.ok(info, 'expected an offer-able staging problem');
    assert.strictEqual(info.gameId, 'cyberpunk2077');
    assert.strictEqual(info.modCount, 0);
    assert.strictEqual(info.target, MODS_ROOT + '\\cyberpunk2077');

    // Older Vortex shape: session.gameMode was the game id itself.
    state = makeState();
    state.session.gameMode = 'cyberpunk2077';
    info = internals.evaluateStagingFix(makeContext(state).context, hostFile);
    assert.ok(info, 'legacy session.gameMode string must still work');

    // No active profile -> no active game, nothing offered, no crash.
    state = makeState({ activeProfileId: '' });
    assert.strictEqual(internals.evaluateStagingFix(makeContext(state).context, hostFile), null);

    // Same drive already: nothing to offer.
    state = makeState({ stagingPath: 'Z:\\home\\deck\\Other Staging' });
    assert.strictEqual(internals.evaluateStagingFix(makeContext(state).context, hostFile), null);

    // Missing host.json disables the fix.
    state = makeState();
    info = internals.evaluateStagingFix(makeContext(state).context, missingHost);
    assert.ok(info);
    assert.strictEqual(info.target, null);
    assert.strictEqual(info.reason, 'no host.json');

    // modsRoot on another drive than the game is not usable.
    const otherHostFile = makeHostFile(tmp, 'D:\\Vortex Mods', 'host-d.json');
    info = internals.evaluateStagingFix(makeContext(state).context, otherHostFile);
    assert.strictEqual(info.target, null);

    // No offer without Vortex's own deployment failure.
    internals.resetOfferedStagingFixes();
    let harness = makeContext(makeState());
    internals.maybeOfferStagingFix(harness.context, hostFile);
    assert.strictEqual(harness.sent.length, 0, 'no notification without deploy failure');

    // Non-empty staging: no offer, even with the failure present.
    internals.resetOfferedStagingFixes();
    harness = makeContext(makeState({
      notifications: [{ id: DEPLOY_FAILURE }],
      mods: { someMod: { id: 'someMod' } }
    }));
    internals.maybeOfferStagingFix(harness.context, hostFile);
    assert.strictEqual(harness.sent.length, 0, 'no offer when staging holds mods');

    // Unset staging folder: Vortex falls back to {userData}/{game}/mods, which
    // is still a different drive than the game.
    internals.resetOfferedStagingFixes();
    state = makeState({ notifications: [{ id: DEPLOY_FAILURE }] });
    delete state.settings.mods.installPath.cyberpunk2077;
    harness = makeContext(state, {
      userData: 'C:\\users\\steamuser\\AppData\\Roaming\\Vortex'
    });
    info = internals.evaluateStagingFix(harness.context, hostFile);
    assert.ok(info, 'unset staging folder must still be detected');
    assert.strictEqual(
      info.stagingPath,
      'C:\\users\\steamuser\\AppData\\Roaming\\Vortex\\cyberpunk2077\\mods'
    );
    internals.maybeOfferStagingFix(harness.context, hostFile);
    assert.strictEqual(harness.sent.length, 1, 'unset staging folder must be offered');

    // Without userData the staging folder is unknown: offer nothing.
    internals.resetOfferedStagingFixes();
    state = makeState({ notifications: [{ id: DEPLOY_FAILURE }] });
    delete state.settings.mods.installPath.cyberpunk2077;
    harness = makeContext(state, { userData: '' });
    assert.strictEqual(internals.evaluateStagingFix(harness.context, hostFile), null);
    internals.maybeOfferStagingFix(harness.context, hostFile);
    assert.strictEqual(harness.sent.length, 0);

    // Offer exactly once per game + staging folder.
    internals.resetOfferedStagingFixes();
    harness = makeContext(makeState({ notifications: [{ id: DEPLOY_FAILURE }] }));
    internals.maybeOfferStagingFix(harness.context, hostFile);
    internals.maybeOfferStagingFix(harness.context, hostFile);
    assert.strictEqual(harness.sent.length, 1, 'must not nag about the same staging folder');
    const notification = harness.sent[0];
    assert.strictEqual(notification.id, NOTIFICATION_ID);
    assert.strictEqual(notification.actions.length, 1);
    assert.ok(notification.message.indexOf(MODS_ROOT + '\\cyberpunk2077') !== -1);

    // The action writes the staging path through Vortex's own store.
    const target = MODS_ROOT + '\\cyberpunk2077';
    const tagPath = target + '\\__vortex_staging_folder';
    harness = makeContext(makeState({ notifications: [{ id: DEPLOY_FAILURE }] }));
    const io = makeFakeFs();
    const offer = internals.evaluateStagingFix(harness.context, hostFile);
    internals.applyStagingFix(harness.context, offer, io);
    assert.strictEqual(harness.dispatched.length, 1);
    assert.deepStrictEqual(harness.dispatched[0], {
      type: 'SET_MOD_INSTALL_PATH',
      payload: { gameId: 'cyberpunk2077', path: target }
    });
    // The folder and Vortex's own tag must exist before the setting changes,
    // otherwise deployment fails with ENOENT until Vortex creates them.
    assert.deepStrictEqual(io.dirs, [target], 'staging folder must be created first');
    assert.strictEqual(
      io.files[tagPath],
      JSON.stringify({ instance: 'instance-1', game: 'cyberpunk2077' })
    );
    await wait(1300);
    assert.strictEqual(harness.errors.length, 0, 'applied action must not report an error');
    assert.ok(harness.dismissed.indexOf(NOTIFICATION_ID) !== -1, 'own notification must be dismissed');

    // An existing tag belongs to some other instance: never overwrite it.
    harness = makeContext(makeState());
    const ioTagged = makeFakeFs({
      existing: { [tagPath]: '{"instance":"other-instance","game":"cyberpunk2077"}' }
    });
    internals.applyStagingFix(harness.context, offer, ioTagged);
    assert.strictEqual(
      ioTagged.files[tagPath],
      '{"instance":"other-instance","game":"cyberpunk2077"}',
      'existing tag must be preserved'
    );

    // Cannot create the folder: the setting must not be pointed at it.
    harness = makeContext(makeState());
    const ioFail = makeFakeFs({ failMkdir: true });
    internals.applyStagingFix(harness.context, offer, ioFail);
    assert.strictEqual(harness.dispatched.length, 0, 'no setting change without a folder');
    assert.strictEqual(harness.errors.length, 1, 'prepare failure must surface an error');

    // No instance id: cannot write a valid tag, so do not change the setting.
    harness = makeContext(makeState({ instanceId: '' }));
    internals.applyStagingFix(harness.context, offer, makeFakeFs());
    assert.strictEqual(harness.dispatched.length, 0, 'no setting change without a tag');
    assert.strictEqual(harness.errors.length, 1);

    // A Vortex version that no longer understands the action is reported, not silent.
    harness = makeContext(makeState(), { dispatchApplies: false });
    internals.applyStagingFix(
      harness.context,
      { gameId: 'cyberpunk2077', target: target },
      makeFakeFs()
    );
    await wait(1300);
    assert.strictEqual(harness.errors.length, 1, 'unapplied action must surface an error');

    // A stale own notification disappears when the problem is gone.
    internals.resetOfferedStagingFixes();
    harness = makeContext(makeState({ notifications: [{ id: NOTIFICATION_ID }] }));
    internals.maybeOfferStagingFix(harness.context, hostFile);
    assert.ok(harness.dismissed.indexOf(NOTIFICATION_ID) !== -1);

    // ---- host.json v2: volume anchors from real Steam libraries ----
    const homeVolume = { anchor: 'Z:\\home\\deck', modsRoot: MODS_ROOT };
    const sdVolume = { anchor: SD_ANCHOR, modsRoot: SD_MODS_ROOT };
    const v2Host = makeV2HostFile(tmp, MODS_ROOT, [homeVolume, sdVolume], 'host-v2.json');

    // 1. Internal game + C: staging: previous behaviour is preserved.
    internals.resetOfferedStagingFixes();
    state = makeState();
    info = internals.evaluateStagingFix(makeContext(state).context, v2Host);
    assert.ok(info, 'internal game with C: staging must still be offered');
    assert.strictEqual(info.target, MODS_ROOT + '\\cyberpunk2077');

    // 2. SD game + staging in home: fix target is on the SD volume.
    state = makeState({ gamePath: SD_GAME_PATH, stagingPath: HOME_STAGING });
    info = internals.evaluateStagingFix(makeContext(state).context, v2Host);
    assert.ok(info, 'SD game with home staging must be offered');
    assert.strictEqual(info.target, SD_MODS_ROOT + '\\cyberpunk2077');

    // The notification must carry the SD target too, not just evaluateStagingFix.
    internals.resetOfferedStagingFixes();
    harness = makeContext(makeState({
      notifications: [{ id: DEPLOY_FAILURE }],
      gamePath: SD_GAME_PATH,
      stagingPath: HOME_STAGING
    }));
    internals.maybeOfferStagingFix(harness.context, v2Host);
    assert.strictEqual(harness.sent.length, 1, 'v2 SD fix must be offered');
    assert.ok(harness.sent[0].message.indexOf(SD_MODS_ROOT + '\\cyberpunk2077') !== -1);

    // The dispatched setting must use the volume target, not the v1 modsRoot.
    harness = makeContext(makeState({ gamePath: SD_GAME_PATH, stagingPath: HOME_STAGING }));
    const sdOffer = internals.evaluateStagingFix(harness.context, v2Host);
    const sdIo = makeFakeFs();
    internals.applyStagingFix(harness.context, sdOffer, sdIo);
    assert.deepStrictEqual(harness.dispatched[0], {
      type: 'SET_MOD_INSTALL_PATH',
      payload: { gameId: 'cyberpunk2077', path: SD_MODS_ROOT + '\\cyberpunk2077' }
    });
    assert.deepStrictEqual(sdIo.dirs, [SD_MODS_ROOT + '\\cyberpunk2077']);

    // 3. SD game + staging already under the SD anchor: no fix.
    state = makeState({
      gamePath: SD_GAME_PATH,
      stagingPath: SD_MODS_ROOT + '\\cyberpunk2077'
    });
    assert.strictEqual(
      internals.evaluateStagingFix(makeContext(state).context, v2Host), null,
      'staging on the game volume must not be offered'
    );

    // 4. host.json v1 without volumes: original drive comparison stays intact.
    state = makeState();
    info = internals.evaluateStagingFix(makeContext(state).context, hostFile);
    assert.ok(info, 'v1 must still offer the internal-game fix');
    assert.strictEqual(info.target, MODS_ROOT + '\\cyberpunk2077');
    state = makeState({ stagingPath: 'Z:\\home\\deck\\Other Staging' });
    assert.strictEqual(
      internals.evaluateStagingFix(makeContext(state).context, hostFile), null,
      'v1 must stay passive for the same drive'
    );

    // 5. Unknown volume and invalid volume maps leave the extension passive.
    const homeOnly = makeV2HostFile(tmp, MODS_ROOT, [homeVolume], 'host-home-only.json');
    const emptyVolumes = makeV2HostFile(tmp, MODS_ROOT, [], 'host-empty-volumes.json');
    const brokenVolumes = makeV2HostFile(
      tmp, MODS_ROOT, [{ anchor: 'relative\\path', modsRoot: MODS_ROOT }],
      'host-broken-volumes.json'
    );
    const nonArrayVolumes = makeV2HostFile(
      tmp, MODS_ROOT, { anchor: SD_ANCHOR, modsRoot: SD_MODS_ROOT },
      'host-nonarray-volumes.json'
    );
    const missingAnchor = makeV2HostFile(
      tmp, MODS_ROOT, [{ modsRoot: SD_MODS_ROOT }], 'host-missing-anchor.json'
    );
    const missingModsRoot = makeV2HostFile(
      tmp, MODS_ROOT, [{ anchor: SD_ANCHOR }], 'host-missing-modsroot.json'
    );
    const invalidJson = path.join(tmp, 'host-invalid.json');
    fs.writeFileSync(invalidJson, '{not json', 'utf8');
    state = makeState({ gamePath: SD_GAME_PATH, stagingPath: HOME_STAGING });
    assert.strictEqual(
      internals.evaluateStagingFix(makeContext(state).context, homeOnly), null,
      'unknown volume must stay passive'
    );
    [emptyVolumes, brokenVolumes, nonArrayVolumes, missingAnchor, missingModsRoot, invalidJson].forEach(function (file) {
      const result = internals.evaluateStagingFix(makeContext(state).context, file);
      assert.ok(result && result.target === null, 'invalid volumes must stay passive');
      assert.strictEqual(result.reason, 'invalid host.json', 'invalid map must be the reported reason');
      internals.resetOfferedStagingFixes();
      const passive = makeContext(makeState({ notifications: [{ id: DEPLOY_FAILURE }] }));
      internals.maybeOfferStagingFix(passive.context, file);
      assert.strictEqual(passive.sent.length, 0, 'invalid volumes must not show a fix');
    });

    // A corrupt map must not be reported as "same drive" when both paths
    // happen to be on Z: (the old v1 comparison ran before the host check).
    [emptyVolumes, invalidJson].forEach(function (file) {
      const sameDrive = makeState({ stagingPath: 'Z:\\home\\deck\\Other Staging' });
      const result = internals.evaluateStagingFix(makeContext(sameDrive).context, file);
      assert.ok(result && result.target === null, 'same-drive invalid map must stay passive');
      assert.strictEqual(result.reason, 'invalid host.json');
      internals.resetOfferedStagingFixes();
    });

    // Two libraries on different volumes: the matching one is selected.
    assert.deepStrictEqual(
      internals.volumeForPath([homeVolume, sdVolume], SD_GAME_PATH),
      { anchor: SD_ANCHOR.toLowerCase(), modsRoot: SD_MODS_ROOT }
    );
    assert.deepStrictEqual(
      internals.volumeForPath([homeVolume, sdVolume], GAME_PATH),
      { anchor: 'z:\\home\\deck', modsRoot: MODS_ROOT }
    );

    // The longest matching anchor wins.
    const nestedVolumes = [
      { anchor: 'Z:\\home', modsRoot: 'Z:\\home\\Vortex Mods' },
      { anchor: 'Z:\\home\\deck', modsRoot: MODS_ROOT }
    ];
    assert.strictEqual(internals.volumeForPath(nestedVolumes, GAME_PATH).modsRoot, MODS_ROOT);

    // Partial directory-name matches are not anchor matches.
    assert.strictEqual(
      internals.volumeForPath([sdVolume], 'Z:\\run\\media\\deck\\SDCard\\steamapps\\common\\Game'),
      null
    );
    assert.strictEqual(
      internals.pathUnderAnchor(
        internals.normalizedWindowsPath('z:\\run\\media\\deck\\sdcard'),
        internals.normalizedWindowsPath(SD_ANCHOR)
      ), false
    );

    console.log('STAGING_FIX_HARNESS_OK');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

const args = process.argv.slice(2);
if (args[0] === '--host') {
  if (!args[1]) {
    console.error('STAGING_FIX_HARNESS_FAILED: missing host file');
    process.exit(1);
  }
  try {
    checkGeneratedHostFile(args[1]);
  } catch (error) {
    console.error('STAGING_FIX_HARNESS_FAILED: ' + (error && error.stack ? error.stack : error));
    process.exit(1);
  }
} else {
  main().catch(function (error) {
    console.error('STAGING_FIX_HARNESS_FAILED: ' + (error && error.stack ? error.stack : error));
    process.exit(1);
  });
}
