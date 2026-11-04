import pytest
from app.integrations.document_exchange import DocumentExchange, ExchangeDocument, MockDocumentExchange


@pytest.fixture(params=['one_c','diadok'])
def adapter(request):return MockDocumentExchange(request.param)


def doc(project=1,identity='SYNTHETIC-1',revision='1',content=b'SYNTHETIC sample; not vendor wire format'):
    return ExchangeDocument(project,identity,revision,content)


def test_repeat_conflict_and_revision_history(adapter):
    assert isinstance(adapter,DocumentExchange)
    assert adapter.accept_delivery(doc()) is True
    assert adapter.accept_delivery(doc()) is False
    with pytest.raises(ValueError,match='conflicting'):adapter.accept_delivery(doc(content=b'SYNTHETIC different'))
    assert adapter.accept_delivery(doc(revision='2')) is True
    assert len(adapter.list_documents(1,limit=100).documents)==2
    assert adapter.get_document(1,'SYNTHETIC-1','1')==doc()


def test_tenant_isolation_and_cursor_scope(adapter):
    adapter.accept_delivery(doc());adapter.accept_delivery(doc(identity='SYNTHETIC-2'))
    page=adapter.list_documents(1,limit=1);assert page.next_cursor is not None
    assert adapter.list_documents(1,limit=1,cursor=page.next_cursor).documents[0].external_id=='SYNTHETIC-2'
    assert adapter.list_documents(2,limit=10).documents==()
    with pytest.raises(LookupError):adapter.get_document(2,'SYNTHETIC-1','1')
    with pytest.raises(ValueError):adapter.list_documents(2,limit=1,cursor=page.next_cursor)
    other=MockDocumentExchange('diadok' if adapter.provider=='one_c' else 'one_c')
    with pytest.raises(ValueError):other.list_documents(1,limit=1,cursor=page.next_cursor)


@pytest.mark.parametrize('limit',[0,101,True,1.5])
def test_page_budget_fail_closed(adapter,limit):
    with pytest.raises(ValueError):adapter.list_documents(1,limit=limit)


def test_mock_refuses_unmarked_real_data():
    with pytest.raises(ValueError,match='synthetic'):ExchangeDocument(1,'x','1',b'not marked',synthetic=False)
    with pytest.raises(ValueError):ExchangeDocument(1,'x','1',b'x'*(1024*1024+1))
