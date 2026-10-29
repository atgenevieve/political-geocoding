/**
 * Political geocoding for Google Sheets.
 *
 * Install: Extensions > Apps Script, paste this file, save. Then in any cell:
 *
 *   =ZIP2CD(A2)                 -> "TX-07"   district holding most of the ZIP's people
 *   =ZIP2CD(A2, "all")          -> "TX-07, TX-09"
 *   =ZIP2CD(A2, "pct")          -> "TX-07 82.1%, TX-09 17.9%"
 *   =ZIP2CD(A2, "share")        -> 82.1     % of the ZIP's people in that district
 *   =ZIP2CD(A2:A500)            -> one result per row (much faster than filling down)
 *   =ZIP2CD(A2, "cd", "cd119")  -> use the 119th Congress lines instead of the default
 *   =ZIP2CD_PCT(A2, "TX-09")    -> 17.9     % of the ZIP's people in a given district
 *   =ZIP2CD_TABLE(A2)           -> spills rows of: district, % of people, % of land
 *
 * Plans: "cd120" (default: lines on the ballot Nov 3, 2026, seated Jan 3, 2027)
 *        "cd119" (current members of Congress until Jan 3, 2027)
 *
 * Data: https://github.com/atgenevieve/political-geocoding (rebuilt from
 * Census Bureau block assignment files; ZIPs are Census ZCTAs).
 */

var PG_BASE = 'https://raw.githubusercontent.com/atgenevieve/political-geocoding/main/data';
var PG_DEFAULT_PLAN = 'cd120';
var PG_CACHE_SECONDS = 21600; // 6 hours, the Apps Script maximum

/**
 * Congressional district(s) for a ZIP code or a range of ZIP codes.
 *
 * @param {string|number|Array} zip  ZIP code(s). ZIP+4 and numbers missing leading zeros are fine.
 * @param {string} mode  "cd" (default), "all", "pct" or "share".
 * @param {string} plan  "cd120" (default) or "cd119".
 * @return District label(s), e.g. "NY-12".
 * @customfunction
 */
function ZIP2CD(zip, mode, plan) {
  mode = String(mode || 'cd').toLowerCase();
  var format = {
    cd: function (r) { return r[0][0]; },
    all: function (r) { return r.map(function (x) { return x[0]; }).join(', '); },
    pct: function (r) { return r.map(function (x) { return x[0] + ' ' + pgPct_(x) + '%'; }).join(', '); },
    share: function (r) { return pgWeight_(r[0]); }
  }[mode];
  if (!format) throw new Error('mode must be "cd", "all", "pct" or "share"');
  return pgMap_(zip, plan, function (rows) { return rows ? format(rows) : ''; });
}

/**
 * Percent of a ZIP's population living in a given congressional district.
 *
 * @param {string|number|Array} zip  ZIP code(s).
 * @param {string} cd  District label, e.g. "TX-09" or "WY-AL".
 * @param {string} plan  "cd120" (default) or "cd119".
 * @return Percent from 0 to 100.
 * @customfunction
 */
function ZIP2CD_PCT(zip, cd, plan) {
  var want = String(cd || '').toUpperCase().trim();
  return pgMap_(zip, plan, function (rows) {
    if (!rows) return '';
    for (var i = 0; i < rows.length; i++) if (rows[i][0] === want) return pgWeight_(rows[i]);
    return 0;
  });
}

/**
 * All districts overlapping one ZIP: district, % of population, % of land area.
 *
 * @param {string|number} zip  A single ZIP code.
 * @param {string} plan  "cd120" (default) or "cd119".
 * @return Rows of [district, % population, % land].
 * @customfunction
 */
function ZIP2CD_TABLE(zip, plan) {
  var z = pgNormalizeZip_(zip);
  var rows = z && pgLookup_([z], plan)[z];
  if (!rows) return [['', '', '']];
  return rows.map(function (r) { return [r[0], r[1] === null ? '' : r[1], r[2] === null ? '' : r[2]]; });
}

/** Clears cached data so the next calculation re-downloads it. Run from the editor. */
function PG_CLEAR_CACHE() {
  var cache = CacheService.getScriptCache();
  ['cd119', 'cd120'].forEach(function (plan) {
    var keys = [];
    for (var i = 0; i < 1000; i++) keys.push(pgCacheKey_(plan, ('00' + i).slice(-3)));
    cache.removeAll(keys);
  });
}

// --- internals -------------------------------------------------------------

function pgMap_(input, plan, fn) {
  var grid = Array.isArray(input) ? input : [[input]];
  var zips = [];
  var norm = grid.map(function (row) {
    return row.map(function (v) { var z = pgNormalizeZip_(v); if (z) zips.push(z); return z; });
  });
  var data = pgLookup_(zips, plan);
  var out = norm.map(function (row) {
    return row.map(function (z) { return z ? fn(data[z]) : ''; });
  });
  return Array.isArray(input) ? out : out[0][0];
}

function pgNormalizeZip_(v) {
  if (v === '' || v === null || v === undefined) return '';
  var s = String(v).trim();
  var m = s.match(/^(\d{1,5})(?:-\d{4})?$/) || s.match(/^(\d{5})\d{4}$/);
  if (!m) return '';
  return ('00000' + m[1]).slice(-5);
}

// Population share, falling back to land share for unpopulated ZIPs.
function pgWeight_(row) { return row[1] !== null ? row[1] : (row[2] !== null ? row[2] : 0); }
function pgPct_(row) { return Math.round(pgWeight_(row) * 10) / 10; }

function pgCacheKey_(plan, prefix) { return 'pg:' + plan + ':' + prefix; }

/** Returns {zip: [[cd, pctPop, pctLand], ...]} for the requested ZIPs. */
function pgLookup_(zips, plan) {
  plan = String(plan || PG_DEFAULT_PLAN).toLowerCase();
  if (!/^[a-z0-9_]+$/.test(plan)) throw new Error('unknown plan: ' + plan);
  var prefixes = {};
  zips.forEach(function (z) { prefixes[z.slice(0, 3)] = true; });
  var keys = Object.keys(prefixes);
  if (!keys.length) return {};

  var cache = CacheService.getScriptCache();
  var cached = cache.getAll(keys.map(function (p) { return pgCacheKey_(plan, p); }));
  var shards = {};
  var missing = [];
  keys.forEach(function (p) {
    var hit = cached[pgCacheKey_(plan, p)];
    if (hit) shards[p] = JSON.parse(hit); else missing.push(p);
  });

  if (missing.length) {
    var responses = UrlFetchApp.fetchAll(missing.map(function (p) {
      return { url: PG_BASE + '/congress/' + plan + '/by_prefix/' + p + '.json', muteHttpExceptions: true };
    }));
    var toCache = {};
    responses.forEach(function (resp, i) {
      var p = missing[i];
      var code = resp.getResponseCode();
      var text = code === 200 ? resp.getContentText() : (code === 404 ? '{}' : null);
      if (text === null) throw new Error('political-geocoding: HTTP ' + code + ' fetching ' + plan + '/' + p);
      shards[p] = JSON.parse(text);
      toCache[pgCacheKey_(plan, p)] = text;
    });
    cache.putAll(toCache, PG_CACHE_SECONDS);
  }

  var result = {};
  zips.forEach(function (z) {
    var rows = shards[z.slice(0, 3)][z];
    if (rows) result[z] = rows;
  });
  return result;
}
