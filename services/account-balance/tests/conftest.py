import os

# Set before the app is imported. Tests must not try to export spans or open Postgres.
os.environ["OTEL_DISABLED"] = "1"
os.environ.setdefault("LOG_LEVEL", "WARNING")
