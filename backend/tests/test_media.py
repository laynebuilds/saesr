"""app.media.upload against a mocked Supabase Storage (never the real one)."""
import httpx
import pytest

from app import media


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "abc123.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    return p


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co/")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sk-test")


def test_not_configured_returns_local(monkeypatch, clip):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)

    def boom(request):
        raise AssertionError("must not call the network")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    assert media.upload(clip, "clips", client=client) == "/clips/abc123.mp4"


def test_upload_success(configured, clip):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = request.content
        return httpx.Response(200, json={"Key": "clips/site-demo/abc123.mp4"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    url = media.upload(clip, "clips", client=client)
    assert url == "https://proj.supabase.co/storage/v1/object/public/clips/site-demo/abc123.mp4"
    assert seen["url"] == "https://proj.supabase.co/storage/v1/object/clips/site-demo/abc123.mp4"
    h = seen["headers"]
    assert h["apikey"] == "sk-test"
    assert h["authorization"] == "Bearer sk-test"
    assert h["content-type"] == "video/mp4"
    assert h["x-upsert"] == "true"
    assert seen["body"] == clip.read_bytes()


def test_keyframe_content_type(configured, tmp_path):
    jpg = tmp_path / "abc_k1.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")
    seen = {}

    def handler(request):
        seen["ct"] = request.headers["content-type"]
        seen["url"] = str(request.url)
        return httpx.Response(200)

    url = media.upload(jpg, "keyframes", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["ct"] == "image/jpeg"
    assert seen["url"].endswith("/storage/v1/object/keyframes/site-demo/abc_k1.jpg")
    assert url.endswith("/storage/v1/object/public/keyframes/site-demo/abc_k1.jpg")


def test_http_error_falls_back(configured, clip):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "Bucket not found"})))
    assert media.upload(clip, "clips", client=client) == "/clips/abc123.mp4"


def test_network_error_falls_back(configured, clip):
    def handler(request):
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert media.upload(clip, "clips", client=client) == "/clips/abc123.mp4"


def test_missing_file_falls_back(configured, tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert media.upload(tmp_path / "nope.jpg", "keyframes", client=client) == "/clips/nope.jpg"


def test_unknown_bucket_falls_back(configured, clip):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert media.upload(clip, "secrets", client=client) == "/clips/abc123.mp4"
