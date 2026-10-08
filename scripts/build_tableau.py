"""Build editable Tableau 2026.1 workbooks from the pipeline's CSV exports.

The builder uses the standard library. Pass --validate-schema to additionally
check the official Tableau XSD with lxml. Native Tableau rendering is a separate
validation step; XML success must never be presented as a native render check.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
import zipfile


USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)
ROOT = Path(__file__).resolve().parents[1]
WORKBOOK_NAME = "Retention_Analytics"
VERSION = "26.1"
NAVY = "#132F42"
TEAL = "#177E89"
ORANGE = "#D88B36"
RED = "#BB4A4A"
LIGHT = "#F4F7F8"

SOURCES = {
    "customers": ("customer_scores.csv", "Customer lifecycle and churn scores"),
    "forecast": ("sales_forecast.csv", "Monthly sales and six-month outlook"),
    "cohorts": ("cohorts.csv", "Signup cohort purchase retention"),
    "monthly": ("monthly_sales.csv", "Monthly completed-order revenue"),
}
REQUIRED = {
    "customers": {"customer_id", "snapshot_date", "acquisition_channel", "country", "segment",
                  "risk_band", "churn_probability", "recommended_action", "reason", "recency_days",
                  "revenue_at_risk", "expected_90d_revenue"},
    "forecast": {"month", "series", "revenue", "forecast", "lower", "upper"},
    "cohorts": {"cohort_month", "month_index", "cohort_size", "retained_customers", "retention_rate"},
    "monthly": {"month", "revenue", "orders", "active_customers"},
}
STRINGS = {"customer_id", "acquisition_channel", "country", "segment", "risk_band", "reason",
           "recommended_action", "series"}
DATES = {"month", "cohort_month", "snapshot_date", "signup_date"}
INTEGERS = {"month_index", "cohort_size", "retained_customers", "orders", "active_customers",
            "tenure_days", "recency_days", "orders_30d", "orders_90d", "orders_180d", "lifetime_orders",
            "days_to_first_order", "churned"}
PERCENTAGES = {"churn_probability", "retention_rate"}
CURRENCY = {"revenue", "forecast", "lower", "upper", "revenue_at_risk", "expected_90d_revenue",
            "revenue_90d", "revenue_180d", "lifetime_revenue", "avg_order_value", "sales_display"}
CAPTIONS = {
    "customer_id": "Customer ID", "churn_probability": "90-day churn probability",
    "revenue_at_risk": "90-day revenue exposure (USD)",
    "expected_90d_revenue": "90-day revenue proxy (USD)",
    "sales_display": "Monthly revenue / forecast (USD)",
    "month_index": "Months since signup", "cohort_month": "Signup cohort",
    "retention_rate": "Purchased in month / cohort size",
    "lower": "Approximate lower forecast bound (USD)",
    "upper": "Approximate upper forecast bound (USD)",
}


def node(parent, tag, text=None, **attrs):
    result = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})
    if text is not None:
        result.text = str(text)
    return result


def source_name(key):
    return "textscan.retention_" + key


def datatype(field):
    return "string" if field in STRINGS else "date" if field in DATES else "integer" if field in INTEGERS else "real"


def field_column(parent, field):
    dtype = datatype(field)
    dimension = dtype in ("string", "date") or field == "month_index"
    attrs = {"name": f"[{field}]", "datatype": dtype,
             "role": "dimension" if dimension else "measure",
             "type": "ordinal" if dtype == "date" or field == "month_index" else "nominal" if dimension else "quantitative",
             "caption": CAPTIONS.get(field, field.replace("_", " ").title())}
    if field in PERCENTAGES:
        attrs.update(default_format="p0.0%", aggregation="Avg")
    elif field in CURRENCY:
        attrs.update(default_format='c"$"#,##0;("$"#,##0)', aggregation="Sum")
    elif dtype == "date":
        attrs.update(default_format="mmm yyyy")
    result = node(parent, "column", **attrs)
    if field == "sales_display":
        node(result, "calculation", **{"class": "tableau", "formula": "IFNULL([revenue], [forecast])"})
    return result


def add_source(parent, key, fields, data_dir):
    filename, caption = SOURCES[key]
    ds = node(parent, "datasource", caption=caption, inline="true", name=source_name(key), version=VERSION)
    connection = node(ds, "connection", **{"class": "textscan", "directory": str(data_dir).replace("\\", "/"),
                                           "filename": filename, "password": "", "server": ""})
    relation = node(connection, "relation", name=filename, table=f"[{Path(filename).stem}#csv]", type="table")
    cols = node(relation, "columns", character_set="UTF-8", header="yes", locale="en_US", separator=",")
    for index, field in enumerate(fields):
        node(cols, "column", name=field, datatype=datatype(field), ordinal=index)
    records = node(connection, "metadata-records")
    remote_types = {"string": 129, "integer": 20, "real": 5, "date": 133}
    for index, field in enumerate(fields):
        dtype = datatype(field)
        record = node(records, "metadata-record", **{"class": "column"})
        for tag, value in (("remote-name", field), ("remote-type", remote_types[dtype]),
                           ("local-name", f"[{field}]"), ("parent-name", f"[{Path(filename).stem}#csv]"),
                           ("remote-alias", field), ("ordinal", index), ("local-type", dtype),
                           ("aggregation", "Count" if dtype in ("string", "date") else "Avg" if field in PERCENTAGES else "Sum"),
                           ("contains-null", "true")):
            node(record, tag, value)
    node(ds, "aliases", enabled="yes")
    for field in fields:
        field_column(ds, field)
    if key == "forecast":
        field_column(ds, "sales_display")
    return ds


def pill(field, derivation="None", field_type=None):
    """Return (field, derivation, instance-type); date truncation is explicit."""
    if field_type is None:
        field_type = "ordinal" if datatype(field) == "date" or field == "month_index" else "nominal" if derivation == "None" else "quantitative"
    return field, derivation, field_type


def instance(p):
    field, derivation, field_type = p
    code = {"None": "none", "Sum": "sum", "Avg": "avg", "Count": "cnt", "CountD": "ctd", "Month": "tmn", "Min": "min", "Max": "max"}[derivation]
    suffix = {"nominal": "nk", "ordinal": "ok", "quantitative": "qk"}[field_type]
    return f"[{code}:{field}:{suffix}]"


def ref(key, p):
    return f"[{source_name(key)}].{instance(p)}"


def simple_id(parent, text):
    node(parent, "simple-id", uuid="{" + str(uuid.uuid5(uuid.NAMESPACE_URL, "retention/" + text)).upper() + "}")


def style(parent, element, **formats):
    rule = node(parent, "style-rule", element=element)
    for attribute, value in formats.items():
        node(rule, "format", attr=attribute.replace("_", "-"), value=value)
    return rule


def add_sheet(parent, name, key, rows=(), cols=(), mark="Bar", encodings=(), filters=(),
              sort=None, tooltip=None, kpi=False):
    ws = node(parent, "worksheet", name=name)
    layout = node(ws, "layout-options")
    title = node(node(layout, "title"), "formatted-text")
    node(title, "run", name, bold="true", fontsize="12", fontcolor=NAVY)
    table = node(ws, "table")
    view = node(table, "view")
    sources = node(view, "datasources")
    node(sources, "datasource", name=source_name(key), caption=SOURCES[key][1])
    dependencies = node(view, "datasource-dependencies", datasource=source_name(key))
    used = list(rows) + list(cols) + [p for _, p in encodings] + [pill(field) for field, _ in filters]
    if sort:
        used += list(sort)
    used = list(dict.fromkeys(used))
    fields = list(dict.fromkeys(p[0] for p in used))
    if "sales_display" in fields:
        fields += ["revenue", "forecast"]
    for field in dict.fromkeys(fields):
        field_column(dependencies, field)
    for p in used:
        node(dependencies, "column-instance", column=f"[{p[0]}]", derivation=p[1], name=instance(p), pivot="key", type=p[2])
    for field, selection in filters:
        p = pill(field)
        filter_node = node(view, "filter", **{"class": "categorical", "column": ref(key, p)})
        if selection is None:
            gf = node(filter_node, "groupfilter", function="level-members", level=instance(p))
            gf.set("{" + USER_NS + "}ui-enumeration", "all")
        else:
            gf = node(filter_node, "groupfilter", function="member", level=instance(p), member=json.dumps(selection))
        gf.set("{" + USER_NS + "}ui-marker", "enumerate")
    if sort:
        node(view, "computed-sort", column=ref(key, sort[0]), direction="DESC", using=ref(key, sort[1]))
    if filters:
        slices = node(view, "slices")
        for field, _ in filters:
            node(slices, "column", ref(key, pill(field)))
    node(view, "aggregation", value="true")
    table_style = node(table, "style")
    style(table_style, "worksheet", font_family="Arial", font_size="10", color=NAVY)
    style(table_style, "pane", background_color="#FFFFFF")
    pane = node(node(table, "panes"), "pane", selection_relaxation_option="selection-relaxation-allow")
    node(node(pane, "view"), "breakdown", value="auto")
    node(pane, "mark", **{"class": mark})
    enc = node(pane, "encodings")
    for encoding, p in encodings:
        node(enc, encoding, column=ref(key, p))
    if tooltip:
        formatted = node(node(pane, "customized-tooltip", show_buttons="true"), "formatted-text")
        node(formatted, "run", tooltip, fontsize="10")
    pane_style = node(pane, "style")
    mark_style = style(pane_style, "mark", color=TEAL, mark_labels_show="true" if kpi else "false")
    if kpi:
        node(mark_style, "format", attr="font-size", value="28")
        node(mark_style, "format", attr="font-weight", value="bold")
        node(mark_style, "format", attr="text-align", value="center")
    color_encoding = next((p for encoding, p in encodings if encoding == "color"), None)
    if color_encoding and color_encoding[0] in ("risk_band", "series"):
        palette = {"High": RED, "Medium": ORANGE, "Low": TEAL} if color_encoding[0] == "risk_band" else {"Actual": TEAL, "Forecast": ORANGE}
        encoding = node(mark_style, "encoding", attr="color", field=ref(key, color_encoding), type="palette")
        for member, color in palette.items():
            map_node = node(encoding, "map", to=color)
            node(map_node, "bucket", json.dumps(member))
    def shelf(values):
        return "" if not values else ref(key, values[0]) if len(values) == 1 else "(" + " / ".join(ref(key, p) for p in values) + ")"
    node(table, "rows", shelf(rows))
    node(table, "cols", shelf(cols))
    simple_id(ws, "worksheet/" + name)
    return ws


def zone(zones, zone_id, x, y, width, height, **attrs):
    result = node(zones, "zone", id=zone_id, x=x, y=y, w=width, h=height, **attrs)
    return result


def zone_finish(item, background="#FFFFFF"):
    zs = node(item, "zone-style")
    for attribute, value in (("background-color", background), ("border-style", "none"), ("margin", "8")):
        node(zs, "format", attr=attribute, value=value)


def text_zone(zones, zone_id, x, y, width, height, text, fontsize=12, background=LIGHT, color=NAVY):
    item = zone(zones, zone_id, x, y, width, height, type_v2="text")
    formatted = node(item, "formatted-text")
    node(formatted, "run", text, fontname="Arial", fontsize=fontsize, fontcolor=color)
    zone_finish(item, background)


def add_dashboard(parent, name, items, heading, footer, height=1050):
    dashboard = node(parent, "dashboard", name=name)
    ds_style = node(dashboard, "style")
    style(ds_style, "dashboard", background_color=LIGHT)
    node(dashboard, "size", maxheight=height, minheight=height, maxwidth=1440, minwidth=1440)
    zones = node(dashboard, "zones")
    text_zone(zones, 1, 0, 0, 100000, 7600, heading, fontsize=22, background=NAVY, color="#FFFFFF")
    for index, (sheet, x, y, width, h) in enumerate(items, start=2):
        item = zone(zones, index, x, y, width, h, name=sheet, show_title="true")
        zone_finish(item)
    text_zone(zones, 90, 500, 94700, 99000, 5000, footer, fontsize=10)
    simple_id(dashboard, "dashboard/" + name)
    return dashboard


def add_window(parent, name, dashboard=False, filters=(), sheets=()):
    result = node(parent, "window", **{"class": "dashboard" if dashboard else "worksheet", "name": name})
    if dashboard:
        viewpoints = node(result, "viewpoints")
        for sheet in sheets:
            vp = node(viewpoints, "viewpoint", name=sheet)
            if sheet != "Customer Priority":
                node(vp, "zoom", type="entire-view")
        node(result, "active", id="2")
    else:
        cards = node(result, "cards")
        left = node(node(cards, "edge", name="left"), "strip", size="180")
        for kind in ("pages", "filters", "marks"):
            node(left, "card", type=kind)
        top = node(cards, "edge", name="top")
        for kind in ("columns", "rows", "title"):
            node(node(top, "strip", size="32"), "card", type=kind)
        if filters:
            right = node(node(cards, "edge", name="right"), "strip", size="200")
            for field in filters:
                node(right, "card", type="filter", param=ref("customers", pill(field)))
        vp = node(result, "viewpoint")
        if name != "Customer Priority":
            node(vp, "zoom", type="entire-view")
    simple_id(result, "window/" + name)


def workbook(fields, data_dir):
    wb = ET.Element("workbook", {"original-version": VERSION, "version": VERSION,
                                 "source-build": "0.0.0 (0000.0.0.0)", "source-platform": "win"})
    node(node(wb, "document-format-change-manifest"), "ManifestByVersion")
    sources = node(wb, "datasources")
    for key in SOURCES:
        add_source(sources, key, fields[key], data_dir)
    sheets = node(wb, "worksheets")
    count = pill("customer_id", "CountD")
    exposure = pill("revenue_at_risk", "Sum")
    probability = pill("churn_probability", "Avg")
    risk = pill("risk_band")
    segment = pill("segment")
    customer = pill("customer_id")
    action = pill("recommended_action")
    add_sheet(sheets, "Eligible Customers", "customers", mark="Text", encodings=(("text", count),), kpi=True)
    add_sheet(sheets, "Average 90-day Churn Risk", "customers", mark="Text", encodings=(("text", probability),), kpi=True)
    add_sheet(sheets, "Revenue Exposure - Proxy", "customers", mark="Text", encodings=(("text", exposure),), kpi=True)
    add_sheet(sheets, "Risk Mix", "customers", rows=(risk,), cols=(count,),
              encodings=(("color", risk), ("text", count), ("tooltip", probability)))
    add_sheet(sheets, "Revenue Exposure by Segment", "customers", rows=(segment,), cols=(exposure,),
              encodings=(("color", risk), ("tooltip", count), ("tooltip", probability)), sort=(segment, exposure))
    add_sheet(sheets, "Sales Outlook - Python Model", "forecast", rows=(pill("sales_display", "Sum"),),
              cols=(pill("month", "Month", "quantitative"),), mark="Line",
              encodings=(("color", pill("series")), ("tooltip", pill("lower", "Min")), ("tooltip", pill("upper", "Max"))),
              tooltip="Monthly completed-order revenue and six-month model forecast.\nOrange: future forecast. Teal: observed sales.\nBounds are approximate, not a guarantee.\nMonth: <" + ref("forecast", pill("month", "Month", "quantitative")) + ">\nRevenue / forecast: <" + ref("forecast", pill("sales_display", "Sum")) + ">\nLower: <" + ref("forecast", pill("lower", "Min")) + ">\nUpper: <" + ref("forecast", pill("upper", "Max")) + ">")
    add_sheet(sheets, "Signup Cohort Retention", "cohorts", rows=(pill("cohort_month"),),
              cols=(pill("month_index"),), mark="Square",
              encodings=(("color", pill("retention_rate", "Avg")), ("tooltip", pill("cohort_size", "Min")),
                         ("tooltip", pill("retained_customers", "Sum"))),
              tooltip="Share of signup cohort making a completed purchase in the indicated month.\nUnobserved future months are blank. This is monthly activity, not uninterrupted survival.\nCohort size: <" + ref("cohorts", pill("cohort_size", "Min")) + ">\nRetained customers: <" + ref("cohorts", pill("retained_customers", "Sum")) + ">\nRetention: <" + ref("cohorts", pill("retention_rate", "Avg")) + ">")
    filters = (("risk_band", "High"), ("segment", None), ("acquisition_channel", None))
    add_sheet(sheets, "Customer Priority", "customers", rows=(customer, segment, action), cols=(exposure,),
              encodings=(("color", risk), ("tooltip", probability), ("tooltip", pill("reason")),
                         ("tooltip", pill("recency_days", "Min"))), filters=filters, sort=(customer, exposure))
    add_sheet(sheets, "Retention Playbook", "customers", rows=(segment, action), cols=(count,),
              encodings=(("color", risk), ("tooltip", exposure)), sort=(segment, exposure))
    add_sheet(sheets, "Sales History - Tableau Forecast Ready", "monthly", rows=(pill("revenue", "Sum"),),
              cols=(pill("month", "Month", "quantitative"),), mark="Line",
              encodings=(("tooltip", pill("orders", "Sum")),))
    dashboards = node(wb, "dashboards")
    overview_items = [("Eligible Customers", 1000, 8500, 32000, 10000),
                      ("Average 90-day Churn Risk", 34000, 8500, 32000, 10000),
                      ("Revenue Exposure - Proxy", 67000, 8500, 32000, 10000),
                      ("Sales Outlook - Python Model", 1000, 20000, 63500, 28500),
                      ("Risk Mix", 65500, 20000, 33500, 28500),
                      ("Signup Cohort Retention", 1000, 50000, 63500, 43500),
                      ("Revenue Exposure by Segment", 65500, 50000, 33500, 43500)]
    add_dashboard(dashboards, "Retention Overview", overview_items,
                  "RETENTION ANALYTICS  |  Customer lifecycle & revenue outlook",
                  "Synthetic demonstration | Portfolio KPIs cover eligible customers only. Revenue exposure = prior-180-day revenue / 2 × churn probability; it is not causal lost revenue. Explore the Customer Worklist tab.")
    worklist_items = [("Customer Priority", 1000, 17500, 98000, 54000),
                      ("Retention Playbook", 1000, 73000, 98000, 20500)]
    worklist = add_dashboard(dashboards, "Customer Worklist", worklist_items,
                            "CUSTOMER WORKLIST  |  Prioritize retention outreach",
                            "Filters affect Customer Priority only. The playbook remains a portfolio reference. Recommendations are hypotheses to test in a randomized holdout; no retention lift is established.")
    zones = worklist.find("zones")
    for index, field in enumerate(("risk_band", "segment", "acquisition_channel")):
        item = zone(zones, 70 + index, 1000 + index * 33000, 8700, 32000, 7200,
                    type_v2="filter", name="Customer Priority", param=ref("customers", pill(field)), mode="dropdown")
        zone_finish(item)
    windows = node(wb, "windows", source_height="1000")
    for ws in sheets:
        add_window(windows, ws.get("name"), filters=("risk_band", "segment", "acquisition_channel") if ws.get("name") == "Customer Priority" else ())
    add_window(windows, "Customer Worklist", True, sheets=[v[0] for v in worklist_items])
    add_window(windows, "Retention Overview", True, sheets=[v[0] for v in overview_items])
    explain = node(wb, "explain-data", enabled_for_viewer="false", extreme_values_enabled_for_all="false")
    node(explain, "explanation-types")
    return wb


def xml_bytes(wb):
    ET.indent(wb, space="  ")
    return ET.tostring(wb, encoding="utf-8", xml_declaration=True)


def validate_structure(xml_data, csv_headers, packaged=False):
    """Check cross-references not covered by Tableau's published XSD."""
    wb = ET.fromstring(xml_data)
    sources = {ds.get("name"): ds for ds in wb.findall("./datasources/datasource")}
    sheets = {ws.get("name"): ws for ws in wb.findall("./worksheets/worksheet")}
    assert len(sheets) == len(wb.findall("./worksheets/worksheet")), "Duplicate worksheet names"
    for key in SOURCES:
        ds = sources[source_name(key)]
        csv_cols = [col.get("name") for col in ds.findall("./connection/relation/columns/column")]
        assert csv_cols == csv_headers[key], f"CSV field mismatch: {key}"
        connection = ds.find("connection")
        if packaged:
            assert connection.get("directory") == "Data", "Packaged connection must stay inside Data"
        else:
            assert (Path(connection.get("directory")) / connection.get("filename")).is_file()
    pattern = re.compile(r"\[([^\]]+)\]\.\[([^\]]+)\]")
    for ws in sheets.values():
        deps = ws.find("./table/view/datasource-dependencies")
        ds_name = deps.get("datasource")
        assert ds_name in sources, "Unknown datasource"
        known_columns = {col.get("name") for col in sources[ds_name].findall("column")}
        instances = {col.get("name") for col in deps.findall("column-instance")}
        for col in deps.findall("column-instance"):
            assert col.get("column") in known_columns, f"Unknown source field {col.get('column')}"
        for element in ws.iter():
            values = list(element.attrib.values()) + ([element.text] if element.text else [])
            for value in values:
                for referenced_ds, referenced_field in pattern.findall(value):
                    assert referenced_ds == ds_name, f"Wrong datasource reference in {ws.get('name')}"
                    assert f"[{referenced_field}]" in instances, f"Undeclared field instance {referenced_field}"
    for z in wb.findall("./dashboards/dashboard/zones/zone"):
        if z.get("name"):
            assert z.get("name") in sheets, "Dashboard points to missing worksheet"
        if z.get("type-v2") == "filter":
            targets = {f.get("column") for f in sheets[z.get("name")].findall("./table/view/filter")}
            assert z.get("param") in targets, "Dashboard filter lacks matching worksheet filter"
    return {"worksheets": len(sheets), "dashboards": len(wb.findall("./dashboards/dashboard")),
            "data_sources": len(sources), "cross_reference_checks": "passed"}


