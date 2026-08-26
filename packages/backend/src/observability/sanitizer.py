"""日志敏感信息脱敏"""

from urllib.parse import urlparse, urlunparse

# 敏感键名模式：键名命中即整值打码。日志管线（logging.mask_sensitive_keys）
# 与手动脱敏（sanitize_value）共用此单一真相源
SENSITIVE_KEY_PATTERNS: tuple[str, ...] = ("password", "secret", "api_key", "token", "authorization")


def is_sensitive_key(key: str) -> bool:
    """判断字段名是否命中敏感模式"""
    return any(pattern in key.lower() for pattern in SENSITIVE_KEY_PATTERNS)


def sanitize_url(url: str) -> str:
    """脱敏 URL 中的密码

    redis://:password@host:port → redis://***@host:port
    postgresql://user:password@host:port → postgresql://user:***@host:port
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.password:
            # 替换密码为 ***
            netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return url


def sanitize_value(key: str, value: object) -> str:
    """根据字段名判断是否需要脱敏"""
    if is_sensitive_key(key):
        return "***"
    if isinstance(value, str) and ("redis://" in value or "postgresql://" in value):
        return sanitize_url(value)
    return str(value)
