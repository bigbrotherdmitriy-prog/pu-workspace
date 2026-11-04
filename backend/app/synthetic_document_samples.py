"""Deterministic marked samples only. Never accepts a live DB or real input."""
from io import BytesIO
from datetime import date
import zipfile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
import app.models
from app.models.organization_contract import Organization
from app.models.project import Project
from app import estimates,work_log,ks_forms,equipment_test_act,id_handover,work_log_form

MARKER='СИНТЕТИЧЕСКИЙ ОБРАЗЕЦ — НЕ ДЛЯ ПОДПИСАНИЯ'


def _marked_docx(content):
    source=BytesIO(content);target=BytesIO()
    with zipfile.ZipFile(source) as old,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as new:
        for name in old.namelist():
            data=old.read(name)
            if name=='word/document.xml':
                xml=data.decode();assert '<w:body>' in xml
                xml=xml.replace('<w:body>','<w:body><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>'+MARKER+'</w:t></w:r></w:p>',1);data=xml.encode()
            new.writestr(name,data)
    return target.getvalue()


def _marked_xlsx(content):
    from openpyxl import load_workbook
    wb=load_workbook(BytesIO(content))
    for ws in wb:
        ws.insert_rows(1);ws.cell(1,1,MARKER);ws.print_title_rows='1:1';ws.print_area=ws.dimensions
        ws.page_setup.paperSize=ws.PAPERSIZE_A4;ws.page_setup.orientation=ws.ORIENTATION_LANDSCAPE
        ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0;ws.sheet_properties.pageSetUpPr.fitToPage=True
        ws.oddHeader.center.text='SYNTHETIC SAMPLE - NOT FOR SIGNING'
    target=BytesIO();wb.save(target);return target.getvalue()


def build_synthetic_samples():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            org=Organization(name='SYNTHETIC organization');db.add(org);db.flush()
            project=Project(name='SYNTHETIC sample project',organization_id=org.id);db.add(project);db.flush()
            estimate=estimates.create_estimate(db,project.id,'SYNTHETIC estimate',{'lines':[{'name':'SYNTHETIC work','unit':'m','qty':'2','unit_price':'10.00','total':'20.00'}]},vat_rate='0')
            line=estimates.get_estimate(db,project.id,estimate['id'])['lines'][0]['id']
            work_log.add_entry(db,project.id,None,{'work_date':'2026-10-05','work_name':'SYNTHETIC work','qty':'1','unit':'m','estimate_line_id':line})
            act=estimates.create_act(db,project.id,estimate['id'],date(2026,10,1),date(2026,10,28))
            data=estimates.act_with_totals(db,project.id,act['id']);assert data['period_total']=='10.00'
            header={key:'SYNTHETIC / '+MARKER for key in ['object_name','customer','contractor','investor','site','contract_no','contractor_rep','customer_rep']};header['date']='2026-10-05'
            equipment=[{'name':'SYNTHETIC device','type_mark':'SYNTHETIC model','serial':'SYNTHETIC serial','program':'SYNTHETIC test only','values':'SYNTHETIC observations','result':'pass'}]
            register=[{'title':'SYNTHETIC test act','kind':'SYNTHETIC mock form','section':'SYNTHETIC','status':'SYNTHETIC draft','required':True,'files':'SYNTHETIC-ID-test.docx'}]
            samples={
                'SYNTHETIC-KS2.docx':_marked_docx(ks_forms.render_ks2_docx(header,data)),
                'SYNTHETIC-KS3.docx':_marked_docx(ks_forms.render_ks3_docx(header,data)),
                'SYNTHETIC-KS2-KS3.xlsx':_marked_xlsx(ks_forms.render_xlsx(header,data)),
                'SYNTHETIC-ID-equipment-test.docx':_marked_docx(equipment_test_act.render(header,equipment)),
                'SYNTHETIC-ID-work-log.docx':_marked_docx(work_log_form.render(db,project.id,header,'2026-10-01','2026-10-28')),
                'SYNTHETIC-ID-register.docx':_marked_docx(id_handover.render_register(header,register)),
                'SYNTHETIC-ID-register.xlsx':_marked_xlsx(id_handover.render_register_xlsx(header,register)),
            }
            return samples,{'synthetic':True,'not_for_signing':True,'dates_and_values':'Fixed synthetic fixtures, not real customer data','estimate_qty':'2','fact_qty':'1.000','unit_price':'10.00','act_period_total':data['period_total'],'vat_rate':data['vat_rate'],'forms':'3 marked examples from existing generators; NOT3newofficialcustomerforms'}
    finally:engine.dispose()
