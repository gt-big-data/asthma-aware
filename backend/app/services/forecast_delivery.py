"""Cloud CDN URL signing for explicitly validated environmental product objects.

No service-account key files or cloud signing call required. Key comes from an
existing Secret Manager secret mounted into the API environment at deployment.
"""
import base64
import hashlib
import hmac
import re
from datetime import timedelta
from urllib.parse import urlsplit


class DeliveryUnavailable(ValueError):
    pass


def sign_delivery_url(settings, name, *, now, valid_until):
    if now.tzinfo is None or valid_until.tzinfo is None:
        raise DeliveryUnavailable("Delivery timestamps must be timezone-aware")
    base = settings.forecast_delivery_base_url.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise DeliveryUnavailable("Configure an HTTPS CDN origin")
    # Keep the exact object path and query under our control. No URL prefixes,
    # traversal, query injection, percent-encoded separators or arbitrary hosts.
    if (not isinstance(name, str) or not name.startswith("processed/")
            or not re.fullmatch(r"[A-Za-z0-9_./-]{1,1024}", name)
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise DeliveryUnavailable("Invalid processed object name")
    key_name = settings.forecast_cdn_key_name
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", key_name):
        raise DeliveryUnavailable("Configure a CDN signing key name")
    try:
        key = base64.b64decode(settings.forecast_cdn_signing_key, altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise DeliveryUnavailable("Configure a valid CDN signing key") from None
    if len(key) != 16:
        raise DeliveryUnavailable("Cloud CDN signing keys must be 16 bytes")
    expires = min(now + timedelta(minutes=5), valid_until)
    if now.tzinfo is None or expires <= now:
        raise DeliveryUnavailable("The processed product has expired")
    unsigned = f"{base}/{name}?Expires={int(expires.timestamp())}&KeyName={key_name}"
    signature = base64.urlsafe_b64encode(hmac.new(key, unsigned.encode(), hashlib.sha1).digest()).decode()
    return {"url": unsigned + "&Signature=" + signature,
            "expires_at": expires.isoformat().replace("+00:00", "Z")}
