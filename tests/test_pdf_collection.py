import httpx
import pytest
from app import collectors


def test_public_pdf_has_its_own_bounded_download_size(monkeypatch):
    monkeypatch.setattr(collectors.time,'sleep',lambda _:None)
    client=collectors.PublicClient();client.client.close()
    data=b'%PDF-1.7\n'+b'x'*4_100_000
    client.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(404) if r.url.path=='/robots.txt' else httpx.Response(200,content=data)))
    url='https://ecp.capitaleco-pro.com/scdzzb/cgUploadController.do?id=public'
    try:
        with pytest.raises(collectors.SourceUnavailable,match='4MB'):client._request(url)
        assert client.get_pdf_bytes(url)==data
    finally:client.close()
