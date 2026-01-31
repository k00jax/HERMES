from app.net.sanitize import extract_text


def test_sanitize_removes_scripts_and_styles() -> None:
    html = """
    <html>
      <head>
        <style>body {background: red;}</style>
        <script>alert('x');</script>
      </head>
      <body>
        <p>Hello world.</p>
      </body>
    </html>
    """
    text = extract_text(html)
    assert "alert" not in text
    assert "background" not in text
    assert "Hello world." in text
