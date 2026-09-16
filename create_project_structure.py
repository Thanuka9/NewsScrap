from pathlib import Path

# ============================================================
# PROJECT ROOT
# ============================================================

ROOT = Path(r"C:\Users\thanuka\Desktop\Projects\News Scrap")


# ============================================================
# FOLDERS TO CREATE
# ============================================================

folders = [
    # Alembic / database migrations
    "alembic",
    "alembic/versions",

    # Configuration
    "config",
    "config/sources",

    # Main Python package
    "src",
    "src/bank_intel",

    # Database
    "src/bank_intel/database",
    "src/bank_intel/database/models",

    # Collection
    "src/bank_intel/collectors",
    "src/bank_intel/collectors/sources",

    # Extraction
    "src/bank_intel/extraction",

    # Common utilities
    "src/bank_intel/common",

    # Raw / processed data
    "data",
    "data/raw",
    "data/staged",
    "data/processed",

    # Scripts
    "scripts",

    # Tests
    "tests",
    "tests/collectors",
    "tests/extraction",
    "tests/database",

    # Logs
    "logs",

    # Documentation
    "docs",
]


# ============================================================
# EMPTY / STARTER FILES
# ============================================================

files = [
    # Root
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    ".env",
    ".env.example",
    ".gitignore",
    "alembic.ini",

    # Package initialization
    "src/bank_intel/__init__.py",

    # Database
    "src/bank_intel/database/__init__.py",
    "src/bank_intel/database/base.py",
    "src/bank_intel/database/session.py",

    "src/bank_intel/database/models/__init__.py",
    "src/bank_intel/database/models/source.py",
    "src/bank_intel/database/models/crawl.py",
    "src/bank_intel/database/models/article.py",

    # Collectors
    "src/bank_intel/collectors/__init__.py",
    "src/bank_intel/collectors/base.py",
    "src/bank_intel/collectors/http_client.py",
    "src/bank_intel/collectors/browser_client.py",
    "src/bank_intel/collectors/popup_handler.py",

    # Individual newspaper adapters
    "src/bank_intel/collectors/sources/__init__.py",
    "src/bank_intel/collectors/sources/daily_news.py",
    "src/bank_intel/collectors/sources/daily_mirror.py",
    "src/bank_intel/collectors/sources/ceylon_today.py",
    "src/bank_intel/collectors/sources/island.py",
    "src/bank_intel/collectors/sources/daily_ft.py",

    # Extraction
    "src/bank_intel/extraction/__init__.py",
    "src/bank_intel/extraction/article.py",
    "src/bank_intel/extraction/validator.py",

    # Common utilities
    "src/bank_intel/common/__init__.py",
    "src/bank_intel/common/urls.py",
    "src/bank_intel/common/hashing.py",
    "src/bank_intel/common/logging.py",

    # Source configuration
    "config/sources/daily_news.yaml",
    "config/sources/daily_mirror.yaml",
    "config/sources/ceylon_today.yaml",
    "config/sources/island.yaml",
    "config/sources/daily_ft.yaml",

    # Scripts
    "scripts/run_collection.py",

    # Tests
    "tests/__init__.py",
    "tests/collectors/__init__.py",
    "tests/extraction/__init__.py",
    "tests/database/__init__.py",
]


# ============================================================
# CREATE STRUCTURE
# ============================================================

def create_project_structure():
    print(f"\nCreating project structure at:\n{ROOT}\n")

    ROOT.mkdir(parents=True, exist_ok=True)

    # Create directories
    for folder in folders:
        path = ROOT / folder
        path.mkdir(parents=True, exist_ok=True)
        print(f"[DIR ] {path}")

    # Create files
    for file in files:
        path = ROOT / file

        if not path.exists():
            path.touch()
            print(f"[FILE] {path}")
        else:
            print(f"[SKIP] {path} already exists")

    print("\n======================================")
    print("Project structure created successfully.")
    print("======================================\n")


if __name__ == "__main__":
    create_project_structure()