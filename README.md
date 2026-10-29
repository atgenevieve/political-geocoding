# political-geocoding

Open, rebuildable lookups from places to political districts, built directly
from Census Bureau files so they can be regenerated whenever district lines
change.

**Available now:** every ZIP code → U.S. congressional district(s), with the
share of the ZIP's population (and land) in each district, usable straight
from Google Sheets.

| Plan | What it is | Lookup |
|---|---|---|
| `cd120` (default) | Districts on the ballot **Nov 3, 2026**, seated Jan 3, 2027. Includes the 2025–26 mid-decade maps in AL, CA, FL, LA, NC, OH, TN, TX and UT. Missouri stays on its 2022 map. | [`data/congress/cd120/zip_cd.csv`](data/congress/cd120/zip_cd.csv) |
| `cd119` | Districts of the **current** members of Congress (until Jan 3, 2027). | [`data/congress/cd119/zip_cd.csv`](data/congress/cd119/zip_cd.csv) |

`data/congress/zip_cd.csv` is always a copy of the default plan, so links to it
keep working when the default moves to a newer map.

## Google Sheets

### Option 1: custom function (recommended)

1. In your sheet, open **Extensions → Apps Script**.
2. Replace the editor contents with [`sheets/PoliticalGeocoding.gs`](sheets/PoliticalGeocoding.gs) and save.
3. Use it in any cell (the first run asks you to authorize fetching data from GitHub):

```
=ZIP2CD(A2)                 TX-07                     district with most of the ZIP's people
=ZIP2CD(A2, "all")          TX-07, TX-09
=ZIP2CD(A2, "pct")          TX-07 82.1%, TX-09 17.9%
=ZIP2CD(A2, "share")        82.1                      % of the ZIP's people in that district
=ZIP2CD(A2:A5000)           one result per row; far faster than filling a formula down
=ZIP2CD(A2, "cd", "cd119")  current Congress instead of the 2026 lines
=ZIP2CD_PCT(A2, "TX-09")    17.9                      % of the ZIP's people in a given district
=ZIP2CD_TABLE(A2)           spills: district | % of people | % of land
```

ZIPs can be text or numbers (lost leading zeros are restored) and may be ZIP+4.
Data is cached for six hours per spreadsheet.

### Option 2: no script

Load the lookup once into a tab (say `cd`) with
`=IMPORTDATA("https://raw.githubusercontent.com/atgenevieve/political-geocoding/main/data/congress/zip_cd.csv")`,
then look ZIPs up against that tab:

```
=VLOOKUP(VALUE(LEFT(A2,5)), cd!A:G, 2, FALSE)   primary district
=VLOOKUP(VALUE(LEFT(A2,5)), cd!A:G, 6, FALSE)   all districts with % of people
```

(`IMPORTDATA` turns ZIPs into numbers, which is why the lookup key is
`VALUE(...)`.)

## Files

Each plan folder `data/congress/<plan>/` holds:

- `zip_cd.csv`: one row per ZIP. Columns: `zip`, `cd` (district with the
  largest share of the ZIP's population), `pct` (that share), `n_cds`,
  `all_cds`, `all_cds_pct`, `state`.
- `zip_cd_detail.csv`: one row per ZIP × district piece. Columns: `zip`,
  `state`, `cd`, `cd_geoid` (state FIPS + district), `pct_pop`, `pct_land`,
  `pop`, `zip_pop`.
- `by_prefix/<first 3 digits>.json`: the same pieces as small JSON files,
  `{"zip": [[cd, pct_pop, pct_land], ...]}`, used by the Sheets function.

`data/manifest.json` records each plan's source URL, its checksum and
`Last-Modified` date, and the validation results of the last build.

District labels are `ST-NN`; at-large states and the non-voting delegates of
DC and Puerto Rico are `ST-AL`.

## How it is built

Every map is turned into a **block assignment**: which district each 2020
Census tabulation block belongs to. The Census Bureau publishes these
(block equivalency files) for each Congress, including the 120th Congress file
that covers the mid-decade redraws. The pipeline then:

1. Loads 2020 Census population for all ~8.2 million blocks (PL 94-171) and
   the Census 2020 block → ZCTA relationship file (cached; these never change).
2. Downloads each plan's block assignment file listed in
   [`config/plans.toml`](config/plans.toml).
3. Sums population and land area for every ZIP × district piece and computes
   each piece's share of the ZIP.
4. Validates the result: every populated block must be assigned and every
   state must have exactly its apportioned number of districts. The build
   fails otherwise.

Percentages are population-weighted, which is what matters for people (a ZIP
that is 90% empty ranch land in one district but 90% of its residents in
another counts as 90% in the second). Unpopulated ZIPs fall back to land area.

### Keeping it current

The [`build data`](.github/workflows/build-data.yml) GitHub Action reruns the
pipeline every Monday, whenever the pipeline or plan list changes, and on
demand from the Actions tab. If a source file was republished, the new data is
validated and committed automatically.

When a state redraws its lines:

- If the Census Bureau republishes the plan's file (as it did on 2026-10-06
  for Missouri), the weekly run picks it up with no changes needed.
- For a new map that Census has not published yet, add a `[[plan]]` entry in
  `config/plans.toml` pointing at the state's block assignment file and push.
- When a new Congress is seated or a newer map becomes the one people want by
  default, change `[defaults]` in `config/plans.toml`.

### Run it locally

```
pip install -r requirements.txt
python -m geocoding build          # all plans; first run downloads ~1 GB of Census files
python -m geocoding build cd120    # one plan
python -m pytest
```

## Caveats

- **ZIP codes are Census ZIP Code Tabulation Areas (ZCTAs)**, which cover
  nearly all residential ZIPs. ZIPs for PO boxes only, single large
  organizations or military mail have no ZCTA and are not in the lookup yet.
- Population shares use 2020 Census counts, the same counts the districts
  were drawn with.
- Where a plan splits a census block, the Census file assigns the whole block
  to one district, which moves at most a handful of people.

## Roadmap

The pipeline is generic over district type: anything expressible as a block
assignment file can be added as a plan with a new `layer`.

- State legislative districts (upper and lower chambers)
- Local districts (city council, county commission, school board)
- Address → district geocoding (address → census block via the Census
  geocoder, then the same block assignment tables)
- ZIPs without a ZCTA (PO box and unique ZIPs)
