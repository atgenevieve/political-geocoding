import zipfile

import pandas as pd
import pytest

from geocoding import build, sources
from geocoding.fips import HOUSE_SEATS, cd_label


def test_apportionment_totals_435():
    assert sum(HOUSE_SEATS.values()) == 435


def test_cd_labels():
    assert cd_label("48", "07") == "TX-07"
    assert cd_label("56", "00") == "WY-AL"
    assert cd_label("11", "98") == "DC-AL"


def test_read_pl_block_population(tmp_path):
    geo = "\n".join([
        "PLST|WY|040|  |00|000|01|0000001|0400000US56|56|" + "|" * 80,
        "PLST|WY|750|  |00|000|01|0000002|7500000US560019627001000|560019627001000|" + "|" * 80,
        "PLST|WY|750|  |00|000|01|0000003|7500000US560019627001001|560019627001001|" + "|" * 80,
    ])
    seg = "\n".join([
        "PLST|WY|000|01|0000001|576851|1",
        "PLST|WY|000|01|0000002|12|1",
        "PLST|WY|000|01|0000003|0|1",
    ])
    path = tmp_path / "wy2020.pl.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("wygeo2020.pl", geo)
        zf.writestr("wy000012020.pl", seg)
    df = sources.read_pl_block_population(path, "wy")
    assert df.to_dict("records") == [
        {"block": "560019627001000", "pop": 12},
        {"block": "560019627001001", "pop": 0},
    ]


def test_read_block_assignment_with_header_in_zip(tmp_path):
    path = tmp_path / "cd.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("NationalCD120.txt", "GEOID,CDFP\n010010201001000,02\n010010201001001,7\n")
        zf.writestr("st01_al_cd120.txt", "GEOID,CDFP\n010010201001000,02\n")
    df = sources.read_block_assignment(path, "(?i)national")
    assert df.to_dict("records") == [
        {"block": "010010201001000", "district": "02"},
        {"block": "010010201001001", "district": "07"},
    ]


def test_read_block_assignment_pipe_no_header(tmp_path):
    path = tmp_path / "plan.txt"
    path.write_text("480010001001000|07\n480010001001001|09\n")
    df = sources.read_block_assignment(path)
    assert list(df["district"]) == ["07", "09"]


def test_read_zcta_block_splits_shared_block(tmp_path):
    path = tmp_path / "rel.txt"
    cols = "OID_ZCTA5_20|GEOID_ZCTA5_20|AREALAND_ZCTA5_20|GEOID_TABBLOCK_20|AREALAND_TABBLOCK_20|AREALAND_PART"
    path.write_text("\n".join([
        cols,
        "1|11111|100|010010000001000|100|100",
        "2|22222|100|010010000001001|100|75",
        "3|33333|100|010010000001001|100|25",
        "4||0|010010000001002|50|50",
    ]))
    df = sources.read_zcta_block(path).sort_values(["block", "zcta"])
    assert df["zcta"].tolist() == ["11111", "22222", "33333"]
    assert df["share"].tolist() == [1.0, 0.75, 0.25]


def _base():
    # ZIP 10001 straddles CD01/CD02 (75/25 by people, 50/50 by land).
    # ZIP 10002 is unpopulated and entirely in CD02.
    # One block sits outside any ZCTA.
    return pd.DataFrame({
        "block": ["010010000001000", "010010000001001", "010010000001002", "010010000001003"],
        "zcta": ["10001", "10001", "10002", ""],
        "pop": [300.0, 100.0, 0.0, 5.0],
        "aland": [50.0, 50.0, 10.0, 1.0],
    })


def _assignment():
    return pd.DataFrame({
        "block": ["010010000001000", "010010000001001", "010010000001002", "010010000001003"],
        "district": ["01", "02", "02", "02"],
    })


def test_overlay_percentages():
    pieces, stats = build.zcta_district_overlay(_base(), _assignment())
    rows = pieces[["zcta", "district", "pct_pop", "pct_land", "weight"]].to_dict("records")
    assert rows[0] == {"zcta": "10001", "district": "01", "pct_pop": 75.0, "pct_land": 50.0, "weight": 75.0}
    assert rows[1]["district"] == "02" and rows[1]["pct_pop"] == 25.0
    # unpopulated ZIP falls back to land share
    assert rows[2]["zcta"] == "10002" and pd.isna(rows[2]["pct_pop"]) and rows[2]["weight"] == 100.0
    assert stats["population_outside_zctas"] == 5
    assert stats["unassigned_population"] == 0


def test_outputs(tmp_path):
    pieces, _ = build.zcta_district_overlay(_base(), _assignment())
    summary = build.write_congress_outputs("cdtest", pieces, tmp_path)
    assert summary == {"zips": 2, "zips_split": 1}
    lookup = pd.read_csv(tmp_path / "zip_cd.csv", dtype=str)
    first = lookup.iloc[0].to_dict()
    assert first["zip"] == "10001" and first["cd"] == "AL-01" and first["pct"] == "75.0"
    assert first["all_cds_pct"] == "AL-01 (75.0%), AL-02 (25.0%)"
    assert (tmp_path / "zip_slim.csv").read_text().splitlines()[0] == '10001,AL-01,"AL-01, AL-02","AL-01 (75.0%), AL-02 (25.0%)"'
    assert (tmp_path / "zip" / "10001.csv").read_text() == "AL-01,75.0,AL-01 (75.0%)\nAL-02,25.0,AL-02 (25.0%)\n"
    assert (tmp_path / "zip" / "10002.csv").read_text() == "AL-02,100.0,AL-02 (100.0%)\n"


def test_validate_rejects_wrong_seat_count():
    _, stats = build.zcta_district_overlay(_base(), _assignment())
    with pytest.raises(ValueError, match="AL: 2 districts, expected 7"):
        build.validate_congress(stats)


def test_read_block_assignment_picks_named_district_column(tmp_path):
    path = tmp_path / "plan.txt"
    path.write_text("BLOCKID,STATEFP,CD120FP\n480010001001000,48,07\n480010001001001,48,09\n")
    df = sources.read_block_assignment(path)
    assert list(df["district"]) == ["07", "09"]


def test_summary_skips_unpopulated_slivers(tmp_path):
    base = pd.concat([_base(), pd.DataFrame({
        "block": ["010010000001004"], "zcta": ["10001"], "pop": [0.0], "aland": [5.0]})])
    assignment = pd.concat([_assignment(), pd.DataFrame({
        "block": ["010010000001004"], "district": ["03"]})])
    pieces, _ = build.zcta_district_overlay(base, assignment)
    build.write_congress_outputs("cdtest", pieces, tmp_path)
    lookup = pd.read_csv(tmp_path / "zip_cd.csv", dtype=str)
    assert lookup.iloc[0]["all_cds"] == "AL-01, AL-02"
    detail = pd.read_csv(tmp_path / "zip_cd_detail.csv", dtype=str)
    assert "AL-03" in set(detail["cd"])
