"""Render release documentation from the same maintained user-facing sources."""
import argparse
from pathlib import Path
import re
import tomllib

from docx import Document
from docx.shared import Inches,Pt,RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT=Path(__file__).resolve().parents[1]
VERSION=tomllib.loads((ROOT/"pyproject.toml").read_text())["project"]["version"]
SERIES=".".join(VERSION.split(".")[:2])


def text(paragraph,source):
    for piece in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)',source):
        if piece.startswith('**'):
            run=paragraph.add_run(piece[2:-2]);run.bold=True
        elif piece.startswith('`'):
            run=paragraph.add_run(piece[1:-1]);run.font.name='Arial';run.font.size=Pt(10)
        else:paragraph.add_run(piece)


def build(source,path,images):
    doc=Document();section=doc.sections[0]
    section.page_width=Inches(8.5);section.page_height=Inches(11)
    section.top_margin=Inches(.65);section.bottom_margin=Inches(.65);section.left_margin=Inches(.8);section.right_margin=Inches(.8)
    for name in ('Normal','Body Text','List Paragraph','List Number','List Bullet'):
        style=doc.styles[name];style.font.name='Arial';style.font.size=Pt(11)
        style.paragraph_format.line_spacing=1.12;style.paragraph_format.space_after=Pt(7)
    for name,size in [('Title',25),('Heading 1',16),('Heading 2',13),('Heading 3',12)]:
        style=doc.styles[name];style.font.name='Arial';style.font.size=Pt(size);style.font.color.rgb=RGBColor(0,0,0)
        style.paragraph_format.space_before=Pt(16);style.paragraph_format.space_after=Pt(7)
    for border in doc.styles.element.xpath('.//w:pBdr'):
        border.getparent().remove(border)
    doc.styles['Title'].paragraph_format.space_before=Pt(0)
    header=section.header.paragraphs[0];header.text='GeoTrace  •  Документация версии '+VERSION;header.style=doc.styles['Normal']
    for run in header.runs:run.font.size=Pt(9);run.font.color.rgb=RGBColor.from_string('687383')
    footer=section.footer.paragraphs[0];footer.alignment=2
    run=footer.add_run();field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');run._r.addnext(field)
    lines=source.read_text(encoding='utf-8').splitlines();index=0;current=''
    image_map={'Создание проекта участка':('workspace.png','Проект двух листов с общим участком KML'),
               'Группы полигонов':('classes.png','Управление группами проекта'),
               'Сопоставление листов и проверка стыков':('groups.png','Соответствия исходных групп общим классам проекта'),
               'Макет и экспорт карты':('layout.png','Окно макета перед экспортом'),
               'Что проверено на примере':('map.png','Пример карты участка на двух исходных листах')}
    def picture(file,caption):
        file=Path(file)
        if file.is_file():
            paragraph=doc.add_paragraph();paragraph.paragraph_format.keep_with_next=True
            shape=paragraph.add_run().add_picture(str(file),width=Inches(6.85))
            shape._inline.docPr.set('descr',caption)
            p=doc.add_paragraph(caption);p.paragraph_format.space_after=Pt(10)
            for r in p.runs:r.font.size=Pt(9);r.italic=True
    while index<len(lines):
        line=lines[index].strip();index+=1
        if not line:continue
        match=re.fullmatch(r'!\[([^\]]+)\]\(([^)]+)\)',line)
        if match:
            picture(source.parent/match[2],match[1]);continue
        if line.startswith('# '):doc.add_paragraph(line[2:],'Title');continue
        if line.startswith('## '):
            current=line[3:]
            doc.add_heading(current,level=1);continue
        if line.startswith('|'):
            if current!='Клавиши и поиск действий':doc.add_page_break()
            rows=[line]
            while index<len(lines) and lines[index].lstrip().startswith('|'):
                rows.append(lines[index].strip());index+=1
            parsed=[[s.strip() for s in row.strip('|').split('|')] for row in rows if not re.fullmatch(r'[|\s:\-]+',row)]
            table=doc.add_table(rows=1,cols=len(parsed[0]));table.style='Table Grid'
            # Neutral table styling with repeated headers and modest padding.
            for j,value in enumerate(parsed[0]):text(table.rows[0].cells[j].paragraphs[0],value)
            repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for row in parsed[1:]:
                cells=table.add_row().cells
                for j,value in enumerate(row):text(cells[j].paragraphs[0],value)
            for row in table.rows:
                no_split=OxmlElement('w:cantSplit');row._tr.get_or_add_trPr().append(no_split)
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.space_after=Pt(5)
                        for run in paragraph.runs:run.font.size=Pt(10)
            continue
        numbered=re.match(r'^\d+\.\s+(.*)',line)
        p=doc.add_paragraph(style='Normal')
        if numbered:
            p.paragraph_format.left_indent=Inches(.24)
            p.paragraph_format.first_line_indent=Inches(-.24)
        text(p,line)
    doc.core_properties.title=lines[0].removeprefix('# ');doc.core_properties.subject='GeoTrace '+VERSION
    doc.core_properties.author='GeoTrace';doc.core_properties.last_modified_by='GeoTrace'
    path.parent.mkdir(parents=True,exist_ok=True);doc.save(path)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--screenshots',type=Path);args=parser.parse_args()
    for source,name in [('USER_GUIDE.md',f'GeoTrace-{SERIES}-Руководство.docx'),('PRESENTATION.md',f'GeoTrace-{SERIES}-Возможности.docx')]:
        build(ROOT/'docs'/source,args.output/name,args.screenshots)


if __name__=='__main__':main()
