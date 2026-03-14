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

print("AllergyMap DB initialized with indexes.");
