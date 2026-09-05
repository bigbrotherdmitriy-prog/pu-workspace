"""Read-only documentation checks. No product imports, database or network."""
import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote
from uuid import UUID

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
INT_TYPES = {'organization', 'user', 'project', 'contract', 'message', 'task',
             'response_draft', 'background_job'}
UUID_TYPES = {'connection_identity', 'mail_connection', 'source', 'source_version',
              'evidence', 'deadline_claim', 'context_relation', 'action', 'policy',
              'approval', 'receipt', 'ledger_event'}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, 'duplicate JSON key'
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)


def canonical(value):
    def check(v):
        if isinstance(v, dict):
            assert all(k.isascii() for k in v)
            for item in v.values():
                check(item)
        elif isinstance(v, list):
            for item in v:
                check(item)
        elif isinstance(v, str):
            v.encode('utf-8', errors='strict')
        elif type(v) is int:
            assert abs(v) <= 2**53 - 1
        else:
            assert v is None or type(v) is bool, 'noncanonical scalar'
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def key(ref):
    assert set(ref) == {'namespace', 'type', 'tenant_id', 'id'}
    assert ref['namespace'] == 'pu'
    assert ref['type'] in INT_TYPES | UUID_TYPES
    assert ref['tenant_id'] == {'kind': 'int', 'value': '1'}, 'tenant mismatch'
    tagged = ref['id']
    assert set(tagged) == {'kind', 'value'}
    expected = 'int' if ref['type'] in INT_TYPES else 'uuid'
    assert tagged['kind'] == expected
    value = tagged['value']
    assert isinstance(value, str)
    if expected == 'int':
        assert re.fullmatch(r'[1-9][0-9]*', value)
        assert int(value) <= 2**63 - 1
    else:
        assert str(UUID(value)) == value
    if ref['type'] == 'organization':
        assert tagged == ref['tenant_id']
    return canonical(ref)


def validate(d):
    assert d['synthetic_only'] is True
    rows = d['records']
    index = {key(row['ref']): row for row in rows}
    assert len(index) == len(rows)
    def resolve(ref):
        return index[key(ref)]
    def walk(v):
        if isinstance(v, dict):
            if 'namespace' in v and 'tenant_id' in v:
                resolve(v)
            if set(v) == {'ref', 'version_kind', 'value'}:
                row = resolve(v['ref'])
                kind, version = v['version_kind'], v['value']
                assert kind in {'revision', 'record_version'}
                assert type(version) is int and version > 0
                versions = [row.get(kind)] + [x.get(kind) for x in row.get('versions', [])]
                assert version in versions, 'unresolved version'
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)
    walk(d)
    bytype = lambda t: [r for r in rows if r['ref']['type'] == t]
    assert len(bytype('source')) == 2
    mail = bytype('mail_connection')[0]
    for source in bytype('source'):
        assert source['identity_ref'] == mail['identity_ref']
        assert resolve(source['current_version_ref'])['source_ref'] == source['ref']
    evidence = bytype('evidence')[0]
    assert resolve(evidence['source_version']['ref'])['source_ref'] == evidence['source_ref']
    assert evidence['assessment']['verification'] == 'verified'
    claim = bytype('deadline_claim')[0]
    assert claim['verification'] == 'confirmed' and claim['evidence'][0]['ref'] == evidence['ref']
    relations = bytype('context_relation')
    assert len(relations) == 2 and all(r['state'] == 'confirmed' and r['applicability'] == 'current' for r in relations)
    project = bytype('project')[0]['ref']
    assert bytype('contract')[0]['project_ref'] == project
    assert {r['target_ref']['type'] for r in relations} == {'project', 'contract'}
    policy = bytype('policy')[0]
    assert policy['mode'] == 'CONFIRM' and not policy['auto_enabled'] and not policy['external_execute']
    actions = [r for r in bytype('action') if 'envelope' in r]
    assert len(actions) == 2
    for action in actions:
        env = action['envelope']
        assert sha(env) == action['envelope_sha256'], 'envelope hash mismatch'
        assert env['policy_sha256'] == sha(policy)
        assert env['action_ref'] == action['ref'] and env['revision'] == action['revision']
        assert env['autonomy'] == 'CONFIRM' and env['project_ref'] == project
        assert env['action_type'] in policy['allowed_action_types']
        assert all(p['ref'] == q['ref'] for p, q in zip(env['relations'], relations))
        approval = resolve(action['approval_ref'])
        assert approval['action']['ref'] == action['ref']
        assert approval['action']['value'] == action['revision']
        assert approval['envelope_sha256'] == action['envelope_sha256']
        assert approval['decision'] == 'GRANTED' and approval['approver_ref'] != env['requested_by']
        receipt = next(r for r in bytype('receipt') if r['action']['ref'] == action['ref'])
        assert receipt['envelope_sha256'] == action['envelope_sha256']
        assert receipt['approval_ref'] == approval['ref'] and receipt['outcome'] == 'APPLIED'
        assert approval['granted_at'] <= receipt['committed_at'] < approval['expires_at']
        events = [r for r in bytype('ledger_event') if r['subject']['ref'] == action['ref']]
        assert [e['type'] for e in events] == ['APPROVAL_GRANTED', 'DISPATCH_AUTHORIZED', 'ACTION_SUCCEEDED']
        assert events[-1]['receipt_ref'] == receipt['ref']
        assert events[-1]['transaction_id'] == events[-2]['transaction_id'] == receipt['transaction_id']
    assert actions[1]['envelope']['compensates_action_ref'] == actions[0]['ref']
    assert actions[0]['envelope']['payload']['due_date'] == claim['value']['due_date']
    assert bytype('task')[0]['versions'][-1]['status'] == 'cancelled'
    streams = {}
    for event in bytype('ledger_event'):
        stream = key(event['subject']['ref'])
        streams[stream] = streams.get(stream, 0) + 1
        assert event['sequence'] == streams[stream]
    assert not bytype('response_draft')[0]['sent']
    payload = bytype('background_job')[0]['payload']
    assert set(payload) == {'action_ref', 'action_revision', 'correlation_id'}
    return len(rows)


