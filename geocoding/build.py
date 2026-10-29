"""Build ZIP (ZCTA) -> district lookups for every configured plan."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pandas as pd

from . import sources
from .fips import HOUSE_SEATS, USPS, cd_label
from .sources import log

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "plans.toml"
DATA = ROOT / "data"

# Districts that are not real districts (Census uses ZZ for unassigned water).
NON_DISTRICTS = {"ZZ"}


def load_config(path: Path = CONFIG) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def zcta_district_overlay(base: pd.DataFrame, assignment: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Overlay ZCTAs on districts using 2020 block population and land area.

    base:       block, zcta, pop, aland  (from sources.block_base)
    assignment: block, district          (a plan's block assignment file)

    Returns one row per (zcta, state, district) piece with population/land
    counts and percentages of the ZCTA, plus QA stats.
    """
    df = base.merge(assignment, on="block", how="left")
    df["state"] = df["block"].str[:2]
    unassigned = df["district"].isna()
    stats = {
        "blocks": int(len(base["block"].unique())),
        "population": round(float(df["pop"].sum())),
        "unassigned_blocks": int(df.loc[unassigned, "block"].nunique()),
        "unassigned_population": round(float(df.loc[unassigned, "pop"].sum())),
        "population_outside_zctas": round(float(df.loc[df["zcta"] == "", "pop"].sum())),
    }

    district_pop = (
        df[~unassigned].groupby(["state", "district"], as_index=False)["pop"].sum()
    )
    stats["districts"] = district_pop

    df = df[~unassigned & (df["zcta"] != "") & ~df["district"].isin(NON_DISTRICTS)]
    pieces = df.groupby(["zcta", "state", "district"], as_index=False)[["pop", "aland"]].sum()
    pieces = pieces[(pieces["pop"] > 0) | (pieces["aland"] > 0)]
    totals = pieces.groupby("zcta")[["pop", "aland"]].transform("sum")
    pieces["zcta_pop"] = totals["pop"]
    pieces["pct_pop"] = (100 * pieces["pop"] / totals["pop"]).where(totals["pop"] > 0)
    pieces["pct_land"] = (100 * pieces["aland"] / totals["aland"]).where(totals["aland"] > 0)
    # Rank by population share; fall back to land share for unpopulated ZCTAs.
    pieces["weight"] = pieces["pct_pop"].fillna(pieces["pct_land"]).fillna(0)
    pieces = pieces.sort_values(["zcta", "weight", "district"], ascending=[True, False, True])
    return pieces.reset_index(drop=True), stats


def validate_congress(stats: dict) -> dict:
    """Check seat counts and population balance; raise on hard failures."""
    d = stats.pop("districts")
    d = d[~d["district"].isin(NON_DISTRICTS) & (d["pop"] > 0)]
    problems, balance = [], {}
    for state, grp in d.groupby("state"):
        expected = max(HOUSE_SEATS.get(state, 0), 1)
        if len(grp) != expected:
            problems.append(f"{USPS.get(state, state)}: {len(grp)} districts, expected {expected}")
        if len(grp) > 1:
            ideal = grp["pop"].sum() / len(grp)
            balance[USPS[state]] = round(100 * (grp["pop"].max() - grp["pop"].min()) / ideal, 3)
    missing = set(HOUSE_SEATS) - set(d["state"])
    problems += [f"{USPS[s]}: no districts" for s in sorted(missing)]
    if stats["unassigned_population"] > 0:
        problems.append(f"{stats['unassigned_population']:,} people in blocks the plan does not assign")
    if problems:
        raise ValueError("plan failed validation:\n  " + "\n  ".join(problems))
    stats["district_count"] = int(len(d))
    # Range of district populations as % of ideal, per multi-district state.
    # Whole-block files can be off by a few people where a plan splits blocks.
    stats["max_population_range_pct"] = max(balance.values())
    stats["population_range_pct_by_state"] = balance
    return stats


def fmt_pct(x) -> str:
    return "" if pd.isna(x) else f"{x:.1f}%"


