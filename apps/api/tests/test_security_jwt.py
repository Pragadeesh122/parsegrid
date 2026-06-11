import time
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import verify_jwt


def _token(claims: dict, secret: str | None = None, alg: str = "HS256") -> str:
    return pyjwt.encode(claims, secret or settings.auth_secret, algorithm=alg)


def _creds(token: str):
    return SimpleNamespace(credentials=token)


def _valid_claims() -> dict:
    return {"sub": "user-1", "exp": int(time.time()) + 300}


def test_valid_token_returns_payload():
    payload = verify_jwt(_creds(_token(_valid_claims())))
    assert payload.sub == "user-1"


def test_expired_token_rejected():
    claims = {"sub": "user-1", "exp": int(time.time()) - 10}
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token(claims)))
    assert exc.value.status_code == 401


def test_wrong_secret_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token(_valid_claims(), secret="x" * 40)))
    assert exc.value.status_code == 401


def test_token_without_exp_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token({"sub": "user-1"})))
    assert exc.value.status_code == 401


def test_token_without_sub_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token({"exp": int(time.time()) + 300})))
    assert exc.value.status_code == 401


def test_token_with_empty_sub_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_jwt(_creds(_token({"sub": "", "exp": int(time.time()) + 300})))
    assert exc.value.status_code == 401
