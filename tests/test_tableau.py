"""Package and reference checks for the editable Tableau deliverables."""

import csv
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tableau_builder", ROOT / "scripts" / "build_tableau.py")
tb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tb)


@pytest.fixture
def demo_project(tmp_path):
    source = tmp_path / "artifacts" / "tableau"
    source.mkdir(parents=True)
    for key, (filename, _) in tb.SOURCES.items():
        fields = sorted(tb.REQUIRED[key])
        row = {field: "2026-01-01" if field in tb.DATES else "1" for field in fields}
        row.update({field: "Example" for field in fields if field in tb.STRINGS})
        if key == "customers":
            row.update(customer_id="C001", risk_band="High", churn_probability="0.72", revenue_at_risk="72", expected_90d_revenue="100")
        if key == "forecast":
            row.update(series="Actual", revenue="200", forecast="", lower="", upper="")
        if key == "cohorts":
            row.update(retention_rate="0.4", cohort_size="10", retained_customers="4", month_index="0")
        with (source / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow(row)
    return tmp_path


def test_packaged_workbook_is_portable_and_byte_exact(demo_project):
    result = tb.build_tableau(demo_project)
    with zipfile.ZipFile(result["twbx"]) as archive:
        assert archive.testzip() is None
        root = ET.fromstring(archive.read("Retention_Analytics.twb"))
        assert len(root.findall("./worksheets/worksheet")) == 10
        assert len(root.findall("./dashboards/dashboard")) == 2
        for connection in root.findall("./datasources/datasource/connection"):
            assert connection.get("directory") == "Data"
            filename = connection.get("filename")
            assert archive.read("Data/" + filename) == (demo_project / "artifacts/tableau" / filename).read_bytes()
        assert not any(name.startswith("/") or ".." in name.split("/") for name in archive.namelist())


def test_rebuilding_same_exports_produces_same_package(demo_project):
    first = tb.build_tableau(demo_project)
    first_bytes = Path(first["twbx"]).read_bytes()
    second = tb.build_tableau(demo_project)
    assert first_bytes == Path(second["twbx"]).read_bytes()


def test_missing_required_csv_column_fails(demo_project):
    file = demo_project / "artifacts/tableau/customer_scores.csv"
    file.write_text(file.read_text().replace("churn_probability", "unknown_score"))
    with pytest.raises(ValueError, match="churn_probability"):
        tb.build_tableau(demo_project)


def test_filters_have_real_targets_and_probability_aggregation(demo_project):
    result = tb.build_tableau(demo_project)
    root = ET.parse(result["twb"]).getroot()
    priority = root.find("./worksheets/worksheet[@name='Customer Priority']")
    filters = priority.findall("./table/view/filter")
    assert len(filters) == 3
    assert any(f.find("groupfilter").get("member") == '"High"' for f in filters)
    cards = root.findall("./dashboards/dashboard[@name='Customer Worklist']/zones/zone[@type-v2='filter']")
    assert {c.get("param") for c in cards} == {f.get("column") for f in filters}
    instances = root.findall(".//column-instance[@column='[churn_probability]']")
    assert instances and all(i.get("derivation") == "Avg" for i in instances)
    cohorts = root.findall(".//column-instance[@column='[retention_rate]']")
    assert cohorts and all(i.get("derivation") == "Avg" for i in cohorts)
    assert result["validation"]["native_tableau_opened"] is False


def test_broken_field_reference_is_rejected():
    fields = {key: sorted(value) for key, value in tb.REQUIRED.items()}
    root = tb.workbook(fields, "Data")
    root.find("./worksheets/worksheet/table/cols").text = "[textscan.retention_customers].[sum:missing:qk]"
    with pytest.raises(AssertionError, match="Undeclared field"):
        tb.validate_structure(tb.xml_bytes(root), fields, packaged=True)


def test_official_schema():
    pytest.importorskip("lxml")
    fields = {key: sorted(value) for key, value in tb.REQUIRED.items()}
    root = tb.workbook(fields, "Data")
    assert tb.validate_schema(tb.xml_bytes(root), ROOT / "tableau/schemas").startswith("passed")
