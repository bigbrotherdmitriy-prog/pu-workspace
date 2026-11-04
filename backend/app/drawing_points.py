"""Provisional page-relative reference, not proof that a PDF page was rendered."""
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, text
from app.models.defects import Defect, DefectDrawingPoint
from app.provisional_policy import get_policy


def validate_point(version_id, page, x, y, *, policy=None):
    policy = policy or get_policy()
    if policy.drawing_point == 'disabled': raise ValueError('Drawing points disabled by provisional policy')
    if any(type(v) is not int or not 1 <= v <= 2147483647 for v in [version_id,page]):
        raise ValueError('Invalid immutable version or 1-based page')
    coordinates=[]
    for value in [x,y]:
        if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)) or len(str(value))>32:
            raise ValueError('Invalid normalized coordinate')
        try:
            number=Decimal(str(value))
            if not number.is_finite() or not 0 <= number <= 1:raise ValueError('Coordinate outside page')
            if number and number.adjusted() < -24:raise ValueError('Coordinate precision budget exceeded')
            # Zero's exponent carries no position information. Canonicalize it
            # before fixed-point formatting, which can otherwise allocate huge strings.
            coordinates.append('0' if number.is_zero() else format(number,'f'))
        except InvalidOperation as exc:raise ValueError('Invalid normalized coordinate') from exc
    return {'rd_version_id':version_id,'page':page,'x':coordinates[0],'y':coordinates[1],'coordinate_system':'normalized_page_v1'}


def bind_point(db,project_id,defect_id,*,version_id,page,x,y,policy=None):
    intent=validate_point(version_id,page,x,y,policy=policy)
    from app.defects import bind_rd_version
    with db.no_autoflush:
        if db.get_bind().dialect.name=='sqlite':
            db.execute(text('UPDATE defects SET id=id WHERE id=:id AND project_id=:p'),{'id':defect_id,'p':project_id})
        elif db.get_bind().dialect.name!='postgresql':raise RuntimeError('Unsupported drawing point database')
        row=db.scalar(select(Defect).where(Defect.id==defect_id,Defect.project_id==project_id).with_for_update())
        if row is None:raise LookupError('Defect not found in project')
        existing=db.scalar(select(DefectDrawingPoint).where(DefectDrawingPoint.defect_id==defect_id,DefectDrawingPoint.project_id==project_id))
        if existing:
            stored={key:getattr(existing,key) for key in intent}
            # Decimal-equivalent repeats are the same point, regardless of notation.
            if (stored['rd_version_id'],stored['page'],Decimal(stored['x']),Decimal(stored['y'])) != (version_id,page,Decimal(intent['x']),Decimal(intent['y'])):
                raise ValueError('Drawing point already bound; version/coordinates are immutable')
            return stored
        bind_rd_version(db,project_id,defect_id,version_id)
        db.execute(DefectDrawingPoint.__table__.insert().values(project_id=project_id,defect_id=defect_id,**intent))
        return intent
