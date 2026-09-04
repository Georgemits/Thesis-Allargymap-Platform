// MongoDB initialization script — runs once on first container start.

db = db.getSiblingDB("allergymap");

// ── Collection: reports ───────────────────────────────────────────────────
db.createCollection("reports");

db.reports.createIndex({ location: "2dsphere" });
db.reports.createIndex({ timestamp: -1 });
db.reports.createIndex({ city: 1 });

// ── Collection: env_snapshots ─────────────────────────────────────────────
db.createCollection("env_snapshots");

db.env_snapshots.createIndex({ location: "2dsphere" });
db.env_snapshots.createIndex({ city: 1, timestamp: -1 });

// ── Collection: users ─────────────────────────────────────────────────────
// One document per anonymous device (see backend/app/models/user.py). The
// unique index is the constraint that makes device_id an identity rather than
// a label: the register-or-touch upsert relies on it to stay idempotent under
// concurrent requests from the same device.
db.createCollection("users");

db.users.createIndex({ device_id: 1 }, { unique: true });
db.users.createIndex({ last_seen_at: -1 });

// ── Collection: allergy_profiles ──────────────────────────────────────────
// One allergy profile per device, kept out of the users document so it can be
// replaced or erased on its own.
db.createCollection("allergy_profiles");

db.allergy_profiles.createIndex({ device_id: 1 }, { unique: true });

print("AllergyMap DB initialized with indexes.");
