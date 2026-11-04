import pytest
from app.drawing_points import validate_point, bind_point
from app.provisional_policy import ProvisionalPolicy


@pytest.mark.parametrize('changes',[{'version_id':True},{'version_id':0},{'page':0},{'page':True},{'x':'NaN'},{'x':'Infinity'},{'x':True},{'y':-0.1},{'y':1.1},{'x':'1e-100000'}])
def test_invalid_point_rejected(changes):
    data=dict(version_id=1,page=1,x=0,y=1);data.update(changes)
    with pytest.raises(ValueError):validate_point(**data)


def test_disabled_policy_fails_closed():
    with pytest.raises(ValueError,match='disabled'):validate_point(1,1,0,0,policy=ProvisionalPolicy(drawing_point='disabled'))


def test_version_bound_point_idempotence_and_foreign_scope(db_session):
    from app.models.organization_contract import Organization
    from app.models.project import Project
    from app.models.rd import RdVolume,RdVolumeVersion
    from app.defects import create_defect,get_defect
    db=db_session;org=Organization(name='SYNTHETIC');db.add(org);db.flush()
    p=Project(name='SYNTHETIC',organization_id=org.id);other=Project(name='SYNTHETIC other',organization_id=org.id);db.add_all([p,other]);db.flush()
    volume=RdVolume(project_id=p.id,title='SYNTHETIC drawing',file_name='SYNTHETIC.pdf',provider='local');db.add(volume);db.flush()
    version=RdVolumeVersion(volume_id=volume.id,content_sha256='a'*64,size_bytes=0,file_name='SYNTHETIC.pdf',mime_type='application/pdf');db.add(version);db.flush()
    defect=create_defect(db,p.id,None,{'title':'SYNTHETIC point'})
    first=bind_point(db,p.id,defect['id'],version_id=version.id,page=1,x='0.25',y='0.5')
    assert bind_point(db,p.id,defect['id'],version_id=version.id,page=1,x='0.250',y='0.50')==first
    assert get_defect(db,p.id,defect['id'])['drawing_point']==first
    with pytest.raises(ValueError,match='immutable'):bind_point(db,p.id,defect['id'],version_id=version.id,page=2,x=0.25,y=0.5)
    with pytest.raises(LookupError):bind_point(db,other.id,defect['id'],version_id=version.id,page=1,x=0,y=0)
