from cryptography.fernet import Fernet

from gitlab_notifier.security.crypto import TokenCipher


def test_roundtrip():
    c = TokenCipher(Fernet.generate_key())
    ct = c.encrypt("glpat-xxx")
    assert ct != b"glpat-xxx"
    assert c.decrypt(ct) == "glpat-xxx"
