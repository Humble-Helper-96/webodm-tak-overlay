#!/usr/bin/env node
/*
 * test_camera_grouping.js — regression tests for mixed-camera grouping
 * (v0.8.5 redesign: operator selects which detected camera group(s) to
 * process; nothing is ever auto-purged).
 *
 * Usage:
 *     node test_camera_grouping.js
 *
 * This extracts the actual grouping/filtering functions straight out of
 * templates/app.html at run time (readExifData, parseTiffHeader,
 * extractFilenamePrefix, finalizeGroups, analyzePhotos, filterToGroup,
 * isJpegFilename) via brace-matching on the function source, so it always
 * tests the real shipped client-side code — not a hand-copied stand-in
 * that could silently drift from it. If a function is renamed or removed,
 * extraction fails loudly rather than testing stale logic.
 *
 * Fixtures are built in-process (buildJpegFixture below) as minimal,
 * spec-correct little-endian TIFF/EXIF blocks wrapped in a JPEG APP1
 * segment — no external binary files or other tooling required.
 *
 * Covers (roadmap requirement #12):
 *   - single RGB group / single thermal group (no picker needed)
 *   - mixed RGB + thermal (two distinct groups, nothing dropped)
 *   - .jpg/.JPG and .jpeg/.JPEG accepted case-insensitively
 *   - ambiguous/unknown grouping (ends up "uncertain", not misclassified)
 *   - more than two groups
 *   - no group is ever auto-purged (filterToGroup never mutates the
 *     shared analysis, and every other group stays fully retrievable)
 *   - non-JPEG sidecar files (.irg, .TIFF) excluded from the photo set
 *   - the exact real-world Autel 640T MAX_/IRX_ file pattern end to end
 *     (a worked example the tests exercise, not the algorithm's definition)
 */

'use strict';

const fs = require('fs');
const path = require('path');

const APP_HTML = path.join(__dirname, 'templates', 'app.html');

// ── Extract real functions from templates/app.html ──────────────────────

function extractFunction(src, name) {
    var patterns = [
        new RegExp('(?:^|\\n)function\\s+' + name + '\\s*\\('),
        new RegExp('(?:^|\\n)async function\\s+' + name + '\\s*\\('),
    ];
    for (var p = 0; p < patterns.length; p++) {
        var m = patterns[p].exec(src);
        if (!m) continue;
        var declStart = m.index + (m[0][0] === '\n' ? 1 : 0);
        var braceStart = src.indexOf('{', declStart);
        var depth = 0;
        var inString = null;  // null | "'" | '"' | '`'
        var inLineComment = false;
        var inBlockComment = false;
        for (var i = braceStart; i < src.length; i++) {
            var c = src[i];
            var next = src[i + 1];
            var prev = src[i - 1];

            if (inLineComment) {
                if (c === '\n') inLineComment = false;
                continue;
            }
            if (inBlockComment) {
                if (c === '*' && next === '/') { inBlockComment = false; i++; }
                continue;
            }
            if (inString) {
                if (c === inString && prev !== '\\') inString = null;
                continue;
            }
            if (c === '/' && next === '/') { inLineComment = true; i++; continue; }
            if (c === '/' && next === '*') { inBlockComment = true; i++; continue; }
            if (c === "'" || c === '"' || c === '`') { inString = c; continue; }
            if (c === '{') depth++;
            else if (c === '}') {
                depth--;
                if (depth === 0) return src.slice(declStart, i + 1);
            }
        }
        throw new Error('unbalanced braces extracting function: ' + name);
    }
    throw new Error('function not found in templates/app.html: ' + name);
}

function loadGroupingFunctions() {
    var html = fs.readFileSync(APP_HTML, 'utf8');
    var scriptMatch = /<script>([\s\S]*?)<\/script>\s*<\/head>/.exec(html);
    // The grouping functions all live in the main (second, non-head) inline
    // script block; search the whole file rather than relying on a single
    // <script> tag position, since that's robust to the file gaining more
    // <script> blocks later.
    var names = [
        'readExifData', 'parseTiffHeader', 'readAscii', 'readRationalDegrees',
        'readRational', 'extractFilenamePrefix', 'finalizeGroups',
        'groupNameSuffix', 'analyzePhotos', 'filterToGroup', 'isJpegFilename',
    ];
    var src = names.map(function(name) { return extractFunction(html, name); }).join('\n');
    var sandbox = {};
    // photoAnalysis is a module-level var in app.html that filterToGroup
    // reads; analyzePhotos reassigns it on each call via the global
    // (non-"var"-scoped-inside-function) assignment it already does.
    sandbox.photoAnalysis = { points: [], groups: [], noGps: [], duplicates: [], mixedCameras: false };
    var vm = require('vm');
    vm.createContext(sandbox);
    vm.runInContext(src + '\n;this.__exports = { readExifData, extractFilenamePrefix, finalizeGroups, groupNameSuffix, analyzePhotos, filterToGroup, isJpegFilename, getPhotoAnalysis: function(){ return photoAnalysis; } };', sandbox);
    return sandbox.__exports;
}

