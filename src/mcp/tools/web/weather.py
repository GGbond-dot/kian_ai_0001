"""
天气查询工具 —— 使用 open-meteo.com 免费 API（国内可访问，无需 API Key）。
流程：geocoding-api 城市名→经纬度 → api.open-meteo 获取天气数据。
"""
import json
import ssl
import urllib.parse
import urllib.request
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# WMO 天气代码 → 中文描述
_WMO_CODE = {
    0: "晴", 1: "多云", 2: "局部多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "冰粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨夹冰雹", 99: "强雷阵雨夹冰雹",
}

def _wmo(code) -> str:
    return _WMO_CODE.get(int(code), f"天气代码{code}")

def _http_get(url: str, timeout: int = 8) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return r.read()


async def query_weather(args: dict) -> str:
    """查询指定城市的实时天气和未来三天预报。"""
    city = (args.get("city") or "").strip()
    if not city:
        return "请告诉我你想查哪个城市的天气，比如「上海天气」。"

    try:
        # Step1: 城市名 → 经纬度
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
        )
        logger.info(f"[天气] 解析城市：{city}")
        geo_data = json.loads(_http_get(geo_url, timeout=6))

        results = geo_data.get("results")
        if not results:
            return f"找不到城市「{city}」，试试完整城市名？"

        loc = results[0]
        lat = loc["latitude"]
        lon = loc["longitude"]
        city_name = loc.get("name", city)
        country = loc.get("country", "")
        admin = loc.get("admin1", "")

        # Step2: 获取天气
        weather_url = (
            "https://api.open-meteo.com/v1/forecast?"
            + urllib.parse.urlencode({
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": "auto",
                "forecast_days": 3,
            })
        )
        logger.info(f"[天气] 获取天气数据：{city_name} ({lat},{lon})")
        w_data = json.loads(_http_get(weather_url, timeout=8))

        cur = w_data["current"]
        temp      = cur["temperature_2m"]
        feels     = cur["apparent_temperature"]
        humidity  = cur["relative_humidity_2m"]
        wind      = cur["wind_speed_10m"]
        cur_desc  = _wmo(cur["weather_code"])

        daily = w_data["daily"]
        day_names = ["今天", "明天", "后天"]
        forecast_lines = []
        for i in range(min(3, len(daily["time"]))):
            d_desc = _wmo(daily["weather_code"][i])
            d_min  = daily["temperature_2m_min"][i]
            d_max  = daily["temperature_2m_max"][i]
            rain   = daily["precipitation_sum"][i]
            rain_str = f"，降水 {rain}mm" if rain and float(rain) > 0 else ""
            forecast_lines.append(f"  {day_names[i]}：{d_desc} {d_min}~{d_max}°C{rain_str}")

        location_str = city_name
        if admin and admin != city_name:
            location_str = f"{admin} · {city_name}"
        if country and country not in ("中国", "China"):
            location_str += f"（{country}）"

        result = (
            f"📍 {location_str} 当前天气\n"
            f"🌤 {cur_desc}，{temp}°C（体感 {feels}°C）\n"
            f"💧 湿度 {humidity}%，风速 {wind} km/h\n\n"
            f"📅 近三天预报：\n" + "\n".join(forecast_lines)
        )
        logger.info(f"[天气] 成功：{city_name}")
        return result

    except urllib.error.URLError as e:
        logger.error(f"[天气] 网络失败：{e}")
        return f"天气查询网络失败：{e}"
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"[天气] 解析失败：{e}")
        return f"天气数据解析失败：{e}"
    except Exception as e:
        logger.error(f"[天气] 未知错误：{e}")
        return f"天气查询出错：{e}"
