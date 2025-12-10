#!/usr/bin/env python
"""
DESTRUCTIVE DATABASE RESET SCRIPT
This script will completely nuke your local database and reset all migrations.
"""

import glob
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


# Project configuration
PROJECT_ROOT = Path(
    __file__
).parent.parent.parent  # Go up from scripts/destructive/ to project root
SPOT_DIR = PROJECT_ROOT / "spot"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# Django apps (from your project structure)
DJANGO_APPS = [
    "app",
    "bucketlist",
    "contacts",
    "dashboard",
    "drinks",
    "entity",
    "event",
    "ledger",
    "media",
    "moderation",
    "notifications",
    "organization",
    "payment",
    "place",
    "social",
    "social_iq",
    "user",
]


def print_banner(message):
    """Print a prominent banner message"""
    print("\n" + "=" * 60)
    print(f" {message}")
    print("=" * 60)


def run_command(command, cwd=None, check=True, interactive=False):
    """Run a shell command and handle errors"""
    if cwd is None:
        cwd = SPOT_DIR

    command_str = command if isinstance(command, str) else " ".join(command)
    print(f"Running: {command_str}")

    run_kwargs = {
        "cwd": cwd,
        "check": check,
    }
    if not interactive:
        run_kwargs["capture_output"] = True
        run_kwargs["text"] = True

    try:
        result = subprocess.run(
            command.split() if isinstance(command, str) else command, **run_kwargs
        )
        if not interactive:
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
        return result
    except subprocess.CalledProcessError as e:
        print(f"❌ Command failed: {e}")
        if not interactive:
            if e.stdout:
                print("STDOUT:", e.stdout)
            if e.stderr:
                print("STDERR:", e.stderr)
        if check:
            raise
        return e


def confirm_destructive_action():
    """Get user confirmation for destructive actions"""
    print(
        "[WARNING] This will PERMANENTLY DELETE your local database and all migration files!"
    )
    print("[WARNING] This action CANNOT be undone!")
    print("")

    response = input("Are you sure you want to continue? Type 'y' to confirm: ")
    if response.lower() != "y":
        print("[ERROR] Operation cancelled")
        return False

    print("\nProceeding with database reset...")
    return True


def delete_database():
    """Delete the SQLite database file"""
    print_banner("DELETING DATABASE")

    db_paths = [
        SPOT_DIR / "db.sqlite3",
        PROJECT_ROOT / "db.sqlite3",
        SPOT_DIR / "app" / "db.sqlite3",
    ]

    deleted = False
    for db_path in db_paths:
        if db_path.exists():
            print(f"Deleting database: {db_path}")
            db_path.unlink()
            deleted = True

    if not deleted:
        print("No database file found to delete")
    else:
        print("[OK] Database deleted successfully")


def handle_rmtree_error(func, path, exc_info):
    """
    Error handler for `shutil.rmtree`.
    If the error is a `PermissionError`, it attempts to change the file's
    permissions to writable and retries the operation. This is to handle
    issues with read-only files, common on Windows.
    """
    # We are interested in the exception value
    exc_value = exc_info[1]
    if isinstance(exc_value, PermissionError):
        # Add write permission
        os.chmod(path, stat.S_IWRITE)
        # Retry the operation that failed
        func(path)
    else:
        # For other errors, re-raise the exception
        raise


def delete_migration_files():
    """Delete all migration files except __init__.py"""
    print_banner("DELETING MIGRATION FILES")

    total_deleted = 0

    for app in DJANGO_APPS:
        migrations_dir = SPOT_DIR / app / "migrations"
        if not migrations_dir.exists():
            print(f"No migrations directory for {app}")
            continue

        # Delete all .py files except __init__.py
        migration_files = list(migrations_dir.glob("*.py"))
        deleted_count = 0

        for migration_file in migration_files:
            if migration_file.name != "__init__.py":
                print(f"Deleting: {migration_file}")
                migration_file.unlink()
                deleted_count += 1

        # Delete __pycache__ if it exists
        pycache_dir = migrations_dir / "__pycache__"
        if pycache_dir.exists():
            shutil.rmtree(pycache_dir, onerror=handle_rmtree_error)
            print(f"Deleted __pycache__ for {app}")

        total_deleted += deleted_count
        print(f"[OK] Deleted {deleted_count} migration files from {app}")

    print(f"\n[OK] Total migration files deleted: {total_deleted}")


def create_migrations():
    """Create new migration files for all apps"""
    print_banner("CREATING NEW MIGRATIONS")

    # Create migrations for each app
    for app in DJANGO_APPS:
        print(f"\nCreating migrations for {app}...")
        run_command([sys.executable, "manage.py", "makemigrations", app])

    # Create any additional migrations
    print("\nCreating any remaining migrations...")
    run_command([sys.executable, "manage.py", "makemigrations"])