// ── Minimal in-process JPEG+EXIF fixture builder ─────────────────────────
// Mirrors a real little-endian TIFF/EXIF structure: IFD0 (optional
// Make/Model + mandatory ExifIFD pointer) -> Exif IFD (DateTimeOriginal,
// PixelXDimension, PixelYDimension), wrapped in a JPEG SOI+APP1(+EOI).

function u16le(n) { var b = Buffer.alloc(2); b.writeUInt16LE(n, 0); return b; }
function u32le(n) { var b = Buffer.alloc(4); b.writeUInt32LE(n, 0); return b; }
function entry(tag, type, count, valueBytes) {
    if (valueBytes.length !== 4) throw new Error('value field must be 4 bytes');
    return Buffer.concat([u16le(tag), u16le(type), u32le(count), valueBytes]);
}

function buildJpegFixture(width, height, make, model) {
    var haveCamera = !!(make || model);
    var numIfd0 = haveCamera ? 3 : 1; // Make, Model, ExifIFD ptr  OR  just ExifIFD ptr
    var IFD0_START = 8;
    var IFD0_SIZE = 2 + numIfd0 * 12 + 4;
    var DATA0_START = IFD0_START + IFD0_SIZE;

    var makeBytes = haveCamera ? Buffer.from(make + '\0', 'ascii') : Buffer.alloc(0);
    var modelBytes = haveCamera ? Buffer.from(model + '\0', 'ascii') : Buffer.alloc(0);
    var MAKE_OFFSET = DATA0_START;
    var MODEL_OFFSET = MAKE_OFFSET + makeBytes.length;
    var EXIF_IFD_START = MODEL_OFFSET + modelBytes.length;

    var EXIF_NUM_ENTRIES = 3;
    var EXIF_IFD_SIZE = 2 + EXIF_NUM_ENTRIES * 12 + 4;
    var EXIF_DATA_START = EXIF_IFD_START + EXIF_IFD_SIZE;

    var datetimeBytes = Buffer.from('2026:07:17 09:28:00\0', 'ascii'); // 20 bytes
    var DATETIME_OFFSET = EXIF_DATA_START;
    var TOTAL_LEN = DATETIME_OFFSET + datetimeBytes.length;

    var ifd0Parts = [u16le(numIfd0)];
    if (haveCamera) {
        ifd0Parts.push(entry(0x010F, 2, makeBytes.length, u32le(MAKE_OFFSET)));
        ifd0Parts.push(entry(0x0110, 2, modelBytes.length, u32le(MODEL_OFFSET)));
    }
    ifd0Parts.push(entry(0x8769, 4, 1, u32le(EXIF_IFD_START)));
    ifd0Parts.push(u32le(0)); // next IFD offset
    var ifd0 = Buffer.concat(ifd0Parts);
    if (ifd0.length !== IFD0_SIZE) throw new Error('IFD0 size mismatch');

    var exifIfd = Buffer.concat([
        u16le(EXIF_NUM_ENTRIES),
        entry(0x9003, 2, 20, u32le(DATETIME_OFFSET)),
        entry(0xA002, 3, 1, Buffer.concat([u16le(width), Buffer.from([0, 0])])),
        entry(0xA003, 3, 1, Buffer.concat([u16le(height), Buffer.from([0, 0])])),
        u32le(0),
    ]);

    var tiff = Buffer.concat([
        Buffer.from('II', 'ascii'), u16le(42), u32le(IFD0_START),
        ifd0,
        makeBytes, modelBytes,
        exifIfd,
        datetimeBytes,
    ]);
    if (tiff.length !== TOTAL_LEN) throw new Error('TIFF total length mismatch');

    var payload = Buffer.concat([Buffer.from('Exif\0\0', 'ascii'), tiff]);
    var app1Length = payload.length + 2;
    var jpeg = Buffer.concat([
        Buffer.from([0xFF, 0xD8]),
        Buffer.from([0xFF, 0xE1]), u16beBuf(app1Length),
        payload,
        Buffer.from([0xFF, 0xD9]),
    ]);
    return jpeg;
}