def validate_schema(xml_data, schema_dir):
    """Validate an unchanged official schema using two local namespace shims.

    Tableau publishes namespace imports without their XSD locations. The shims
    define only user extension attributes and xml:base, not workbook structure.
    """
    from lxml import etree

    schema_doc = etree.parse(str(schema_dir / "twb_2026.1.0.xsd"))
    ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
    for item in schema_doc.findall("xs:import", ns):
        item.set("schemaLocation", (schema_dir / ("user_namespace.xsd" if item.get("namespace") == USER_NS else "xml_namespace.xsd")).resolve().as_uri())
    schema = etree.XMLSchema(schema_doc)
    schema.assertValid(etree.fromstring(xml_data))
    return "passed (official Tableau 2026.1 XSD; local user/xml namespace shims)"


def build_tableau(root=ROOT, output_dir="artifacts", validate_xsd=False):
    root = Path(root).resolve()
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    source_dir = output_dir / "tableau"
    target = root / "tableau"
    data_dir = target / "Data"
    data_dir.mkdir(parents=True, exist_ok=True)
    fields, row_counts = {}, {}
    for key, (filename, _) in SOURCES.items():
        input_path = source_dir / filename
        with input_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields[key] = reader.fieldnames or []
            missing = REQUIRED[key] - set(fields[key])
            if missing:
                raise ValueError(f"{filename} is missing columns: {sorted(missing)}")
            row_counts[filename] = sum(1 for _ in reader)
            if not row_counts[filename]:
                raise ValueError(f"{filename} has no rows")
        shutil.copyfile(input_path, data_dir / filename)
    wb = workbook(fields, data_dir)
    xml_data = xml_bytes(wb)
    report = validate_structure(xml_data, fields)
    report.update({"tableau_minimum_version": "2026.1", "csv_rows": row_counts,
                   "native_tableau_opened": False,
                   "native_validation_note": "Tableau Desktop/Reader is unavailable in this environment. Opening, querying, dashboard rendering and filter interactions have not been verified in Tableau.",
                   "xsd_validation": "not run; use --validate-schema with lxml installed"})
    if validate_xsd:
        report["xsd_validation"] = validate_schema(xml_data, target / "schemas")
    twb_path = target / (WORKBOOK_NAME + ".twb")
    twb_path.write_bytes(xml_data)
    package_wb = deepcopy(wb)
    for connection in package_wb.findall("./datasources/datasource/connection"):
        connection.set("directory", "Data")
    package_xml = xml_bytes(package_wb)
    validate_structure(package_xml, fields, packaged=True)
    if validate_xsd:
        validate_schema(package_xml, target / "schemas")
    package_path = target / (WORKBOOK_NAME + ".twbx")
    members = {WORKBOOK_NAME + ".twb": package_xml}
    members.update({"Data/" + filename: (data_dir / filename).read_bytes() for filename, _ in SOURCES.values()})
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    with zipfile.ZipFile(package_path) as archive:
        assert archive.testzip() is None, "Corrupt package"
        assert set(archive.namelist()) == set(members), "Missing packaged files"
        for name, content in members.items():
            assert archive.read(name) == content
    report["package_checks"] = "passed: ZIP integrity, member set, relative paths and byte-identical source CSVs"
    report["package_sha256"] = hashlib.sha256(package_path.read_bytes()).hexdigest()
    (target / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"twb": str(twb_path), "twbx": str(package_path), "validation": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"), help="Pipeline artifact directory (relative to project root or absolute)")
    parser.add_argument("--validate-schema", action="store_true", help="Run official XSD validation; requires lxml")
    args = parser.parse_args()
    print(json.dumps(build_tableau(args.root, args.output_dir, args.validate_schema), indent=2))


if __name__ == "__main__":
    main()
