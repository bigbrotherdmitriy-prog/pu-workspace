"""Pure synthetic envelope tests; no DB, auth override, HTTP or provider."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.api.work_log import WorkLogBody
from app.mobile_work_log_contract import OfflineWorkLogCreateRequest


def payload():
    return {'client_mutation_id':'synthetic-command-1','device_id':'synthetic-device-1',
            'project_id':7,'operation':'work_log.create',
            'client_created_at':'2026-10-30T08:00:00+03:00',
            'data':{'work_date':'2026-10-30','work_name':'Synthetic work','qty':'1.250'}}


def test_explicit_payload_stays_identical_on_serialized_retry():
    original=payload()
    original['data']['labor']=[{'person_name':'Synthetic worker','hours':'2','hourly_rate':'100'}]
    model=OfflineWorkLogCreateRequest.model_validate(original)
    encoded=model.model_dump_json()
    assert OfflineWorkLogCreateRequest.model_validate_json(encoded).model_dump_json()==encoded
    assert model.data.work_date=='2026-10-30'
    assert model.client_created_at.utcoffset().total_seconds()==10800
    assert model.data.labor[0].person_name=='Synthetic worker'
    assert original['data']['qty']=='1.250'


@pytest.mark.parametrize('value',[None,'','20261030','2026-W44-5','2026-02-30','2026-1-03','2026-10-30T00:00:00'])
def test_invalid_date_rejected(value):
    body=payload();body['data']['work_date']=value
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


def test_omitted_date_rejected_without_changing_online_dto():
    body=payload();del body['data']['work_date']
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)
    assert WorkLogBody(work_name='Existing online behavior').work_date is None


@pytest.mark.parametrize('value',[0,-1,True,False,'7',7.0,None])
def test_project_id_is_strict_positive_integer(value):
    body=payload();body['project_id']=value
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


@pytest.mark.parametrize('field',['user_id','author_user_id','organization_id','entity_id'])
@pytest.mark.parametrize('location',['envelope','data'])
def test_identity_and_server_id_not_accepted(field,location):
    body=payload();target=body if location=='envelope' else body['data'];target[field]=99
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


@pytest.mark.parametrize('field,value',[('client_created_at','2026-10-30T08:00:00'),
    ('client_mutation_id','short'),('device_id','spaces not valid'),
    ('operation','task.update')])
def test_invalid_command_metadata_rejected(field,value):
    body=payload();body[field]=value
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)
