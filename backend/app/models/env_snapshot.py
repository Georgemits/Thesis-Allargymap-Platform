"""
Environmental snapshot document schema (stored in MongoDB collection: env_snapshots).

Populated by the data_collection/open_meteo_fetcher.py script (or a scheduled job).

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
        "alder_pollen": float,
        "birch_pollen": float,
        "grass_pollen": float,
        "mugwort_pollen": float,
        "olive_pollen": float,
        "ragweed_pollen": float,
    },
    "air_quality": {
        "pm10": float,
        "pm2_5": float,
        "dust": float,
        "european_aqi": float,
    },
}
"""
