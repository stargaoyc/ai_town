"""日志敏感信息脱敏"""

from urllib.parse import urlparse, urlunparse


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


def sanitize_value(key: str, value) -> str:
    """根据字段名判断是否需要脱敏"""
    sensitive_keys = {"password", "secret", "api_key", "token", "authorization"}
    if any(s in key.lower() for s in sensitive_keys):
        return "***"
    if isinstance(value, str) and ("redis://" in value or "postgresql://" in value):
        return sanitize_url(value)
    return str(value)
