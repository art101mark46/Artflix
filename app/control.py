"""Accidental-edit locks and short-lived change approvals (not user authentication)."""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

COMMIT_LOCK = threading.RLock()


class EditSessions:
    def __init__(self):
        self._tokens = set()
        self._lock = threading.Lock()

    def unlock(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens.add(token)
        return token

    def valid(self, token):
        with self._lock:
            return bool(token) and token in self._tokens

    def lock(self, token):
        with self._lock:
            self._tokens.discard(token)


class ReviewSigner:
    def __init__(self):
        self.secret = secrets.token_bytes(32)

    def sign(self, fingerprint, lifetime=900):
        payload = json.dumps({'fingerprint': fingerprint, 'expires': int(time.time()) + lifetime}, sort_keys=True).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip('=')
        mac = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + '.' + mac

    def valid(self, token, fingerprint):
        try:
            encoded, mac = token.split('.')
            expected = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, mac):
                return False
            payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            return payload['fingerprint'] == fingerprint and payload['expires'] >= time.time()
        except (ValueError, TypeError, KeyError):
            return False


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
