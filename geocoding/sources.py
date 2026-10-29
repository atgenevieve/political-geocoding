"""Downloading and reading Census source files.

Everything downloaded lands in CACHE_DIR so reruns are cheap. The 2020 block
population and block -> ZCTA tables never change; district plans are
re-downloaded on every build so a republished file is picked up.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from .fips import STATES, pl_folder_name

CACHE_DIR = Path(os.environ.get("PG_CACHE_DIR", ".cache"))

ZCTA_BLOCK_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    "tab20_zcta520_tabblock20_natl.txt"
)
PL_URL = (
    "https://www2.census.gov/programs-surveys/decennial/2020/data/"
    "01-Redistricting_File--PL_94-171/{folder}/{st}2020.pl.zip"
)
USER_AGENT = "political-geocoding/0.1 (+https://github.com/atgenevieve/political-geocoding)"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def download(url: str, dest: Path, refresh: bool = False, attempts: int = 5) -> dict:
    """Download url to dest (skipped if cached). Returns HTTP metadata."""
    meta_path = dest.with_name(dest.name + ".meta")
    if dest.exists() and not refresh:
        return _read_meta(meta_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        try:
            log(f"downloading {url}")
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
                meta = {"url": url, "last_modified": resp.headers.get("Last-Modified", "")}
            break
        except Exception as exc:  # network hiccups are common on census.gov
            if attempt == attempts:
                raise
            wait = 2**attempt
            log(f"  failed ({exc}); retrying in {wait}s")
            time.sleep(wait)
    tmp.replace(dest)
    meta["sha256"] = sha256(dest)
    meta_path.write_text("\n".join(f"{k}\t{v}" for k, v in meta.items()))
    return meta


def _read_meta(path: Path) -> dict:
    if not path.exists():
        return {}
    return dict(line.split("\t", 1) for line in path.read_text().splitlines() if "\t" in line)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# --- 2020 block population (PL 94-171) -------------------------------------

def read_pl_block_population(zip_path: Path, st: str) -> pd.DataFrame:
    """Total population (P0010001) for every 2020 tabulation block in a state."""
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(f"{st}geo2020.pl") as f:
            geo = pd.read_csv(
                io.TextIOWrapper(f, encoding="latin-1"),
                sep="|", header=None, usecols=[2, 7, 8], names=["sumlev", "logrecno", "geoid"],
                dtype=str, quoting=csv.QUOTE_NONE,
            )
        with zf.open(f"{st}000012020.pl") as f:
            seg = pd.read_csv(
                io.TextIOWrapper(f, encoding="latin-1"),
                sep="|", header=None, usecols=[4, 5], names=["logrecno", "pop"],
                dtype={"logrecno": str, "pop": "int64"}, quoting=csv.QUOTE_NONE,
            )
    geo = geo[geo["sumlev"] == "750"]
    geo["block"] = geo["geoid"].str.split("US").str[-1]
    df = geo.merge(seg, on="logrecno", how="left", validate="one_to_one")
    if df["pop"].isna().any():
        raise ValueError(f"{st}: blocks without population rows")
    bad = df["block"].str.len() != 15
    if bad.any():
        raise ValueError(f"{st}: unexpected block GEOIDs, e.g. {df.loc[bad, 'block'].iloc[0]}")
    return df[["block", "pop"]]


def block_population() -> pd.DataFrame:
    frames = []
    for fips in STATES:
        st = STATES[fips][0].lower()
        path = CACHE_DIR / "pl" / f"{st}2020.pl.zip"
        download(PL_URL.format(folder=pl_folder_name(fips), st=st), path)
        df = read_pl_block_population(path, st)
        log(f"  {st.upper()}: {len(df):,} blocks, pop {df['pop'].sum():,}")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


# --- 2020 block -> ZCTA ----------------------------------------------------

def read_zcta_block(path: Path) -> pd.DataFrame:
    """One row per (block, ZCTA) piece with the share of the block's land in it."""
    df = pd.read_csv(
        path, sep="|", dtype=str, encoding="latin-1",
        usecols=["GEOID_ZCTA5_20", "GEOID_TABBLOCK_20", "AREALAND_TABBLOCK_20", "AREALAND_PART"],
    )
    df = df.rename(columns={
        "GEOID_ZCTA5_20": "zcta", "GEOID_TABBLOCK_20": "block",
        "AREALAND_TABBLOCK_20": "aland_block", "AREALAND_PART": "aland",
    })
    df = df[df["zcta"].notna() & (df["zcta"].str.strip() != "")]
    df["aland"] = pd.to_numeric(df["aland"]).fillna(0)
    df["aland_block"] = pd.to_numeric(df["aland_block"]).fillna(0)
    # ZCTAs are built from whole blocks, so the share is 1 almost everywhere.
    # Split land proportionally if a block is ever listed in more than one.
    n = df.groupby("block")["block"].transform("size")
    share = (df["aland"] / df["aland_block"]).where(df["aland_block"] > 0, 1.0 / n)
    df["share"] = share.where(n > 1, 1.0)
    return df[["block", "zcta", "share", "aland"]]


