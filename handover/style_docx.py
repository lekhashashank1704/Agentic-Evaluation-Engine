"""Post-process a pandoc DOCX: brand fonts and colours, bordered tables with shaded headers."""

import sys

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

NAVY = RGBColor(0x0B, 0x23, 0x45)
BLUE = RGBColor(0x1D, 0x4E, 0xD8)

doc = Document(sys.argv[1])
by_name = {s.name: s for s in doc.styles}
for name in ("Normal", "Body Text", "First Paragraph", "Compact"):
    if name in by_name:
        st = by_name[name]
        st.font.name = "Calibri"
        st.font.size = Pt(10.5)
for name, size, color in (("Title", 26, NAVY), ("Subtitle", 14, BLUE), ("Heading 1", 16, NAVY),
                          ("Heading 2", 13, BLUE), ("Heading 3", 11.5, NAVY)):
    if name in by_name:
        st = by_name[name]
        st.font.name = "Calibri"
        st.font.size = Pt(size)
        st.font.color.rgb = color
        st.font.bold = name != "Subtitle"

for name in ("Source Code", "Verbatim Char"):
    if name in by_name:
        by_name[name].font.size = Pt(7)

for table in doc.tables:
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "C8D1DC")
        borders.append(el)
    tbl_pr.append(borders)
    width = OxmlElement("w:tblW")
    width.set(qn("w:w"), "5000")
    width.set(qn("w:type"), "pct")
    tbl_pr.append(width)
    for i, row in enumerate(table.rows):
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
                    if i == 0:
                        r.font.bold = True
            if i == 0:
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:fill"), "E8EEF8")
                cell._tc.get_or_add_tcPr().append(shd)
doc.save(sys.argv[1])
