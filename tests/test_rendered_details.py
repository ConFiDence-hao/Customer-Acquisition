import pytest
from app import collectors


def test_bjx_static_challenge_uses_rendered_public_article():
    class Client:
        def get(self,url):return '<div>CF_APP_WAF aliyunCaptcha 人机验证</div>'
        def get_rendered(self,url):
            return '<h1>垃圾焚烧烟气改造公开招标公告</h1><div id="article">'+('公开项目说明及采购要求。'*20)+'</div><p>发布时间：2026-10-14 10:23</p>'
    raw={'url':'https://huanbao.bjx.com.cn/news/20261014/1516189.shtml'}
    body,title,date=collectors.read_public_detail(Client(),raw,{'id':'bjx'})
    assert len(body)>120 and date=='2026-10-14 10:23'
    assert raw['acquisition_method']=='browser_rendered'


def test_rendered_article_does_not_reject_inert_verification_template():
    text='<h1>公开公告</h1><div id="article">'+('公开项目文字。'*30)+'</div><script>const x="aliyunCaptcha";</script>'
    assert len(collectors.clean_text(text,'bjx',rendered=True)[0])>120


def test_image_article_records_ocr_provenance(monkeypatch):
    class Client:
        def get(self,url):return '<h1>三峰垃圾焚烧项目公告</h1><div class="content-STRO"><img src="/image.jpg"></div><p>发布时间：2019-10-22</p>'
    from app import image_ocr
    monkeypatch.setattr(image_ocr,'extract_image_article',lambda *args:('【图片OCR识别正文】'+('项目原图识别内容。'*30),['https://www.cseg.cn/image.jpg']))
    raw={'url':'https://www.cseg.cn/index.php?a=shows&catid=15&id=301'}
    body,title,date=collectors.read_public_detail(Client(),raw,{'id':'cseg'})
    assert raw['acquisition_method']=='image_ocr' and raw['image_urls']==['https://www.cseg.cn/image.jpg']
    assert body.startswith('【图片OCR') and date=='2019-10-22'


def test_visible_browser_challenge_is_not_accepted():
    class Client:
        def get(self,url):return 'CF_APP_WAF'
        def get_rendered(self,url):raise collectors.SourceUnavailable('浏览器页面要求访问验证')
    with pytest.raises(collectors.SourceUnavailable,match='访问验证'):
        collectors.read_public_detail(Client(),{'url':'https://huanbao.bjx.com.cn/news/20261014/1516189.shtml'},{'id':'gfcl'})


def test_bjx_uses_the_public_headline_timestamp_not_dates_inside_body():
    html='<div class="cc-headline"><h1>公开公告</h1><p><span>2026-10-14 10:29</span><span>来源：北极星</span></p></div><div class="cc-article">'+('合同日期2099-12-01。'*20)+'</div>'
    assert collectors.clean_text(html,'bjx',rendered=True)[2]=='2026-10-14 10:29'


def test_bjx_keeps_short_public_text_and_never_downloads_images(monkeypatch):
    from app import image_ocr
    monkeypatch.setattr(image_ocr,'extract_image_article',lambda *a:pytest.fail('BJX should keep text without image OCR'))
    class Client:
        def get(self,url):return 'CF_APP_WAF'
        def get_rendered(self,url):return '<div class="cc-headline"><h1>垃圾焚烧飞灰处置中标公告</h1><p><span>2026-10-14 10:29</span></p></div><div class="cc-article"><p>中标单价1300元/吨，处置量约3700吨。</p><img src="https://img01.mybjx.net/news/UploadFile/202610/test.png"></div>'
        def get_bytes(self,url):pytest.fail('No image download permitted')
    raw={'url':'https://huanbao.bjx.com.cn/news/20261014/1516189.shtml'}
    body,title,date=collectors.read_public_detail(Client(),raw,{'id':'bjx'})
    assert body=='中标单价1300元/吨，处置量约3700吨。'
    assert date=='2026-10-14 10:29' and raw['content_coverage']=='partial'
    assert raw['detail_status']=='正文不完整'
    assert '图片' in raw['extraction_warning']


def test_bjx_image_only_article_keeps_list_metadata_without_failed_ocr():
    class Client:
        def get(self,url):return '<h1>垃圾焚烧项目采购公告</h1><div class="cc-article"><img src="https://img01.mybjx.net/news/UploadFile/202610/test.png"></div>'
        def get_bytes(self,url):pytest.fail('No image download')
    raw={'url':'https://huanbao.bjx.com.cn/news/20261014/1516189.shtml','title':'列表原标题','published_at':'2026-10-14'}
    body,title,date=collectors.read_public_detail(Client(),raw,{'id':'gfcl'})
    assert body=='' and raw['content_coverage']=='list_only'
    assert raw['title']=='列表原标题' and raw['published_at']=='2026-10-14'