def block_base(refresh: bool = False) -> pd.DataFrame:
    """Every populated-or-not 2020 block with its ZCTA piece(s) and population.

    Columns: block, zcta ('' if none), pop (float, share of block pop), aland.
    Cached as a gzip CSV because building it downloads ~1 GB of PL files.
    """
    out = CACHE_DIR / "derived" / "block_base.csv.gz"
    if out.exists() and not refresh:
        return pd.read_csv(out, dtype={"block": str, "zcta": str}, keep_default_na=False)
    rel_path = CACHE_DIR / "rel" / "tab20_zcta520_tabblock20_natl.txt"
    download(ZCTA_BLOCK_URL, rel_path)
    rel = read_zcta_block(rel_path)
    pop = block_population()
    base = pop.merge(rel, on="block", how="left")
    base["zcta"] = base["zcta"].fillna("")
    base["share"] = base["share"].fillna(1.0)
    base["aland"] = base["aland"].fillna(0)
    base["pop"] = base["pop"] * base["share"]
    base = base[["block", "zcta", "pop", "aland"]]
    out.parent.mkdir(parents=True, exist_ok=True)
    base.to_csv(out, index=False, compression="gzip")
    return base


# --- District plans (block assignment / equivalency files) ----------------

_BLOCK_RE = re.compile(r"^\d{15}$")


def read_block_assignment(path: Path, member_pattern: str = "") -> pd.DataFrame:
    """Read a block assignment file (zip or text) into columns block, district."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".csv"))]
            if member_pattern:
                names = [n for n in names if re.search(member_pattern, n)] or names
            name = max(names, key=lambda n: zf.getinfo(n).file_size)
            log(f"  using {name} from {path.name}")
            text = zf.read(name).decode("latin-1")
    else:
        text = path.read_text(encoding="latin-1")
    first = text.split("\n", 1)[0]
    sep = max([",", "|", "\t"], key=first.count)
    df = pd.read_csv(io.StringIO(text), sep=sep, header=None, dtype=str, usecols=[0, 1],
                     names=["block", "district"], keep_default_na=False)
    df["block"] = df["block"].str.strip()
    df["district"] = df["district"].str.strip()
    if not _BLOCK_RE.match(df["block"].iloc[0]):  # header row
        df = df.iloc[1:]
    df = df[df["block"] != ""]
    bad = ~df["block"].str.match(_BLOCK_RE)
    if bad.any():
        raise ValueError(f"{path}: unexpected block ids, e.g. {df.loc[bad, 'block'].iloc[0]!r}")
    df["district"] = df["district"].str.zfill(2)
    dupes = df["block"].duplicated()
    if dupes.any():
        raise ValueError(f"{path}: {dupes.sum()} blocks assigned more than once")
    return df.reset_index(drop=True)
