"""Administrator import of the incinerator company directory (CSV, XLSX or JSON).

The directory is business data kept out of the repository. Importing replaces the whole
directory and re-scores stored notices so exact company-name matches take effect at once.
"""
import base64
import csv
import io
import json
import re
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import accounts, db
from .analysis import PROVINCES, analyze

router = APIRouter(prefix='/api/companies')
MAX_BYTES = 10_000_000
MAX_ROWS = 20_000
FIELDS = ('name', 'group_name', 'province', 'city', 'address', 'furnaces', 'commissioned_at', 'contact_name', 'contact_phone')
ALIASES = {
    'name': ('企业名称', '公司名称', '项目公司', '项目公司名称', '焚烧厂名称', '电厂名称', '厂名', '项目名称', '名称', 'name'),
    'group_name': ('所属集团', '隶属集团', '集团', '集团名称', '投资方', '母公司', 'group_name', 'group'),
    'province': ('省份', '所在省份', '省', '省区', 'province'),
    'city': ('城市', '地市', '所在城市', '市', 'city'),
    'address': ('地址', '详细地址', '厂址', 'address'),
    'furnaces': ('焚烧炉', '焚烧炉数', '炉型', '炉数', '规模', '处理规模', '焚烧规模', 'furnaces'),
    'commissioned_at': ('投运时间', '投产时间', '投运日期', '投产日期', '运营时间', 'commissioned_at'),
    'contact_name': ('联系人', '信息公开负责人', '负责人', 'contact_name'),
    'contact_phone': ('联系电话', '电话', '手机', 'contact_phone'),
}
XLSX = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


class DirectoryFile(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=14_000_000)


def _column(ref):
    letters = re.match(r'[A-Z]+', ref).group()
    index = 0
    for letter in letters:index = index * 26 + ord(letter) - 64
    return index - 1


def read_xlsx(data):
    """First worksheet as rows of text; standard library only."""
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        names = set(book.namelist())
        shared = []
        if 'xl/sharedStrings.xml' in names:
            for item in ElementTree.fromstring(book.read('xl/sharedStrings.xml')).iter(XLSX + 'si'):
                shared.append(''.join(t.text or '' for t in item.iter(XLSX + 't')))
        sheet = 'xl/worksheets/sheet1.xml'
        workbook = ElementTree.fromstring(book.read('xl/workbook.xml'))
        first = workbook.find(f'{XLSX}sheets/{XLSX}sheet')
        if first is not None and 'xl/_rels/workbook.xml.rels' in names:
            rid = first.get(REL + 'id')
            for rel in ElementTree.fromstring(book.read('xl/_rels/workbook.xml.rels')):
                if rel.get('Id') == rid:
                    target = rel.get('Target').lstrip('/')
                    sheet = target if target.startswith('xl/') else 'xl/' + target
        rows = []
        for row in ElementTree.fromstring(book.read(sheet)).iter(XLSX + 'row'):
            values = {}
            for cell in row.iter(XLSX + 'c'):
                kind = cell.get('t')
                if kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.iter(XLSX + 't'))
                else:
                    raw = cell.find(XLSX + 'v')
                    text = raw.text if raw is not None and raw.text is not None else ''
                    if kind == 's' and text:text = shared[int(text)]
                    elif re.fullmatch(r'-?\d+\.0', text):text = text[:-2]
                values[_column(cell.get('r'))] = text.strip()
            if values:rows.append([values.get(i, '') for i in range(max(values) + 1)])
            if len(rows) > MAX_ROWS + 20:
                raise HTTPException(422, f'XLSX行数超过读取上限（{MAX_ROWS + 20}行），未导入；请删除多余行后重试')
        return rows


def read_csv(data):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:text = data.decode(encoding);break
        except UnicodeDecodeError:continue
    else:raise HTTPException(422, '无法识别CSV编码，请另存为UTF-8')
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]


def table_records(rows):
    """Locate the header row within the first rows and map known column names."""
    for index, header in enumerate(rows[:10]):
        mapping = {}
        for position, label in enumerate(header):
            key = re.sub(r'\s+', '', label)
            for field, names in ALIASES.items():
                if field not in mapping and key in names:mapping[field] = position;break
        if 'name' in mapping:
            return [{field: (row[position] if position < len(row) else '') for field, position in mapping.items()} for row in rows[index + 1:]]
    raise HTTPException(422, '未找到“企业名称”列；请确认表头包含企业名称、省份等列')


