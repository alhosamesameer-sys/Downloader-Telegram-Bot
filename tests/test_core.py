from app.main import Settings, platform, valid_url

def test_platform_detection():
    assert platform("https://youtube.com/watch?v=1") == "YouTube"
    assert platform("https://www.tiktok.com/@user/video/1") == "TikTok"
    assert platform("https://example.com/x") is None

def test_url_validation():
    assert valid_url("https://example.com/video")
    assert not valid_url("javascript:alert(1)")
    assert not valid_url("not-a-url")

def test_admin_parsing():
    assert Settings(admin_ids="1, 2").admins == {1, 2}
