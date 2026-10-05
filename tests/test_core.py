from app.main import platform,Settings
def test_platform_detection():
    assert platform("https://youtube.com/watch?v=1")=="YouTube"
    assert platform("https://example.com/x") is None
def test_admin_parsing():assert Settings(admin_ids="1, 2").admins=={1,2}
