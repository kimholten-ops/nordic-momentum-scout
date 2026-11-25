from src.notify import les_abonnementer

A = '{"endpoint": "https://a", "keys": {"p256dh": "x", "auth": "y"}}'
B = '{"endpoint": "https://b", "keys": {"p256dh": "x", "auth": "y"}}'


def test_les_abonnementer_formater():
    assert [a["endpoint"] for a in les_abonnementer(A)] == ["https://a"]
    assert [a["endpoint"] for a in les_abonnementer(f"[{A}, {B}]")] == ["https://a", "https://b"]
    assert [a["endpoint"] for a in les_abonnementer(f"{A}\n{B}\n")] == ["https://a", "https://b"]
    assert [a["endpoint"] for a in les_abonnementer(f"{A}\n{A}")] == ["https://a"]
    assert les_abonnementer("") == []
    assert [a["endpoint"] for a in les_abonnementer(f"{A}\nsøppel")] == ["https://a"]
