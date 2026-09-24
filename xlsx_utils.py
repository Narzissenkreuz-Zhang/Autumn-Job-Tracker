from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _column_number(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    value = 0
    for character in letters.group(0) if letters else "A":
        value = value * 26 + ord(character) - 64
    return value


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for item in root.findall("m:si", NS):
        values.append("".join(node.text or "" for node in item.findall(".//m:t", NS)))
    return values


def _worksheet_path(archive: zipfile.ZipFile, requested_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.find("m:sheets", NS)
    if sheets is None or not list(sheets):
        raise ValueError("Excel 文件中没有工作表")
    selected = next((s for s in sheets if s.attrib.get("name") == requested_name), list(sheets)[0])
    relation_id = selected.attrib.get(f"{{{OFFICE_REL}}}id")
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(
        (r.attrib["Target"] for r in relationships.findall("r:Relationship", REL_NS) if r.attrib.get("Id") == relation_id),
        None,
    )
    if not target:
        raise ValueError("无法定位工作表内容")
    target = target.lstrip("/")
    return target if target.startswith("xl/") else f"xl/{target}"


def read_rows(path: str | Path, sheet_name: str = "秋招跟踪") -> list[list[object | None]]:
    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        root = ET.fromstring(archive.read(_worksheet_path(archive, sheet_name)))
        result: list[list[object | None]] = []
        for row in root.findall(".//m:sheetData/m:row", NS):
            row_number = int(row.attrib.get("r", len(result) + 1))
            while len(result) < row_number:
                result.append([])
            values: list[object | None] = []
            for cell in row.findall("m:c", NS):
                column = _column_number(cell.attrib.get("r", "A1"))
                while len(values) < column:
                    values.append(None)
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    inline = cell.find("m:is", NS)
                    value = "".join(node.text or "" for node in inline.findall(".//m:t", NS)) if inline is not None else ""
                else:
                    raw = cell.findtext("m:v", default="", namespaces=NS)
                    if cell_type == "s" and raw:
                        value = strings[int(raw)]
                    elif cell_type in {"str", "e"}:
                        value = raw
                    elif cell_type == "b":
                        value = raw == "1"
                    elif raw == "":
                        value = None
                    else:
                        try:
                            number = float(raw)
                            value = int(number) if number.is_integer() else number
                        except ValueError:
                            value = raw
                values[column - 1] = value
            result[row_number - 1] = values
    return result


def _cell(reference: str, value: object, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{reference}"{style_attr}/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
    text = escape(str(value))
    preserve = ' xml:space="preserve"' if text.startswith(" ") or text.endswith(" ") else ""
    return f'<c r="{reference}" t="inlineStr"{style_attr}><is><t{preserve}>{text}</t></is></c>'


def _column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _sheet_xml(headers: list[str], rows: list[list[object]], widths: list[int]) -> str:
    all_rows = [headers, *rows]
    xml_rows = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = []
        for column_index in range(1, len(headers) + 1):
            value = row[column_index - 1] if column_index <= len(row) else None
            cells.append(_cell(f"{_column_letter(column_index)}{row_index}", value, 1 if row_index == 1 else 0))
        xml_rows.append(f'<row r="{row_index}" ht="{28 if row_index == 1 else 22}" customHeight="1">{"".join(cells)}</row>')
    column_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    last_cell = f"{_column_letter(len(headers))}{len(all_rows)}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_cell}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{column_xml}</cols><sheetData>{"".join(xml_rows)}</sheetData>'
        f'<autoFilter ref="A1:{_column_letter(len(headers))}{len(all_rows)}"/>'
        '</worksheet>'
    )


def export_workbook(path: str | Path, companies: list[dict], positions: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    company_headers = ["公司", "公司性质", "投递截止", "是否投递", "当前阶段", "笔试时间", "统一面试时间", "岗位数量", "官网", "备注"]
    company_rows = [
        [c["name"], c["nature"], c["deadline"], c["submitted"], c["stage"], c["exam_time"], c["interview_time"], c["position_count"], c["website"], c["notes"]]
        for c in companies
    ]
    position_headers = ["公司", "Base（城市）", "部门 / 机构", "岗位", "岗位状态", "笔试时间", "面试时间", "岗位链接", "备注"]
    position_rows = [
        [p["company_name"], p["base"], p["department"], p["title"], p["status_override"], p["exam_override"], p["interview_override"], p["link"], p["notes"]]
        for p in positions
    ]
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="单位进度" sheetId="1" r:id="rId1"/><sheet name="岗位明细" sheetId="2" r:id="rId2"/></sheets></workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2F4858"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf></cellXfs>
</styleSheet>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(company_headers, company_rows, [20, 12, 14, 12, 14, 18, 18, 11, 38, 28]))
        archive.writestr("xl/worksheets/sheet2.xml", _sheet_xml(position_headers, position_rows, [20, 14, 24, 28, 16, 18, 18, 38, 28]))
