"""Company directory import, payload cache invalidation and refresh memo."""
import base64
import csv
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import companies, db, main
from app.analysis import analyze


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setenv('RADAR_PASSWORD', 'testing-only-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-32-characters-long')
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    main.FAILURES.clear()
    with TestClient(main.app) as c:
        assert c.post('/api/login', json={'username': 'admin', 'password': 'testing-only-password'}).status_code == 200
        yield c


def payload(name, data):
    return {'filename': name, 'content_base64': base64.b64encode(data).decode()}


def csv_bytes(rows, encoding='utf-8-sig'):
    buffer = io.StringIO();csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode(encoding)


def xlsx_bytes(rows):
    shared = sorted({cell for row in rows for cell in row})
    sheet = ''.join(f'<row r="{n}">' + ''.join(f'<c r="{chr(65+i)}{n}" t="s"><v>{shared.index(v)}</v></c>' for i, v in enumerate(row)) + '</row>' for n, row in enumerate(rows, 1))
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as book:
        book.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}"><sheets><sheet name="名录" sheetId="1"/></sheets></workbook>')
        book.writestr('xl/sharedStrings.xml', f'<sst xmlns="{ns}">' + ''.join(f'<si><t>{s}</t></si>' for s in shared) + '</sst>')
        book.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{ns}"><sheetData>{sheet}</sheetData></worksheet>')
    return buffer.getvalue()


def notice():
    db.upsert(analyze({'title': '中节能（石家庄）环保能源有限公司烟气在线监测系统采购公告', 'url': 'https://example.com/company',
                       'source_id': 'cecep', 'source_name': '来源', 'source_kind': '集团采购', 'content': '采购烟气在线监测系统'},
                      [], db.DEFAULT_SETTINGS))


def test_csv_import_replaces_directory_and_rescores_notices(client):
    notice()
    rows = [['序号', '企业名称', '所属集团', '省份', '城市'], ['1', '中节能（石家庄）环保能源有限公司', '中节能', '河北省', '石家庄'],
            ['2', '某某再生能源有限公司', '某集团', '广东省', '广州'], ['3', '', '', '', '']]
    result = client.post('/api/companies/import', json=payload('名录.csv', csv_bytes(rows, 'gb18030'))).json()
    assert result['imported'] == 2 and result['total'] == 2 and result['matched_notices'] == 1
    assert client.get('/api/companies?province=河北').json()['total'] == 1
    item = client.get('/api/opportunities').json()['items'][0]
    assert item['matched_companies'][0]['name'] == '中节能（石家庄）环保能源有限公司'
    again = client.post('/api/companies/import', json=payload('新.csv', csv_bytes(rows[:1] + rows[2:3]))).json()
    assert again['total'] == 1 and again['matched_notices'] == 0


def test_xlsx_import_and_validation(client):
    data = xlsx_bytes([['焚烧厂名称', '省份'], ['测试环保发电有限公司甲', '江苏']])
    assert client.post('/api/companies/import', json=payload('a.xlsx', data)).json()['imported'] == 1
    assert client.post('/api/companies/import', json=payload('a.xls', b'x')).status_code == 422
    assert client.post('/api/companies/import', json=payload('a.csv', csv_bytes([['省份'], ['江苏']]))).status_code == 422


def test_xlsx_scan_limit_rejects_before_replacing_directory(client, monkeypatch):
    notice()
    original = [['企业名称', '省份'], ['中节能（石家庄）环保能源有限公司', '河北']]
    imported = client.post('/api/companies/import', json=payload('原名录.csv', csv_bytes(original)))
    assert imported.status_code == 200 and imported.json()['matched_notices'] == 1
    before_companies = client.get('/api/companies').json()['items']
    before_notice = client.get('/api/opportunities').json()['items'][0]

    monkeypatch.setattr(companies, 'MAX_ROWS', 2)
    # Duplicate names keep the unique-company count valid even after the scan limit.
    rows = [['企业名称', '省份']] + [['重复环保能源有限公司', '江苏']] * 22
    rows.append(['尾部环保能源有限公司', '广东'])
    result = client.post('/api/companies/import', json=payload('超限.xlsx', xlsx_bytes(rows)))

    assert result.status_code == 422
    assert '行数' in result.json()['detail']
    assert client.get('/api/companies').json()['items'] == before_companies
    after_notice = client.get(f"/api/opportunities/{before_notice['id']}").json()
    assert after_notice['matched_companies'] == before_notice['matched_companies']


def test_import_requires_admin(client):
    assert client.post('/api/users', json={'username': 'staff', 'display_name': '员工', 'password': 'staff-password-123', 'role': 'user'}).status_code == 200
    client.post('/api/logout')
    assert client.post('/api/login', json={'username': 'staff', 'password': 'staff-password-123'}).status_code == 200
    assert client.post('/api/companies/import', json=payload('a.csv', b'x')).status_code == 403


def test_payload_cache_follows_raw_updates(client):
    notice()
    first = client.get('/api/opportunities').json()['items'][0]
    with db.connect() as c:
        row = c.execute('SELECT payload FROM opportunities').fetchone()
        value = json.loads(row['payload']);value['buyer'] = '直接修改的采购人'
        c.execute('UPDATE opportunities SET payload=?', (db.dumps(value),))
    assert first['buyer'] != '直接修改的采购人'
    assert client.get(f"/api/opportunities/{first['id']}").json()['buyer'] == '直接修改的采购人'


def test_directory_aliases_and_excel_date_serials(client):
    rows = [['2017-2024 名录说明'], ['序号', '企业名称', '焚烧炉数', '投产日期', '隶属集团', '省份', '城市', '信息公开负责人', '联系电话'],
            ['1', '深圳广业环保再生能源有限公司', '3', '38626', '广业环保', '广东', '深圳市', '黄先生', '13800000000']]
    assert client.post('/api/companies/import', json=payload('名录.xlsx', xlsx_bytes(rows))).json()['imported'] == 1
    company = client.get('/api/companies').json()['items'][0]
    assert (company['group_name'], company['furnaces'], company['commissioned_at']) == ('广业环保', '3', '2005-10-01')