def run_migrations():
    """Run all migrations to create database tables"""
    print_banner("RUNNING MIGRATIONS")

    print("Running migrate...")
    run_command([sys.executable, "manage.py", "migrate"])


def create_superuser():
    """Optionally create a superuser"""
    print_banner("CREATING SUPERUSER")

    response = input("Do you want to create a superuser? (y/N): ")
    if response.lower() in ["y", "yes"]:
        print("Creating superuser (you'll need to provide details)...")
        run_command(
            [sys.executable, "manage.py", "createsuperuser"],
            check=False,
            interactive=True,
        )


def load_fixtures():
    """Load any fixture files found in the project"""
    print_banner("LOADING FIXTURES")

    # Look for fixture files
    fixture_patterns = [
        SPOT_DIR / "*/fixtures/*.json",
        SPOT_DIR / "fixtures/*.json",
        PROJECT_ROOT / "fixtures/*.json",
    ]

    fixtures_found = []
    for pattern in fixture_patterns:
        fixtures_found.extend(glob.glob(str(pattern)))

    if not fixtures_found:
        print("No fixture files found")
        return

    # Exact loading order that works (provided by user)
    correct_loading_order = [
        "authgroup.json",  # Load auth groups first
        # 'authgrouppermissions.json',     # SKIP - causes constraint issues
        "gender.json",
        "profiletype.json",  # Load profile dependencies BEFORE users
        "mediatype.json",
        "profilevisibility.json",
        "user.json",  # Load users after profile dependencies
        "profile.json",
        "timezone.json",
        "country.json",
        "region.json",
        "city.json",
        "address.json",
        "place.json",
        "entitytype.json",
        "entity.json",
        "organizationtype.json",
        "organizationenrollment.json",
        "organizationvisibility.json",
        "organizationrole.json",
        "requesttype.json",
        "relationshiptype.json",
        "organization.json",
        "organizationrelationship.json",
        "organizationmember.json",
        "badges.json",
        "bucketlistitems.json",
        "userbucketlist.json",
    ]

    # Additional fixtures to exclude (only the problematic ones)
    fixtures_to_exclude = [
        "authgrouppermissions.json",  # Contains problematic permission ID references
        "authpermission.json",  # Django auto-generated permissions
        "contenttypes.json",  # Django auto-generated content types
    ]

    # Create a mapping of fixture names to their full paths
    fixture_map = {}
    for fixture_path in fixtures_found:
        fixture_name = Path(fixture_path).name
        # Skip excluded fixtures
        if fixture_name not in fixtures_to_exclude:
            fixture_map[fixture_name] = fixture_path

    # Build ordered list of fixtures that exist and are not excluded
    ordered_fixtures = []
    for fixture_name in correct_loading_order:
        if fixture_name in fixture_map:
            ordered_fixtures.append(fixture_map[fixture_name])

    # Add any remaining fixtures not in the order list (also not excluded)
    remaining_fixtures = [
        fixture
        for fixture in fixtures_found
        if Path(fixture).name not in correct_loading_order
        and Path(fixture).name not in fixtures_to_exclude
    ]
    ordered_fixtures.extend(remaining_fixtures)

    if not ordered_fixtures:
        print("No fixtures found to load (after excluding system fixtures)")
        return

    print(f"Found {len(ordered_fixtures)} fixture files (in dependency order):")
    for fixture in ordered_fixtures:
        print(f"  - {Path(fixture).name}")

    if remaining_fixtures:
        print("\nAdditional fixtures (not in predefined order):")
        for fixture in remaining_fixtures:
            print(f"  - {Path(fixture).name}")

    if fixtures_to_exclude:
        excluded_count = len(
            [f for f in fixtures_found if Path(f).name in fixtures_to_exclude]
        )
        if excluded_count > 0:
            print(f"\nExcluded {excluded_count} Django system fixtures:")
            for excluded in fixtures_to_exclude:
                if any(Path(f).name == excluded for f in fixtures_found):
                    print(f"  - {excluded} (problematic system fixture)")

    response = input("\nLoad all fixtures? (y/N): ")
    if response.lower() in ["y", "yes"]:
        for fixture in ordered_fixtures:
            print(f"Loading fixture: {fixture}")
            run_command([sys.executable, "manage.py", "loaddata", fixture])


