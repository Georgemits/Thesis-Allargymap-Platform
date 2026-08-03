"""
Environmental snapshot document schema (stored in MongoDB collection: env_snapshots).

Populated by data_collection/open_meteo_fetcher.py (weather + air quality, always)
and data_collection/mongo_importer.py, which attaches pollen fields via
data_collection/pollen_source.py -- Google Pollen API is the primary pollen
source, with Open-Meteo as fallback + historical/archive (see that module's
docstring for the full selection rule and the grains/m3-vs-UPI scale mapping).

Document shape:
{
    "_id": ObjectId,
    "city": str,
    "timestamp": datetime,
    "location": {
        "type": "Point",
        "coordinates": [lon, lat]
    },
    "weather": {
        "temperature_2m": float,
        "relative_humidity_2m": float,
        "apparent_temperature": float,
        "precipitation": float,
        "wind_speed_10m": float,
        "wind_direction_10m": float,
        "pressure_msl": float,
        "cloud_cover": float,
        "uv_index": float,
    },
    "pollen": {
        # Open-Meteo (CAMS), hourly grains/m3 concentration. Always populated
        # when Open-Meteo data was fetched, regardless of pollen_source below.
        "alder_pollen": float,
        "birch_pollen": float,
        "grass_pollen": float,
        "mugwort_pollen": float,
        "olive_pollen": float,
        "ragweed_pollen": float,
    },
    "pollen_upi": {
        # Google Maps Platform Pollen API, daily Universal Pollen Index (0-5).
        # None when pollen_source == "open_meteo" (Google unavailable / skipped).
        "overall": {"grass": {"value": int, "category": str, "in_season": bool}, "tree": {...}, "weed": {...}},
        "plants":  {"olive": {"value": int, "category": str, "in_season": bool}, "graminales": {...}, ...},
    } | None,
    "pollen_source": "google" | "open_meteo",   # which provider's UPI is attached
    "pollen_source_fallback_reason": str,        # present only on a Google->Open-Meteo fallback
    "air_quality": {
        # Open-Meteo air-quality endpoint, CAMS-powered (see data_collection
        # README for the Saharan-dust provenance note).
        "pm10": float,
        "pm2_5": float,
        "dust": float,          # African/Saharan dust, μg/m3 (CAMS Copernicus model)
        "european_aqi": float,
    },
}
"""
