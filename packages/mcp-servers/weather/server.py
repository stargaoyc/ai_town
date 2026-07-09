"""MCP Weather Server - OpenWeatherMap 集成

为 AI Town 角色提供"查询天气"的工具能力。
角色可调用此 MCP Server 获取实时天气与多日预报。

设计：
- 集成 OpenWeatherMap Current Weather & Forecast API
- 通过环境变量 OPENWEATHER_API_KEY 配置 API Key
- 使用 httpx.AsyncClient 异步请求
- API Key 缺失时优雅降级（返回错误字典，不崩溃）

注意：本包为社区插件骨架，需配置有效 API Key 才能完整运行。

启动方式：
    export OPENWEATHER_API_KEY=your_api_key
    uv run python server.py
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP  # FastMCP 2.0+ 导入方式

logger = structlog.get_logger()

mcp = FastMCP("weather")

OWM_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OWM_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
HTTP_TIMEOUT = 30.0
MAX_FORECAST_DAYS = 5
SECONDS_PER_DAY = 86400


def _get_api_key() -> str | None:
    """从环境变量读取 OpenWeatherMap API Key"""
    return os.environ.get("OPENWEATHER_API_KEY")


def _missing_key_error(action: str) -> dict[str, Any]:
    """构造 API Key 缺失错误返回"""
    return {
        "success": False,
        "error": "API key not configured",
        "message": "Set OPENWEATHER_API_KEY environment variable to use weather service",
        "action": action,
    }


def _parse_current(data: dict[str, Any], units: str) -> dict[str, Any]:
    """解析 OpenWeatherMap Current Weather 响应为统一结构"""
    main = data.get("main", {}) or {}
    wind = data.get("wind", {}) or {}
    weather_list = data.get("weather", []) or []
    weather = weather_list[0] if weather_list else {}

    return {
        "success": True,
        "city": data.get("name", ""),
        "temperature": main.get("temp"),
        "feels_like": main.get("feels_like"),
        "humidity": main.get("humidity"),
        "wind_speed": wind.get("speed"),
        "description": weather.get("description", ""),
        "icon": weather.get("icon", ""),
        "units": units,
        "error": None,
    }


@mcp.tool()
async def get_current_weather(city: str, units: str = "metric") -> dict:
    """查询城市当前天气

    使用 OpenWeatherMap Current Weather API。

    Args:
        city: 城市名（如 "London"、"Beijing"、"Tokyo"）
        units: 温度单位（metric=摄氏, imperial=华氏, standard=开尔文）

    Returns:
        {
            "success": bool,
            "city": str,
            "temperature": float,
            "feels_like": float,
            "humidity": int,
            "wind_speed": float,
            "description": str,
            "icon": str,
            "units": str,
            "error": str | None,
        }
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("get_current_weather_skipped_no_api_key", city=city)
        return _missing_key_error("get_current_weather")

    params = {
        "q": city,
        "appid": api_key,
        "units": units,
    }
    logger.info("get_current_weather_requested", city=city, units=units)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(OWM_CURRENT_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "get_current_weather_http_error",
            status=e.response.status_code,
            error=str(e),
        )
        return {
            "success": False,
            "city": city,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except httpx.HTTPError as e:
        logger.error("get_current_weather_request_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "city": city,
            "error": f"Request failed: {e}",
        }
    except Exception as e:
        logger.error("get_current_weather_unexpected_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "city": city,
            "error": f"Unexpected error: {e}",
        }

    logger.info("get_current_weather_done", city=city)
    return _parse_current(data, units)


@mcp.tool()
async def get_forecast(city: str, days: int = 5, units: str = "metric") -> dict:
    """查询城市天气预报（5 天 / 3 小时间隔）

    使用 OpenWeatherMap 5-day/3-hour Forecast API，并按 days 截断。

    Args:
        city: 城市名
        days: 预报天数（1-5，默认 5）
        units: 温度单位（metric=摄氏, imperial=华氏, standard=开尔文）

    Returns:
        {
            "success": bool,
            "city": str,
            "forecasts": [{"datetime": str, "temperature": float, "description": str}],
            "count": int,
            "error": str | None,
        }
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("get_forecast_skipped_no_api_key", city=city)
        return _missing_key_error("get_forecast")

    actual_days = max(1, min(days, MAX_FORECAST_DAYS))
    params = {
        "q": city,
        "appid": api_key,
        "units": units,
    }
    logger.info("get_forecast_requested", city=city, days=actual_days, units=units)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(OWM_FORECAST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "get_forecast_http_error",
            status=e.response.status_code,
            error=str(e),
        )
        return {
            "success": False,
            "city": city,
            "forecasts": [],
            "count": 0,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except httpx.HTTPError as e:
        logger.error("get_forecast_request_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "city": city,
            "forecasts": [],
            "count": 0,
            "error": f"Request failed: {e}",
        }
    except Exception as e:
        logger.error("get_forecast_unexpected_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "city": city,
            "forecasts": [],
            "count": 0,
            "error": f"Unexpected error: {e}",
        }

    city_name = (data.get("city") or {}).get("name", city)
    raw_list = data.get("list", []) or []

    # 按天数过滤：仅保留 actual_days 天内的预报条目
    now_ts = time.time()
    cutoff_ts = now_ts + actual_days * SECONDS_PER_DAY

    forecasts: list[dict[str, Any]] = []
    for entry in raw_list:
        dt = entry.get("dt")
        if dt is None or dt > cutoff_ts:
            continue
        main = entry.get("main", {}) or {}
        weather_list = entry.get("weather", []) or []
        weather = weather_list[0] if weather_list else {}
        forecasts.append({
            "datetime": entry.get("dt_txt", ""),
            "temperature": main.get("temp"),
            "description": weather.get("description", ""),
        })

    logger.info("get_forecast_done", city=city_name, returned=len(forecasts))

    return {
        "success": True,
        "city": city_name,
        "forecasts": forecasts,
        "count": len(forecasts),
        "error": None,
    }


@mcp.tool()
async def get_weather_by_coords(lat: float, lon: float, units: str = "metric") -> dict:
    """按经纬度查询当前天气

    使用 OpenWeatherMap Current Weather API（坐标查询）。

    Args:
        lat: 纬度（-90 ~ 90）
        lon: 经度（-180 ~ 180）
        units: 温度单位（metric=摄氏, imperial=华氏, standard=开尔文）

    Returns:
        {
            "success": bool,
            "city": str,
            "temperature": float,
            "feels_like": float,
            "humidity": int,
            "wind_speed": float,
            "description": str,
            "icon": str,
            "units": str,
            "error": str | None,
        }
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("get_weather_by_coords_skipped_no_api_key", lat=lat, lon=lon)
        return _missing_key_error("get_weather_by_coords")

    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": units,
    }
    logger.info("get_weather_by_coords_requested", lat=lat, lon=lon, units=units)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(OWM_CURRENT_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            "get_weather_by_coords_http_error",
            status=e.response.status_code,
            error=str(e),
        )
        return {
            "success": False,
            "lat": lat,
            "lon": lon,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
        }
    except httpx.HTTPError as e:
        logger.error("get_weather_by_coords_request_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "lat": lat,
            "lon": lon,
            "error": f"Request failed: {e}",
        }
    except Exception as e:
        logger.error("get_weather_by_coords_unexpected_error", error=str(e), exc_info=True)
        return {
            "success": False,
            "lat": lat,
            "lon": lon,
            "error": f"Unexpected error: {e}",
        }

    logger.info("get_weather_by_coords_done", lat=lat, lon=lon)
    return _parse_current(data, units)


if __name__ == "__main__":
    mcp.run()
