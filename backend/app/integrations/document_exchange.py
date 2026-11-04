"""Provider-neutral local mock contract, NOT a 1C/Diadok wire/API schema."""
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExchangeDocument:
    project_id: int
    external_id: str
    revision: str
    content: bytes
    synthetic: bool = True

    def __post_init__(self):
        if type(self.project_id) is not int or self.project_id <= 0:
            raise ValueError('Invalid project scope')
        for value in (self.external_id,self.revision):
            if not isinstance(value,str) or not value.strip() or value != value.strip() or len(value)>160:
                raise ValueError('Invalid external identity/revision')
        if not isinstance(self.content,bytes) or not 0 < len(self.content) <= 1024*1024:
            raise ValueError('Invalid or excessive document bytes')
        if self.synthetic is not True:raise ValueError('Mock accepts explicitly synthetic documents only')

    @property
    def content_sha256(self):return sha256(self.content).hexdigest()


@dataclass(frozen=True)
class ExchangeCursor:
    provider: str
    project_id: int
    after: tuple[str,str]


@dataclass(frozen=True)
class ExchangePage:
    documents: tuple[ExchangeDocument,...]
    next_cursor: ExchangeCursor | None


@runtime_checkable
class DocumentExchange(Protocol):
    provider: str
    def list_documents(self,project_id: int,*,limit: int,cursor: ExchangeCursor | None=None) -> ExchangePage: ...
    def get_document(self,project_id: int,external_id: str,revision: str) -> ExchangeDocument: ...


class MockDocumentExchange:
    """In-memory only. No URLs, credentials, HTTP, OAuth or legal signing."""
    def __init__(self,provider: str):
        if provider not in {'one_c','diadok'}:raise ValueError('Unknown mock provider')
        self.provider=provider;self._documents={}

    def accept_delivery(self,document: ExchangeDocument) -> bool:
        if not isinstance(document,ExchangeDocument):raise ValueError('Typed synthetic document required')
        key=(document.project_id,document.external_id,document.revision)
        previous=self._documents.get(key)
        if previous is not None:
            if previous!=document:raise ValueError('Same revision delivered with conflicting bytes')
            return False
        self._documents[key]=document;return True

    def list_documents(self,project_id: int,*,limit: int,cursor: ExchangeCursor | None=None) -> ExchangePage:
        if type(project_id) is not int or project_id<=0 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Invalid scope or page budget')
        if cursor is not None and (not isinstance(cursor,ExchangeCursor) or cursor.provider!=self.provider or cursor.project_id!=project_id):
            raise ValueError('Cursor belongs to another provider/project')
        keys=sorted(k for k in self._documents if k[0]==project_id and (cursor is None or k[1:]>cursor.after))
        selected=keys[:limit]
        next_cursor=ExchangeCursor(self.provider,project_id,selected[-1][1:]) if len(keys)>limit else None
        return ExchangePage(tuple(self._documents[k] for k in selected),next_cursor)

    def get_document(self,project_id: int,external_id: str,revision: str) -> ExchangeDocument:
        if type(project_id) is not int or project_id<=0:raise ValueError('Invalid project scope')
        try:return self._documents[(project_id,external_id,revision)]
        except KeyError as exc:raise LookupError('Document not found in project') from exc
