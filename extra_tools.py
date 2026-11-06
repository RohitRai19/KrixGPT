"""
Live-data tools that need no API key.

- get_weather: Open-Meteo (geocoding + current conditions + 3-day outlook)
- get_stock_price: Yahoo Finance via yfinance (quotes, day change, 52-week range)

Both return plain text the model can quote directly, and both degrade to a
short error message instead of raising, so a flaky upstream never breaks
the chat turn.
"""

import logging
from datetime import datetime, timezone

import requests
from langchain_core.tools import tool

HTTP_TIMEOUT = 10
_HEADERS = {"User-Agent": "KrixGPT/1.0"}

# WMO weather interpretation codes used by Open-Meteo
_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def _describe(code) -> str:
    try:
        return _WEATHER_CODES.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


@tool
def get_weather(location: str) -> str:
    """
    Get the current weather and a 3-day forecast for a city or place.
    Use this whenever the user asks about weather, temperature, rain, humidity,
    wind, or what to wear/expect outdoors in a specific place.
    Input: a place name such as "Mumbai", "London, UK" or "New York".
    """
    location = (location or "").strip()
    if not location:
        return "Please tell me which city or place you want the weather for."

    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "en", "format": "json"},
            headers=_HEADERS,
            timeout=HTTP_TIMEOUT,
        ).json()
    except Exception as exc:
        return f"Weather lookup failed while finding '{location}': {type(exc).__name__}."

    results = geo.get("results") or []
    if not results:
        return f"I couldn't find a place called '{location}'. Try adding the country, e.g. 'Paris, France'."

    place = results[0]
    name = ", ".join(p for p in [place.get("name"), place.get("admin1"), place.get("country")] if p)

    try:
        wx = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                           "weather_code,wind_speed_10m,precipitation",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": 3,
            },
            headers=_HEADERS,
            timeout=HTTP_TIMEOUT,
        ).json()
    except Exception as exc:
        return f"Weather service is unavailable right now ({type(exc).__name__}). Please try again shortly."

    cur = wx.get("current") or {}
    daily = wx.get("daily") or {}
    units = wx.get("current_units") or {}
    t_unit = units.get("temperature_2m", "°C")
    w_unit = units.get("wind_speed_10m", "km/h")

    lines = [
        f"Weather for {name} (local time {cur.get('time', 'n/a')}):",
        f"- Now: {cur.get('temperature_2m', '?')}{t_unit}, {_describe(cur.get('weather_code'))}, "
        f"feels like {cur.get('apparent_temperature', '?')}{t_unit}",
        f"- Humidity {cur.get('relative_humidity_2m', '?')}%, wind {cur.get('wind_speed_10m', '?')} {w_unit}, "
        f"precipitation {cur.get('precipitation', 0)} mm",
    ]

    days = daily.get("time") or []
    if days:
        lines.append("- Forecast:")
        for i, day in enumerate(days):
            try:
                label = datetime.fromisoformat(day).strftime("%a %d %b")
            except ValueError:
                label = day
            lines.append(
                f"  {label}: {daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]}{t_unit}, "
                f"{_describe(daily['weather_code'][i])}, "
                f"rain chance {daily.get('precipitation_probability_max', [None]*len(days))[i] or 0}%"
            )

    lines.append("(Source: Open-Meteo)")
    return "\n".join(lines)


def _fmt(value, digits=2):
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


@tool
def get_stock_price(symbol: str) -> str:
    """
    Get the latest stock price, daily change, and key stats for a ticker symbol.
    Use this when the user asks about a share price, stock, market cap, or how a
    company's stock is doing. Input: a ticker such as "AAPL", "TSLA", "MSFT",
    "RELIANCE.NS" (NSE India), "TCS.BO" (BSE India) or an index like "^NSEI".
    If the user gives a company name, convert it to the most likely ticker first.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return "Please give me a ticker symbol, for example AAPL or RELIANCE.NS."

    try:
        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        ticker = yf.Ticker(symbol)
        info = {}
        try:
            fast = ticker.fast_info
            for key in ("currency", "year_low", "year_high", "market_cap", "yearLow", "yearHigh", "marketCap"):
                try:
                    value = getattr(fast, key, None)
                    if value is None and hasattr(fast, "get"):
                        value = fast.get(key)
                    if value is not None:
                        info[key] = value
                except Exception:
                    pass
        except Exception:
            info = {}

        hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
    except Exception as exc:
        return f"Stock lookup failed for {symbol}: {type(exc).__name__}. Check the ticker symbol and try again."

    if hist is None or hist.empty:
        return (f"No market data found for '{symbol}'. Make sure it's a valid ticker. "
                f"Indian stocks need a suffix: RELIANCE.NS (NSE) or RELIANCE.BO (BSE).")

    last = hist.iloc[-1]
    prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else last["Open"]
    price = float(last["Close"])
    change = price - float(prev_close)
    pct = (change / float(prev_close) * 100) if prev_close else 0.0
    sign = "+" if change >= 0 else ""

    currency = info.get("currency") or ""
    name = ""
    try:
        name = (ticker.info or {}).get("shortName") or (ticker.info or {}).get("longName") or ""
    except Exception:
        name = ""

    as_of = hist.index[-1]
    try:
        as_of = as_of.strftime("%Y-%m-%d")
    except Exception:
        as_of = str(as_of)

    lines = [
        f"{name + ' ' if name else ''}({symbol}) as of {as_of}:",
        f"- Price: {_fmt(price)} {currency}".rstrip(),
        f"- Change: {sign}{_fmt(change)} ({sign}{pct:.2f}%) vs previous close {_fmt(prev_close)}",
        f"- Day range: {_fmt(last['Low'])} – {_fmt(last['High'])}, open {_fmt(last['Open'])}, volume {_fmt(last['Volume'], 0)}",
    ]

    lo, hi = info.get("year_low") or info.get("yearLow"), info.get("year_high") or info.get("yearHigh")
    if lo and hi:
        lines.append(f"- 52-week range: {_fmt(lo)} – {_fmt(hi)}")
    mcap = info.get("market_cap") or info.get("marketCap")
    if mcap:
        lines.append(f"- Market cap: {_fmt(mcap, 0)} {currency}".rstrip())

    lines.append(f"(Source: Yahoo Finance, retrieved {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}; "
                 "prices may be delayed and are not investment advice)")
    return "\n".join(lines)