def normalize(record, row_number):
    name = re.sub(r'\s+', '', str(record.get('name') or ''))
    if not name:return None
    value = {field: str(record.get(field) or '').strip()[:500] for field in FIELDS}
    value['name'] = name[:200]
    # Excel stores dates as day serials (e.g. 38626 = 2005-10-01) when the cell is not text.
    if re.fullmatch(r'\d{5}', value['commissioned_at']) and 20000 <= int(value['commissioned_at']) <= 80000:
        value['commissioned_at'] = (date(1899, 12, 30) + timedelta(days=int(value['commissioned_at']))).isoformat()
    province = value['province']
    value['province'] = next((p for p in PROVINCES if province.startswith(p)), province)
    value['source_row'] = row_number
    return value


def parse(filename, data):
    suffix = filename.lower().rsplit('.', 1)[-1]
    if suffix == 'xlsx':
        try:records = table_records(read_xlsx(data))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):raise HTTPException(422, 'XLSX文件无法读取，请确认不是加密或旧版xls格式') from None
    elif suffix == 'csv':records = table_records(read_csv(data))
    elif suffix == 'json':
        try:records = json.loads(data.decode('utf-8-sig'))
        except (UnicodeDecodeError, ValueError):raise HTTPException(422, 'JSON文件格式不正确') from None
        if not isinstance(records, list) or not all(isinstance(x, dict) for x in records):raise HTTPException(422, 'JSON需为企业对象数组')
    else:raise HTTPException(422, '仅支持 .xlsx、.csv 或 .json 文件（旧版.xls请先另存为.xlsx）')
    result, seen = [], set()
    for number, record in enumerate(records, start=1):
        value = normalize(record, number)
        if value and value['name'] not in seen:
            seen.add(value['name']);result.append(value)
    if not result:raise HTTPException(422, '文件中没有可导入的企业名称')
    if len(result) > MAX_ROWS:raise HTTPException(422, f'单次最多导入{MAX_ROWS}家企业')
    return result


def rescore():
    """Re-run rule analysis so name matches, buyer and province inference use the new directory."""
    companies = db.all_companies();settings = db.settings()
    with db.connect() as c:rows = c.execute('SELECT payload,content_hash FROM opportunities').fetchall()
    for row in rows:db.upsert(analyze(json.loads(row['payload']), companies, settings), expected_hash=row['content_hash'])
    db.reconcile_lifecycle()


@router.get('/summary')
def summary(user=Depends(accounts.require_user)):
    with db.connect() as c:
        row = c.execute('SELECT COUNT(*) AS total,COUNT(DISTINCT province) AS provinces,MAX(source_file) AS source_file FROM companies').fetchone()
        matched = c.execute("SELECT COUNT(*) FROM opportunities WHERE json_array_length(COALESCE(json_extract(payload,'$.matched_companies'),'[]'))>0").fetchone()[0]
    return {'total': row['total'], 'provinces': row['provinces'], 'source_file': row['source_file'] or '', 'matched_notices': matched}


@router.post('/import')
def import_directory(body: DirectoryFile, user=Depends(accounts.require_admin)):
    try:data = base64.b64decode(body.content_base64, validate=True)
    except ValueError:raise HTTPException(422, '文件内容编码不正确') from None
    if len(data) > MAX_BYTES:raise HTTPException(413, '文件超过10MB')
    records = parse(body.filename, data)
    from .main import RUN_LOCK
    if not RUN_LOCK.acquire(blocking=False):raise HTTPException(409, '采集或分析正在运行，请完成后再导入名录')
    try:
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM companies')
            columns = (*FIELDS, 'source_row', 'source_file')
            c.executemany(f"INSERT INTO companies ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                          [tuple(r[k] for k in columns[:-1]) + (body.filename[:200],) for r in records])
        rescore()
        db.audit('导入企业名录', body.filename[:200], str(len(records)))
    finally:RUN_LOCK.release()
    return {'imported': len(records), **summary(user)}
