"""Lightweight geo utilities (no external dependency)."""
import math

GREEK_CITIES = {
    "Athens":        (37.9838, 23.7275),
    "Thessaloniki":  (40.6401, 22.9444),
    "Patras":        (38.2466, 21.7346),
    "Heraklion":     (35.3387, 25.1442),
    "Ioannina":      (39.6650, 20.8537),
    "Larissa":       (39.6390, 22.4191),
    "Volos":         (39.3666, 22.9426),
    "Rhodes":        (36.4341, 28.2176),
    "Chania":        (35.5138, 24.0180),
    "Alexandroupoli":(40.8490, 25.8740),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_city(lat: float, lon: float) -> str:
    """Return the name of the nearest configured Greek city."""
    best, best_dist = None, float("inf")
    for name, (clat, clon) in GREEK_CITIES.items():
        d = haversine_km(lat, lon, clat, clon)
        if d < best_dist:
            best_dist = d
            best = name
    return best
