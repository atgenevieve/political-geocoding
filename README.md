# political-geocoding

Open, rebuildable lookups from places to political districts, built directly
from Census Bureau files so they can be regenerated whenever district lines
change.

**Available now:** every ZIP code → U.S. congressional district(s), with the
share of the ZIP's population (and land) in each district, usable from a plain
Google Sheets formula.

| Plan | What it is | Lookup |
|---|---|---|
| `cd120` (default) | Districts on the ballot **Nov 3, 2026**, seated Jan 3, 2027. Includes the 2025–26 mid-decade maps in AL, CA, FL, LA, NC, OH, TN, TX and UT. Missouri stays on its 2022 map. | [`data/congress/cd120/zip_cd.csv`](data/congress/cd120/zip_cd.csv) |
| `cd119` | Districts of the **current** members of Congress (until Jan 3, 2027). | [`data/congress/cd119/zip_cd.csv`](data/congress/cd119/zip_cd.csv) |

`data/congress/zip_cd.csv` is always a copy of the default plan, so links to it
keep working when the default moves to a newer map.

## Google Sheets

Put ZIP codes in column A starting at A2, paste one of these formulas into B2,
and drag it down. Every cell loads the same file, so Sheets fetches it once for
the whole sheet and thousands of rows fill in quickly.

These use the districts on the ballot Nov 3, 2026, and switch automatically
when newer maps take effect. The first time, Sheets may ask you to
**Allow access**; click it.

**Just the district** (`TX-07`; for a ZIP split between districts, the one with most of its people)

```
=IFERROR(VLOOKUP(VALUE(LEFT(A2,5)),IMPORTDATA("https://raw.githubusercontent.com/atgenevieve/political-geocoding/main/data/congress/zip_slim.csv"),2,FALSE),"")
```

**Every district, with the share of the ZIP's people in each** (`TX-07 (82.1%), TX-09 (17.9%)`)

```
=IFERROR(VLOOKUP(VALUE(LEFT(A2,5)),IMPORTDATA("https://raw.githubusercontent.com/atgenevieve/political-geocoding/main/data/congress/zip_slim.csv"),4,FALSE),"")
```

**Every district, without percentages** (`TX-07, TX-09`)

```
=IFERROR(VLOOKUP(VALUE(LEFT(A2,5)),IMPORTDATA("https://raw.githubusercontent.com/atgenevieve/political-geocoding/main/data/congress/zip_slim.csv"),3,FALSE),"")
```

**Every district with shares, using current members' districts** (the lines in effect until Jan 3, 2027)

```
=IFERROR(VLOOKUP(VALUE(LEFT(A2,5)),IMPORTDATA("https://raw.githubusercontent.com/atgenevieve/political-geocoding/main/data/congress/cd119/zip_slim.csv"),4,FALSE),"")
```

## Files

Each plan folder `data/congress/<plan>/` holds:

- `zip_cd.csv`: one row per ZIP. Columns: `zip`, `cd` (district with the
  largest share of the ZIP's population), `pct` (that share), `n_cds`,
  `all_cds`, `all_cds_pct`, `state`.
- `zip_cd_detail.csv`: one row per ZIP × district piece. Columns: `zip`,
  `state`, `cd`, `cd_geoid` (state FIPS + district), `pct_pop`, `pct_land`,
  `pop`, `zip_pop`.
- `zip_slim.csv`: the lookup without a header or extra columns (`zip`, `cd`,
  `all_cds`, `all_cds_pct`), which the spreadsheet formulas import.
- `zip/<zip>.csv`: one tiny file per ZIP for spreadsheet formulas, a row per
  district with `cd`, `% of the ZIP's people`, and a `cd (pct%)` label.

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
- Some states (CA, MD, NJ, NY, VA, WA and others) count incarcerated people
  at their home address when drawing districts, so district totals computed
  from unadjusted Census counts differ by a few percent. This does not change
  which district any block is in.
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
