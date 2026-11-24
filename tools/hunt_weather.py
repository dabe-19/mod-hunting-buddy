#!/usr/bin/env python3
"""Hyper-Local Hunting Weather, Real-Physics Thermal Regime, Barometric Trend,
Moon Phase, Deer Movement Index & Astronomical DNR Legal Shooting Hours.
Uses free keyless Open-Meteo API + NOAA solar equations (pure stdlib math).
"""

import datetime
import json
import math
import ssl
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

_WMO_CODES: Dict[int, str] = {
    0: "Clear Sky",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Dense Drizzle",
    56: "Freezing Drizzle",
    57: "Heavy Freezing Drizzle",
    61: "Light Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    66: "Freezing Rain",
    67: "Heavy Freezing Rain",
    71: "Light Snow",
    73: "Snow",
    75: "Heavy Snow",
    77: "Snow Grains",
    80: "Light Showers",
    81: "Rain Showers",
    82: "Heavy Showers",
    85: "Snow Flurries",
    86: "Heavy Snow Showers",
    95: "Thunderstorm",
    96: "Thunderstorm + Hail",
    99: "Severe Thunderstorm",
}


def deg_to_compass(deg: float) -> str:
    return _COMPASS[int(((deg % 360.0) + 11.25) // 22.5) % 16]


def compute_solar_times(lat: float, lon: float, date_obj: datetime.date, tz_offset_hours: float = -6.0) -> Dict[str, Any]:
    """NOAA simplified sunrise/sunset + Wisconsin DNR legal shooting hours (-30m sunrise to +20m sunset)."""
    n = date_obj.timetuple().tm_yday
    lat_rad = math.radians(lat)
    decl = math.radians(23.44) * math.sin(math.radians((360.0 / 365.0) * (n - 81)))
    b = math.radians((360.0 / 365.0) * (n - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    cos_ha = (math.cos(math.radians(90.833)) - math.sin(lat_rad) * math.sin(decl)) / (math.cos(lat_rad) * math.cos(decl))
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha_deg = math.degrees(math.acos(cos_ha))
    solar_noon_min = 720.0 - 4.0 * lon - eot + tz_offset_hours * 60.0
    sunrise_min = solar_noon_min - ha_deg * 4.0
    sunset_min = solar_noon_min + ha_deg * 4.0

    def _fmt(mins: float) -> str:
        mins = mins % 1440.0
        h = int(mins // 60)
        m = int(round(mins % 60))
        if m == 60:
            h = (h + 1) % 24
            m = 0
        return f"{h:02d}:{m:02d}"

    return {
        "date": date_obj.isoformat(),
        "legal_shooting_start": _fmt(sunrise_min - 30.0),
        "sunrise": _fmt(sunrise_min),
        "sunset": _fmt(sunset_min),
        "legal_shooting_end": _fmt(sunset_min + 20.0),
        "sunrise_hour": round((sunrise_min % 1440.0) / 60.0, 2),
        "sunset_hour": round((sunset_min % 1440.0) / 60.0, 2),
    }


def compute_moon_phase(date_obj: datetime.date) -> Dict[str, Any]:
    """Compute synodic lunar phase name & illumination percentage (pure stdlib math)."""
    # Known new moon reference: 2000-01-06 18:14 UTC
    ref = datetime.date(2000, 1, 6)
    days_since = (date_obj - ref).days - 0.76
    synodic = 29.53058867
    age = days_since % synodic
    frac = age / synodic
    illum_pct = round((1.0 - math.cos(2.0 * math.pi * frac)) * 50.0, 1)

    if frac < 0.03 or frac >= 0.97:
        name, icon = "New Moon", "🌑"
    elif frac < 0.22:
        name, icon = "Waxing Crescent", "🌒"
    elif frac < 0.28:
        name, icon = "First Quarter", "🌓"
    elif frac < 0.47:
        name, icon = "Waxing Gibbous", "🌔"
    elif frac < 0.53:
        name, icon = "Full Moon", "🌕"
    elif frac < 0.72:
        name, icon = "Waning Gibbous", "🌖"
    elif frac < 0.78:
        name, icon = "Last Quarter", "🌗"
    else:
        name, icon = "Waning Crescent", "🌘"

    return {
        "name": name,
        "icon": icon,
        "illumination_pct": illum_pct,
        "lunar_age_days": round(age, 1),
    }


def classify_thermal_regime(
    hr_float: float,
    sunrise_hr: float,
    sunset_hr: float,
    wind_mph: float,
    cloud_cover: float,
    radiation_wm2: float,
) -> Dict[str, Any]:
    """Physics-based thermal airflow regime + plain-English explanation & hunting tactic."""
    # 1. Strong ambient wind (>13 mph) mixes the boundary layer and dominates local thermals
    if wind_mph >= 13.0:
        return {
            "mode": "neutral",
            "label": "Wind-Dominated (Thermals Overridden)",
            "strength": "weak",
            "explanation": f"Steady {wind_mph:.0f} mph wind mixes air faster than terrain heating/cooling can pull it.",
            "tactic": "Trust the primary wind direction arrow; local uphill/downhill thermal drift is minimal.",
        }

    # 2. Evening & Dawn Katabatic Cooling (sinking dense air into valleys, creeks, and draws)
    is_evening_cool = hr_float >= (sunset_hr - 1.5)
    is_dawn_cool = hr_float <= (sunrise_hr + 1.0)
    if is_evening_cool or is_dawn_cool:
        clear_boost = cloud_cover < 55.0
        strength = "strong" if (clear_boost and wind_mph <= 7.0) else "moderate"
        window_txt = "pre-sunset / evening cooling" if is_evening_cool else "pre-dawn / first-light cooling"
        return {
            "mode": "katabatic_drainage",
            "label": f"Katabatic Drainage — Sinking Downhill ({strength.title()})",
            "strength": strength,
            "explanation": f"During {window_txt} ({cloud_cover:.0f}% clouds, {wind_mph:.0f} mph wind), cool dense air sinks downhill into creeks, valleys, and low bottoms.",
            "tactic": "Your scent will pool downhill in low draws & water bottoms. Hunt upper ridges/benches or set up below bedding so scent drains away from deer.",
        }

    # 3. Mid-Morning to Early-Afternoon Solar Warming (rising air pulling uphill)
    if (sunrise_hr + 1.2) <= hr_float <= (sunset_hr - 2.0):
        if radiation_wm2 >= 110.0 or cloud_cover < 75.0:
            strength = "strong" if (radiation_wm2 >= 260.0 and wind_mph <= 7.0) else "moderate"
            return {
                "mode": "upslope_warming",
                "label": f"Upslope Warming — Rising Uphill ({strength.title()})",
                "strength": strength,
                "explanation": f"Solar heating ({radiation_wm2:.0f} W/m², {cloud_cover:.0f}% clouds, {wind_mph:.0f} mph wind) warms terrain and pulls air & scent uphill toward ridge tops.",
                "tactic": "Scent lifts uphill toward high points. Hunt lower-to-mid slopes below ridge bedding, or keep bedding downhill of your stand.",
            }

    return {
        "mode": "neutral",
        "label": "Neutral / Overcast Balance",
        "strength": "weak",
        "explanation": f"Heavy cloud cover ({cloud_cover:.0f}%) or midday transition dampens temperature contrast between ridges and valleys.",
        "tactic": "Scent tracks mostly straight downwind with ambient breeze; watch for subtle swirls near water or thick cover.",
    }


def compute_deer_movement_score(
    hr_float: float,
    sunrise_hr: float,
    sunset_hr: float,
    temp_f: float,
    temp_drop_24h: float,
    wind_mph: float,
    pressure_inhg: float,
    delta_3h: float,
    precip_mm: float,
    snow_cm: float,
) -> Tuple[int, str, List[str]]:
    """Composite 0-100 whitetail movement index with transparent contributing factors."""
    score = 45
    factors: List[str] = []

    # Golden hours (dawn/dusk prime movement)
    if abs(hr_float - sunrise_hr) <= 1.5 or abs(hr_float - sunset_hr) <= 1.5:
        score += 22
        factors.append("Prime dawn/dusk window")
    elif (sunrise_hr + 1.5) < hr_float < (sunset_hr - 1.5):
        score += 4
    else:
        score -= 10

    # Barometric pressure sweet spot & front movement
    if 30.00 <= pressure_inhg <= 30.38:
        score += 12
        factors.append(f"High pressure sweet spot ({pressure_inhg:.2f} inHg)")
    if delta_3h >= 1.5:
        score += 12
        factors.append("Rising post-front barometer")
    elif delta_3h <= -1.5:
        score += 10
        factors.append("Falling pre-front barometer")

    # Cold front drop
    if temp_drop_24h <= -8.0:
        score += 12
        factors.append(f"Cold front ({temp_drop_24h:+.0f}°F vs 24h ago)")
    elif temp_f <= 45.0:
        score += 6
        factors.append(f"Crisp {temp_f:.0f}°F movement temp")

    # Wind speed
    if 3.0 <= wind_mph <= 11.0:
        score += 8
    elif wind_mph > 18.0:
        score -= 18
        factors.append(f"High wind ({wind_mph:.0f} mph) suppresses movement")

    # Precipitation
    if snow_cm > 0.0 and snow_cm <= 1.5:
        score += 8
        factors.append("Light snow flurries")
    elif 0.1 <= precip_mm <= 1.5:
        score += 4
        factors.append("Light drizzle")
    elif precip_mm >= 4.0:
        score -= 16
        factors.append("Heavy rain")

    score = max(5, min(100, int(round(score))))
    rating = "PEAK" if score >= 80 else ("HIGH" if score >= 65 else ("MODERATE" if score >= 45 else "SLOW"))
    return score, rating, factors[:3]


def fetch_hunt_weather(lat: float, lon: float, days: int = 3, force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch hourly weather via Pluggable Sovereign Provider Registry (Open-Meteo -> NOAA/NWS -> SQLite cache)."""
    import os as _os
    _mod_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _mod_root not in sys.path:
        sys.path.insert(0, _mod_root)
    from adapters.registry import fetch_weather_cascade

    res_wx = fetch_weather_cascade(lat, lon, days=days, force_refresh=force_refresh)
    data = res_wx.get("raw") or {}
    provider_id = res_wx.get("provider_id") or "open_meteo_weather"
    provider_name = res_wx.get("provider_name") or "Open-Meteo Hourly Forecast"
    from_cache = bool(res_wx.get("from_cache"))
    offline_fallback = bool(res_wx.get("offline_fallback"))

    tz_off = float(data.get("utc_offset_seconds", -21600)) / 3600.0
    h = data.get("hourly") or {}
    times = h.get("time") or []
    temps = h.get("temperature_2m") or []
    feels = h.get("apparent_temperature") or []
    rhum = h.get("relative_humidity_2m") or []
    dewp = h.get("dew_point_2m") or []
    wspd = h.get("wind_speed_10m") or []
    wdir = h.get("wind_direction_10m") or []
    wgst = h.get("wind_gusts_10m") or []
    pres = h.get("pressure_msl") or []
    pprob = h.get("precipitation_probability") or []
    pmm = h.get("precipitation") or []
    snow = h.get("snowfall") or []
    cloud = h.get("cloud_cover") or []
    srad = h.get("shortwave_radiation") or []
    vis = h.get("visibility") or []
    wcode = h.get("weather_code") or []

    # Build daily shooting hours keyed by YYYY-MM-DD
    unique_dates: List[str] = []
    for t in times:
        dstr = t[:10]
        if dstr not in unique_dates:
            unique_dates.append(dstr)
    if not unique_dates:
        unique_dates = [datetime.date.today().isoformat()]

    shooting_by_date: Dict[str, Dict[str, Any]] = {}
    shooting_hours: List[Dict[str, Any]] = []
    for dstr in unique_dates:
        try:
            d_obj = datetime.date.fromisoformat(dstr)
        except Exception:
            d_obj = datetime.date.today()
        sh = compute_solar_times(lat, lon, d_obj, tz_off)
        sh["moon"] = compute_moon_phase(d_obj)
        shooting_by_date[dstr] = sh
        shooting_hours.append(sh)

    # Determine current local hour index so UI defaults to "now" instead of midnight
    now_local = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=int(tz_off * 3600))
    now_prefix = now_local.strftime("%Y-%m-%dT%H")
    current_idx = 0
    for idx, t in enumerate(times):
        if t.startswith(now_prefix):
            current_idx = idx
            break

    hourly = []
    for i, t in enumerate(times):
        dstr = t[:10]
        sh = shooting_by_date.get(dstr) or shooting_hours[0]
        hr_num = int(t[11:13]) if len(t) >= 13 else 12
        t_f = float(temps[i]) if i < len(temps) and temps[i] is not None else 45.0
        t_24_ago = float(temps[i - 24]) if i >= 24 and temps[i - 24] is not None else t_f
        feels_f = float(feels[i]) if i < len(feels) and feels[i] is not None else t_f
        rh = float(rhum[i]) if i < len(rhum) and rhum[i] is not None else 65.0
        dp_f = float(dewp[i]) if i < len(dewp) and dewp[i] is not None else (t_f - 8.0)
        ws = float(wspd[i]) if i < len(wspd) and wspd[i] is not None else 6.0
        wg = float(wgst[i]) if i < len(wgst) and wgst[i] is not None else ws
        wd = float(wdir[i]) if i < len(wdir) and wdir[i] is not None else 0.0
        p_hpa = float(pres[i]) if i < len(pres) and pres[i] is not None else 1013.2
        p_prev = float(pres[max(0, i - 3)]) if i < len(pres) and pres[max(0, i - 3)] is not None else p_hpa
        delta_3h = round(p_hpa - p_prev, 1)
        p_inhg = round(p_hpa * 0.02953, 2)
        trend = "rising" if delta_3h >= 1.5 else ("falling" if delta_3h <= -1.5 else "steady")
        pp = int(pprob[i]) if i < len(pprob) and pprob[i] is not None else 0
        pr_mm = float(pmm[i]) if i < len(pmm) and pmm[i] is not None else 0.0
        sn_cm = float(snow[i]) if i < len(snow) and snow[i] is not None else 0.0
        cc = float(cloud[i]) if i < len(cloud) and cloud[i] is not None else 50.0
        rad = float(srad[i]) if i < len(srad) and srad[i] is not None else 0.0
        vis_mi = round((float(vis[i]) / 1609.34), 1) if i < len(vis) and vis[i] is not None else 10.0
        wc = int(wcode[i]) if i < len(wcode) and wcode[i] is not None else 0
        cond_txt = _WMO_CODES.get(wc, "Variable")

        therm = classify_thermal_regime(
            hr_float=float(hr_num),
            sunrise_hr=float(sh.get("sunrise_hour", 7.0)),
            sunset_hr=float(sh.get("sunset_hour", 16.5)),
            wind_mph=ws,
            cloud_cover=cc,
            radiation_wm2=rad,
        )
        mv_score, mv_rating, mv_factors = compute_deer_movement_score(
            hr_float=float(hr_num),
            sunrise_hr=float(sh.get("sunrise_hour", 7.0)),
            sunset_hr=float(sh.get("sunset_hour", 16.5)),
            temp_f=t_f,
            temp_drop_24h=t_f - t_24_ago,
            wind_mph=ws,
            pressure_inhg=p_inhg,
            delta_3h=delta_3h,
            precip_mm=pr_mm,
            snow_cm=sn_cm,
        )

        hourly.append({
            "time": t,
            "date": dstr,
            "temp_f": round(t_f, 1),
            "feels_like_f": round(feels_f, 1),
            "humidity_pct": round(rh),
            "dew_point_f": round(dp_f, 1),
            "wind_mph": round(ws, 1),
            "gust_mph": round(wg, 1),
            "wind_deg": round(wd),
            "wind_compass": deg_to_compass(wd),
            "scent_to_deg": round((wd + 180.0) % 360.0),
            "scent_to_compass": deg_to_compass((wd + 180.0) % 360.0),
            "pressure_hpa": round(p_hpa, 1),
            "pressure_inhg": p_inhg,
            "pressure_delta_3h": delta_3h,
            "pressure_trend": trend,
            "precip_prob": pp,
            "precip_mm": round(pr_mm, 2),
            "snowfall_cm": round(sn_cm, 2),
            "cloud_cover_pct": round(cc),
            "shortwave_wm2": round(rad),
            "visibility_mi": vis_mi,
            "weather_code": wc,
            "condition": cond_txt,
            "thermal_mode": therm["mode"],
            "thermal_label": therm["label"],
            "thermal_strength": therm["strength"],
            "thermal_explanation": therm["explanation"],
            "thermal_tactic": therm["tactic"],
            "movement_score": mv_score,
            "movement_rating": mv_rating,
            "movement_factors": mv_factors,
            "shooting_light": sh,
            "moon": sh["moon"],
        })

    cur_cond = hourly[current_idx] if hourly and 0 <= current_idx < len(hourly) else (hourly[0] if hourly else {})

    # Build compact key hunting windows (Now, Today Dusk, Tomorrow Dawn, Tomorrow Dusk)
    # Placed at the TOP of the payload so agent workflows never lose live/upcoming weather to truncation.
    key_windows: List[Dict[str, Any]] = []
    if hourly:
        def _win_summary(label: str, item: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "window": label,
                "time": item.get("time"),
                "temp_f": item.get("temp_f"),
                "feels_like_f": item.get("feels_like_f"),
                "condition": item.get("condition"),
                "wind_deg": item.get("wind_deg"),
                "wind_compass": item.get("wind_compass"),
                "wind_mph": item.get("wind_mph"),
                "gust_mph": item.get("gust_mph"),
                "scent_to_compass": item.get("scent_to_compass"),
                "pressure_inhg": item.get("pressure_inhg"),
                "pressure_trend": item.get("pressure_trend"),
                "precip_prob": item.get("precip_prob"),
                "thermal_mode": item.get("thermal_mode"),
                "thermal_label": item.get("thermal_label"),
                "movement_score": item.get("movement_score"),
                "movement_rating": item.get("movement_rating"),
                "movement_factors": item.get("movement_factors"),
            }

        key_windows.append(_win_summary("NOW (Current Local Hour)", cur_cond))
        seen_times = {cur_cond.get("time")}
        for day_i, dstr in enumerate(unique_dates[:2]):
            sh = shooting_by_date.get(dstr) or shooting_hours[0]
            sr_h = int(round(float(sh.get("sunrise_hour", 7.0))))
            ss_h = int(round(float(sh.get("sunset_hour", 16.5)))) - 1
            targets = [("Today Dawn" if day_i == 0 else "Tomorrow Dawn", sr_h),
                       ("Today Evening Sit" if day_i == 0 else "Tomorrow Evening Sit", ss_h)]
            for wlbl, target_hr in targets:
                for item in hourly:
                    if item.get("date") == dstr and len(item.get("time", "")) >= 13 and int(item["time"][11:13]) == target_hr:
                        if item["time"] not in seen_times:
                            seen_times.add(item["time"])
                            key_windows.append(_win_summary(wlbl, item))
                        break

    return {
        "status": "ok",
        "lat": lat,
        "lon": lon,
        "timezone": data.get("timezone", "America/Chicago"),
        "fetched_at": res_wx.get("fetched_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "provider_id": provider_id,
        "provider_name": provider_name,
        "from_cache": from_cache,
        "offline_fallback": offline_fallback,
        "warnings": res_wx.get("warnings") or [],
        "sources": [provider_name, "NOAA Solar / DNR Legal Shooting Light"],
        "current_hour_index": current_idx,
        "current_conditions": cur_cond,
        "key_hunting_windows": key_windows,
        "moon": shooting_hours[0]["moon"] if shooting_hours else compute_moon_phase(datetime.date.today()),
        "shooting_hours": shooting_hours,
        "hourly": hourly,
    }


if __name__ == "__main__":
    payload = json.loads(sys.stdin.read() or "{}")
    try:
        out = fetch_hunt_weather(
            float(payload["lat"]),
            float(payload["lon"]),
            int(payload.get("days", 3)),
            bool(payload.get("force_refresh", False)),
        )
    except Exception as exc:
        out = {"status": "error", "message": str(exc)}
    print(json.dumps(out, indent=2))
