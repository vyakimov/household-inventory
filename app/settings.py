"""Configuration and canonical option sets.

Loads a project-local `.env` (see `.env.example`), and also an optional `.env` one
directory up as a convenience for keeping shared secrets outside the repo.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # .../inventory-web

# Shared dev .env one level up holds NOTION_TOKEN; project-local .env can override.
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env", override=True)

DB_PATH = Path(os.environ.get("INVENTORY_DB", str(BASE_DIR / "inventory.db")))

# Canonical option sets mirrored from the Notion "Household Inventory Items" database.
# Seeded into the categories/units lookup tables; the importer adds any extras it finds.
CATEGORIES = [
    "pantry staples",
    "canned & jarred",
    "sauces & condiments",
    "herbs & spices",
    "baking & sweeteners",
    "oils & cooking liquids",
    "tea & coffee",
    "snacks & breakfast",
    "nuts & seeds",
    "beverages",
    "alcohol",
    "kitchen supplies",
    "waste bags",
    "household paper",
    "dishwashing",
    "laundry",
    "cleaning",
    "toiletries",
    "baby",
    "cats",
    "wellness",
    "uncategorized",
]
UNITS = [
    "packs", "cans", "cartons", "kg", "jars", "bottles", "blocks", "boxes", "rolls",
    "bags", "tubes", "containers", "buckets", "pouches", "units", "mixed", "unclear",
    "other", "g",
]

DEFAULT_UNIT = "units"
FALLBACK_CATEGORY = "uncategorized"  # used only if a row has no category

# Notion (importer only; never used by the running web app)
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
NOTION_VERSION = os.environ.get("NOTION_VERSION", "2022-06-28")

# Server
HOST = os.environ.get("INVENTORY_HOST", "0.0.0.0")
PORT = int(os.environ.get("INVENTORY_PORT", "8502"))