def markdown_links():
    count = 0
    paths = list(HERE.parent.rglob('*.md')) + [ROOT / 'docs/audits/v54-contract-integration.md']
    for path in paths:
        assert path.exists(), str(path)
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if re.match(r'[a-zA-Z]+:', link):
                continue
            target, _, anchor = unquote(link).partition('#')
            dest = (path.parent / target).resolve() if target else path
            assert dest.exists(), f'broken link: {path.name}: {link}'
            if anchor and dest.suffix == '.md':
                headings = re.findall(r'^#+\s+(.+)$', dest.read_text(encoding='utf-8'), re.M)
                slugs = [re.sub(r'[^\w\- ]', '', h.lower()).replace(' ', '-') for h in headings]
                assert anchor in slugs, f'broken anchor: {link}'
            count += 1
    return count


if __name__ == '__main__':
    d = read_json(HERE / 'pilot.json')
    count = validate(d)
    # Mutation tests validate the checker, not the product execution gate.
    mutations = [
        lambda x: x['records'][0]['ref']['tenant_id'].update(value='2'),
        lambda x: next(r for r in x['records'] if 'envelope' in r)['envelope']['payload'].update(due_date='2026-09-11'),
        lambda x: next(r for r in x['records'] if r['ref']['type'] == 'approval')['action'].update(value=2),
        lambda x: next(r for r in x['records'] if r['ref']['type'] == 'receipt').update(outcome='UNKNOWN'),
    ]
    for mutate in mutations:
        changed = copy.deepcopy(d)
        mutate(changed)
        try:
            validate(changed)
        except (AssertionError, KeyError):
            pass
        else:
            raise AssertionError('mutation escaped checker')
    for path in HERE.parent.rglob('*.json'):
        read_json(path)
    legacy = read_json(HERE.parent / 'action-trust/examples.json')
    assert all(sha(x['policy']) == x['policy_sha256'] for x in legacy['policies'])
    assert all(sha(x['envelope']) == x['envelope_sha256'] for x in legacy['sealed_actions'])
    print(f'DOCUMENT_CHECKS_PASS records={count} actions=2 mutation_checks=4 local_links={markdown_links()} legacy_hashes=8')