def run_population_scripts():
    """Run the population scripts using the actual working scripts"""
    print_banner("RUNNING POPULATION SCRIPTS")

    response = input("Run all population scripts? (y/N): ")
    if response.lower() not in ["y", "yes"]:
        return

    # Define the scripts to run in order (NEW VERSIONS - using ledger system)
    scripts = [
        ("ensure_target_user.py", "Ensure target user exists"),
        ("populate_drinks_catalog.py", "Create drinks catalog and credit packages"),
        (
            "populate_credit_history_NEW.py",
            "Create credit purchase history (NEW ledger system)",
        ),
        (
            "populate_drink_transactions_FIXED.py",
            "Create drink sending/receiving (FIXED ledger system)",
        ),
        ("populate_venue_ledger.py", "Create venue-specific transactions"),
        ("populate_drink_stats.py", "Create patterns and preferences"),
        ("force_friendship.py", "Force friendship (if needed)"),
    ]

    scripts_dir = SCRIPTS_DIR / "non-destructive"
    successful_scripts = 0
    failed_scripts = 0

    for i, (script_name, description) in enumerate(scripts, 1):
        print("\n" + "-" * 50)
        print(f"Step {i}/{len(scripts)}: {description}")
        print(f"Running: {script_name}")
        print("-" * 50)

        script_path = scripts_dir / script_name
        if not script_path.exists():
            print(f"[WARNING] Script not found: {script_path}")
            failed_scripts += 1
            continue

        try:
            # Set environment variable for non-interactive mode
            env = os.environ.copy()
            env["SPOT_AUTO_POPULATE"] = "1"

            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=SPOT_DIR,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print("STDERR:", result.stderr)
            print(f"✅ [OK] Step {i} completed successfully")
            successful_scripts += 1

        except subprocess.CalledProcessError as e:
            print(f"❌ [ERROR] Step {i} failed: {e}")
            failed_scripts += 1

            # For non-critical scripts, continue with the rest
            if script_name in ["force_friendship.py"]:
                print("   ⚠️  Continuing with remaining scripts...")
                continue
            print("   ⚠️  Critical script failed, but continuing...")
            continue

        except Exception as e:
            print(f"❌ [ERROR] Unexpected error in step {i}: {e}")
            failed_scripts += 1
            continue

    # Summary
    print("\n" + "=" * 50)
    print("POPULATION SCRIPTS SUMMARY")
    print("=" * 50)
    print(f"Total scripts: {len(scripts)}")
    print(f"Successful: {successful_scripts}")
    print(f"Failed: {failed_scripts}")

    if failed_scripts == 0:
        print("🎉 [SUCCESS] All population scripts completed successfully!")
    elif successful_scripts > 0:
        print("⚠️  [PARTIAL] Some scripts completed, but there were failures.")
        print("    You can manually run failed scripts from:")
        print(f"    {scripts_dir}")
    else:
        print("❌ [FAILURE] All scripts failed. Check the error messages above.")
        print("    You can manually run scripts from:")
        print(f"    {scripts_dir}")


def verify_setup():
    """Verify the database setup is working"""
    print_banner("VERIFYING SETUP")

    # Check database
    print("Checking database...")
    run_command([sys.executable, "manage.py", "check", "--database", "default"])

    # Show migration status
    print("\nMigration status:")
    run_command([sys.executable, "manage.py", "showmigrations"])

    # Show data summary
    print("\nData summary:")
    run_command([sys.executable, "manage.py", "data_summary"], check=False)


def main():
    """Main function to reset database"""
    print_banner("SPOT DATABASE RESET SCRIPT")
    print("This script will completely reset your local Django database")

    # Verify we're in the right directory
    if not SPOT_DIR.exists():
        print(f"[ERROR] Spot directory not found: {SPOT_DIR}")
        print("Please run this script from the project root directory")
        return None

    if not (SPOT_DIR / "manage.py").exists():
        print(f"[ERROR] manage.py not found in {SPOT_DIR}")
        print("Please verify your project structure")
        return None

    # Get confirmation
    if not confirm_destructive_action():
        return None

    try:
        # Execute reset steps
        delete_database()
        delete_migration_files()
        create_migrations()
        run_migrations()
        load_fixtures()
        create_superuser()
        run_population_scripts()  # NEW: Run population scripts
        verify_setup()

        print_banner("DATABASE RESET COMPLETE")
        print("[OK] Your database has been completely reset")
        print("[OK] All migrations have been recreated")
        print("[OK] Database tables have been created")
        print("[OK] Fixtures have been loaded")
        print("[OK] Population scripts have been run")
        print("\nNext steps:")
        print("1. Start your development server")
        print("2. Test your API endpoints")
        print("3. Verify data looks correct")

    except Exception as e:
        print(f"[ERROR] Command failed: {e}")
        return False

    return True


if __name__ == "__main__":
    main()
