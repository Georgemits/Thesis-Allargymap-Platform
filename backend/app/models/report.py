"""
Symptom report document schema (stored in MongoDB collection: reports).

Document shape:
{
    "_id": ObjectId,
    "user_id": str,          # anonymous device/session ID
    "timestamp": datetime,
    "location": {
        "type": "Point",
        "coordinates": [lon, lat]   # GeoJSON order
    },
    "city": str,             # optional resolved city name
    "symptoms": {
        "sneezing":    int,  # 0-12 VAS scale (Visual Analogue Scale)
        "runny_nose":  int,
        "itchy_eyes":  int,
        "cough":       int,
        "skin_rash":   int,
        "wheezing":    int,
    },
    "overall_severity": int, # 0-12 computed or user-provided
    "notes": str,            # optional free text
}

MongoDB indexes to create at startup (see mongo-init/init.js):
  - location (2dsphere)
  - timestamp (-1)
  - city (1)
"""

from datetime import datetime, timezone


def build_report(user_id: str, lon: float, lat: float, symptoms: dict,
                 city: str = "", notes: str = "") -> dict:
    """Return a validated report document ready for insertion."""
    vas_fields = ["sneezing", "runny_nose", "itchy_eyes", "cough", "skin_rash", "wheezing"]
    clean_symptoms = {}
    for field in vas_fields:
        val = symptoms.get(field, 0)
        clean_symptoms[field] = max(0, min(12, int(val)))

    overall = round(sum(clean_symptoms.values()) / len(vas_fields))

    return {
        "user_id": str(user_id),
        "timestamp": datetime.now(timezone.utc),
        "location": {
            "type": "Point",
            "coordinates": [float(lon), float(lat)],
        },
        "city": str(city),
        "symptoms": clean_symptoms,
        "overall_severity": overall,
        "notes": str(notes)[:500],
    }