function u16beBuf(n) { var b = Buffer.alloc(2); b.writeUInt16BE(n, 0); return b; }

function FakeFile(buf, name) {
    this.name = name;
    this._buf = buf;
}
FakeFile.prototype.arrayBuffer = async function() {
    return this._buf.buffer.slice(this._buf.byteOffset, this._buf.byteOffset + this._buf.byteLength);
};

// ── Test runner ───────────────────────────────────────────────────────

var failures = [];
function check(label, cond, detail) {
    if (cond) {
        console.log('PASS — ' + label);
    } else {
        console.log('FAIL — ' + label + (detail !== undefined ? ' (' + JSON.stringify(detail) + ')' : ''));
        failures.push(label);
    }
}

async function main() {
    var fns = loadGroupingFunctions();
    var analyzePhotos = fns.analyzePhotos;
    var filterToGroup = fns.filterToGroup;
    var isJpegFilename = fns.isJpegFilename;
    var extractFilenamePrefix = fns.extractFilenamePrefix;
    var groupNameSuffix = fns.groupNameSuffix;

    var rgbBuf = buildJpegFixture(4000, 3000, 'AutelRobotics', 'XT705RC');
    var thermalBuf = buildJpegFixture(640, 512, 'AutelRobotics', 'ThermalXT2');
    var noExifBuf = buildJpegFixture(640, 512, '', '');
    var thirdBuf = buildJpegFixture(1280, 720, 'AnotherCam', 'ModelZ');
    var genericABuf = buildJpegFixture(2048, 1536, 'GenericCam', 'ModelA');
    var genericBBuf = buildJpegFixture(1920, 1080, 'GenericCam', 'ModelB');

    // ── Single RGB group ──────────────────────────────────────────────
    var t1 = await analyzePhotos([
        new FakeFile(rgbBuf, 'MAX_0001.JPG'),
        new FakeFile(rgbBuf, 'MAX_0002.JPG'),
    ]);
    check('single RGB group: mixedCameras=false', t1.mixedCameras === false, t1);
    check('single RGB group: 1 group, count 2', t1.groups.length === 1 && t1.groups[0].count === 2, t1.groups);
    check('single RGB group: not uncertain (has camera EXIF)', t1.groups[0].uncertain === false);

    // ── Single thermal group ──────────────────────────────────────────
    var t2 = await analyzePhotos([
        new FakeFile(thermalBuf, 'IRX_0001.jpg'),
        new FakeFile(thermalBuf, 'IRX_0002.jpg'),
    ]);
    check('single thermal group: mixedCameras=false', t2.mixedCameras === false, t2);
    check('single thermal group: dims 640x512', t2.groups[0].width === 640 && t2.groups[0].height === 512);

    // ── Mixed RGB + thermal ───────────────────────────────────────────
    var rgbFiles = [new FakeFile(rgbBuf, 'MAX_0001.JPG'), new FakeFile(rgbBuf, 'MAX_0002.JPG')];
    var thermalFiles = [new FakeFile(thermalBuf, 'IRX_0001.jpg'), new FakeFile(thermalBuf, 'IRX_0002.jpg')];
    var t3files = rgbFiles.concat(thermalFiles);
    var t3 = await analyzePhotos(t3files);
    check('mixed RGB+thermal: mixedCameras=true', t3.mixedCameras === true);
    check('mixed RGB+thermal: exactly 2 groups', t3.groups.length === 2, t3.groups);
    check('mixed RGB+thermal: both groups have 2 photos', t3.groups.every(function(g) { return g.count === 2; }));
    check('mixed RGB+thermal: no photo lost', t3.points.length === 4);

    // ── .jpg / .JPG case-insensitivity ───────────────────────────────
    check('.jpg accepted', isJpegFilename('x.jpg') === true);
    check('.JPG accepted', isJpegFilename('x.JPG') === true);
    var t4 = await analyzePhotos([
        new FakeFile(genericABuf, 'a.jpg'),
        new FakeFile(genericABuf, 'A2.JPG'),
    ]);
    check('.jpg/.JPG grouped together', t4.groups.length === 1 && t4.groups[0].count === 2, t4.groups);

    // ── .jpeg / .JPEG case-insensitivity ──────────────────────────────
    check('.jpeg accepted', isJpegFilename('x.jpeg') === true);
    check('.JPEG accepted', isJpegFilename('x.JPEG') === true);
    var t5 = await analyzePhotos([
        new FakeFile(genericBBuf, 'b.jpeg'),
        new FakeFile(genericBBuf, 'B2.JPEG'),
    ]);
    check('.jpeg/.JPEG grouped together', t5.groups.length === 1 && t5.groups[0].count === 2, t5.groups);

    // ── Ambiguous/unknown grouping ─────────────────────────────────────
    var t6 = await analyzePhotos([
        new FakeFile(noExifBuf, 'AAA_0001.JPG'),
        new FakeFile(noExifBuf, 'BBB_0001.JPG'),
    ]);
    check('ambiguous grouping: one groupKey (identical dims, no camera EXIF)', t6.groups.length === 1, t6.groups);
    check('ambiguous grouping: flagged uncertain', t6.groups[0].uncertain === true, t6.groups[0]);
    check('ambiguous grouping: both prefixes recorded as evidence',
        t6.groups[0].prefixes.slice().sort().join(',') === 'AAA,BBB', t6.groups[0]);

    // ── More than two groups ───────────────────────────────────────────
    var t7 = await analyzePhotos(t3files.concat([new FakeFile(thirdBuf, 'ZZZ_0001.JPG')]));
    check('more than two groups: 3 groups', t7.groups.length === 3, t7.groups);
    check('more than two groups: all 5 photos present', t7.points.length === 5);

    // ── No group is ever auto-purged ────────────────────────────────────
    // filterToGroup reads the sandboxed module-level photoAnalysis, which
    // analyzePhotos(t3files) above already populated as a side effect (the
    // last analyzePhotos call in this sandbox was t7's — recompute t3
    // fresh so the sandbox's photoAnalysis matches exactly what we split).
    var t3b = await analyzePhotos(t3files);
    var rgbKey = t3b.groups.find(function(g) { return g.camera.indexOf('XT705RC') !== -1; }).key;
    var thermalKey = t3b.groups.find(function(g) { return g.camera.indexOf('ThermalXT2') !== -1; }).key;
    var rgbSplit = filterToGroup(t3files, rgbKey);
    check('filterToGroup: RGB split has exactly 2 files', rgbSplit.files.length === 2, rgbSplit.files.map(f => f.name));
    check('filterToGroup: does not mutate shared analysis (still 4 points)', t3b.points.length === 4);
    check('filterToGroup: does not mutate shared analysis (still 2 groups)', t3b.groups.length === 2);
    var thermalSplit = filterToGroup(t3files, thermalKey);
    check('filterToGroup: thermal group still fully retrievable after RGB split',
        thermalSplit.files.length === 2, thermalSplit.files.map(f => f.name));

    // ── Non-JPEG sidecar files excluded ─────────────────────────────────
    var mixedNames = ['IRX_0003.irg', 'IRX_0003.jpg', 'IRX_0003.TIFF', 'MAX_0003.JPG'];
    var jpegOnly = mixedNames.filter(isJpegFilename);
    check('.irg excluded', jpegOnly.indexOf('IRX_0003.irg') === -1);
    check('.TIFF excluded', jpegOnly.indexOf('IRX_0003.TIFF') === -1);
    check('exactly the 2 JPEGs kept', jpegOnly.length === 2 &&
        jpegOnly.indexOf('IRX_0003.jpg') !== -1 && jpegOnly.indexOf('MAX_0003.JPG') !== -1, jpegOnly);

    // ── Platform-agnostic prefix extraction ─────────────────────────────
    check('extractFilenamePrefix is a shape match, not a brand lookup',
        extractFilenamePrefix('MAX_0003.JPG') === 'MAX' &&
        extractFilenamePrefix('IRX_0003.jpg') === 'IRX' &&
        extractFilenamePrefix('DJI_0001.JPG') === 'DJI' &&
        extractFilenamePrefix('FLIR9999.jpg') === 'FLIR' &&
        extractFilenamePrefix('SomeOtherBrandCam_42.jpg') === 'SOMEOTHERBRANDCAM' &&
        extractFilenamePrefix('100_0001.JPG') === null);

    // ── Real-world case: the reported Autel 640T folder ────────────────
    // MAX_000N.JPG (RGB) + IRX_000N.jpg/.TIFF/.irg (thermal JPEG + two
    // non-JPEG sidecars) per capture. Worked example, not the algorithm's
    // definition (requirement #11) — exercises the same functions as
    // every test above against this specific, previously-broken case.
    var realWorldNames = [
        'IRX_0003.irg', 'IRX_0003.jpg', 'IRX_0003.TIFF',
        'IRX_0004.irg', 'IRX_0004.jpg', 'IRX_0004.TIFF',
        'MAX_0003.JPG', 'MAX_0004.JPG',
    ];
    var rwJpegNames = realWorldNames.filter(isJpegFilename);
    check('real-world Autel folder: only the 4 JPEGs pass the file-type filter',
        rwJpegNames.length === 4, rwJpegNames);

    var rwFiles = [
        new FakeFile(rgbBuf, 'MAX_0003.JPG'),
        new FakeFile(rgbBuf, 'MAX_0004.JPG'),
        new FakeFile(thermalBuf, 'IRX_0003.jpg'),
        new FakeFile(thermalBuf, 'IRX_0004.jpg'),
    ];
    var rw = await analyzePhotos(rwFiles);
    check('real-world Autel folder: RGB and thermal in separate groups', rw.groups.length === 2, rw.groups);
    check('real-world Autel folder: RGB group has both RGB photos',
        rw.groups.some(function(g) { return g.camera.indexOf('XT705RC') !== -1 && g.count === 2; }));
    check('real-world Autel folder: thermal group has both thermal photos',
        rw.groups.some(function(g) { return g.camera.indexOf('ThermalXT2') !== -1 && g.count === 2; }));

    var rgbRwKey = rw.groups.find(function(g) { return g.camera.indexOf('XT705RC') !== -1; }).key;
    var rgbRwSplit = filterToGroup(rwFiles, rgbRwKey);
    check('real-world Autel folder: RGB group fully recoverable (the originally-reported bug)',
        rgbRwSplit.files.length === 2 &&
        rgbRwSplit.files.map(function(f) { return f.name; }).sort().join(',') === 'MAX_0003.JPG,MAX_0004.JPG',
        rgbRwSplit.files.map(function(f) { return f.name; }));

    // ── Job name collision when two groups share one EXIF camera string ─
    // Reported live: a dual RGB+thermal gimbal whose two sensors both
    // report the same EXIF Make/Model ("Camera XL726") produced two jobs
    // both named "Incident [Camera XL726]" — indistinguishable in the job
    // list even though grouping itself was correct (dimensions still kept
    // them as separate groups/jobs). groupNameSuffix must disambiguate.
    var sharedCamBuf1 = buildJpegFixture(4000, 3000, 'Autel', 'Camera XL726');
    var sharedCamBuf2 = buildJpegFixture(640, 512, 'Autel', 'Camera XL726');
    var sharedCamResult = await analyzePhotos([
        new FakeFile(sharedCamBuf1, 'MAX_0001.JPG'),
        new FakeFile(sharedCamBuf2, 'IRX_0001.jpg'),
    ]);
    check('shared-camera-string groups: still detected as 2 distinct groups',
        sharedCamResult.groups.length === 2, sharedCamResult.groups);
    var suffixes = sharedCamResult.groups.map(groupNameSuffix);
    check('shared-camera-string groups: job name suffixes are NOT identical',
        suffixes[0] !== suffixes[1], suffixes);
    check('shared-camera-string groups: suffix is just the prefix, not dimensions/camera',
        suffixes.indexOf('MAX') !== -1 && suffixes.indexOf('IRX') !== -1, suffixes);

    // groupNameSuffix fallbacks when there's no single clean prefix.
    check('groupNameSuffix: falls back to camera+dims when prefixes are ambiguous',
        groupNameSuffix({ width: 640, height: 512, camera: 'SomeCam', prefixes: ['AAA', 'BBB'] }) === 'SomeCam 640×512');
    check('groupNameSuffix: falls back to dims alone with no camera and no clean prefix',
        groupNameSuffix({ width: 640, height: 512, camera: '', prefixes: [] }) === '640×512');

    console.log('');
    if (failures.length === 0) {
        console.log('All camera-grouping regression tests passed.');
        process.exit(0);
    } else {
        console.log(failures.length + ' test(s) failed:');
        failures.forEach(function(f) { console.log('  - ' + f); });
        process.exit(1);
    }
}

main().catch(function(e) {
    console.error('TEST HARNESS ERROR:', e);
    process.exit(1);
});