def write_congress_outputs(plan_id: str, pieces: pd.DataFrame, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = pieces.copy()
    p["cd"] = [cd_label(s, d) for s, d in zip(p["state"], p["district"])]
    p["cd_geoid"] = p["state"] + p["district"]
    p["usps"] = p["state"].map(USPS)

    detail = pd.DataFrame({
        "zip": p["zcta"],
        "state": p["usps"],
        "cd": p["cd"],
        "cd_geoid": p["cd_geoid"],
        "pct_pop": p["pct_pop"].round(2),
        "pct_land": p["pct_land"].round(2),
        "pop": p["pop"].round().astype("int64"),
        "zip_pop": p["zcta_pop"].round().astype("int64"),
    })
    detail.to_csv(out_dir / "zip_cd_detail.csv", index=False)

    # The summary files skip pieces with no residents in ZIPs that have
    # residents (slivers of empty land); zip_cd_detail.csv keeps them.
    p = p[(p["pop"] >= 0.5) | (p["zcta_pop"] < 0.5)]
    p["share"] = [f"{cd} {fmt_pct(w)}" for cd, w in zip(p["cd"], p["weight"])]
    g = p.groupby("zcta", sort=True)
    lookup = pd.DataFrame({
        "zip": g["zcta"].first(),
        "cd": g["cd"].first(),
        "pct": g["weight"].first().round(1),
        "n_cds": g["cd"].size(),
        "all_cds": g["cd"].agg(", ".join),
        "all_cds_pct": g["share"].agg(", ".join),
        "state": g["usps"].first(),
    })
    lookup.to_csv(out_dir / "zip_cd.csv", index=False)

    # One tiny CSV per ZIP (a row per district: label, % of the ZIP), so a
    # spreadsheet can read a single ZIP with IMPORTDATA and nothing else.
    zip_dir = out_dir / "zip"
    if zip_dir.exists():
        shutil.rmtree(zip_dir)
    zip_dir.mkdir()
    for z, grp in p.groupby("zcta", sort=False):
        lines = [f"{cd},{w:.1f}" for cd, w in zip(grp["cd"], grp["weight"])]
        (zip_dir / f"{z}.csv").write_text("\n".join(lines) + "\n")

    return {
        "zips": int(lookup.shape[0]),
        "zips_split": int((lookup["n_cds"] > 1).sum()),
    }


def build_plan(plan: dict, base: pd.DataFrame) -> dict:
    log(f"== {plan['id']}: {plan['name']}")
    src = sources.CACHE_DIR / "plans" / plan["id"] / Path(plan["source_url"]).name
    meta = sources.download(plan["source_url"], src, refresh=True)
    assignment = sources.read_block_assignment(src, plan.get("source_file", ""))
    pieces, stats = zcta_district_overlay(base, assignment)
    if plan["layer"] != "congress":
        raise NotImplementedError(f"layer {plan['layer']!r} has no output writer yet")
    stats = validate_congress(stats)
    out_dir = DATA / plan["layer"] / plan["id"]
    stats.update(write_congress_outputs(plan["id"], pieces, out_dir))
    log(f"   {stats['district_count']} districts, {stats['zips']:,} ZIPs "
        f"({stats['zips_split']:,} split), max pop range {stats['max_population_range_pct']}%")
    return {
        **{k: plan[k] for k in ("id", "layer", "name", "in_effect", "first_election", "notes") if k in plan},
        "source": {
            "url": plan["source_url"],
            "page": plan.get("source_page", ""),
            "last_modified": meta.get("last_modified", ""),
            "sha256": meta.get("sha256", ""),
        },
        "files": {
            "lookup": f"data/{plan['layer']}/{plan['id']}/zip_cd.csv",
            "detail": f"data/{plan['layer']}/{plan['id']}/zip_cd_detail.csv",
            "per_zip": f"data/{plan['layer']}/{plan['id']}/zip/{{zip}}.csv",
        },
        "stats": stats,
    }


def build(plan_ids: list[str] | None = None, refresh_base: bool = False) -> None:
    config = load_config()
    plans = [p for p in config["plan"] if not plan_ids or p["id"] in plan_ids]
    base = sources.block_base(refresh=refresh_base)
    log(f"block base: {len(base):,} rows, pop {base['pop'].sum():,.0f}")

    manifest_path = DATA / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    built = {p["id"]: build_plan(p, base) for p in plans}
    manifest["plans"] = {**manifest.get("plans", {}), **built}
    manifest["defaults"] = config.get("defaults", {})
    manifest["population_source"] = "2020 Census PL 94-171 block population"
    manifest["zip_source"] = sources.ZCTA_BLOCK_URL

    # Stable top-level copies of the default congressional lookup, so a
    # spreadsheet formula keeps working when the default map changes.
    default = manifest["defaults"].get("congress")
    src = DATA / "congress" / str(default)
    if default and (src / "zip_cd.csv").exists():
        shutil.copyfile(src / "zip_cd.csv", DATA / "congress" / "zip_cd.csv")
        if (DATA / "congress" / "zip").exists():
            shutil.rmtree(DATA / "congress" / "zip")
        shutil.copytree(src / "zip", DATA / "congress" / "zip")

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
