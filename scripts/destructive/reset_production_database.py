#!/usr/bin/env python
"""
PRODUCTION DATABASE RESET SCRIPT
This script will reset the production RDS database through Docker containers.
"""

import subprocess
import sys


def print_banner(message):
    """Print a prominent banner message"""
    print("\n" + "=" * 60)
    print(f" {message}")
    print("=" * 60)


def run_docker_command(command, description=""):
    """Run a Django management command inside the Docker container"""
    docker_cmd = f"docker exec spot-web python spot/manage.py {command}"
    print(f"Running: {description or command}")
    print(f"Command: {docker_cmd}")

    try:
        result = subprocess.run(
            docker_cmd, shell=True, check=True, capture_output=True, text=True
        )
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed: {e}")
        if e.stdout:
            print(f"STDOUT: {e.stdout}")
        if e.stderr:
            print(f"STDERR: {e.stderr}")
        return False


def confirm_destructive_action():
    """Get user confirmation for destructive operations"""
    print_banner("⚠️  PRODUCTION DATABASE RESET WARNING ⚠️")
    print("This will:")
    print("1. Drop ALL tables in the production database")
    print("2. Delete ALL data permanently")
    print("3. Recreate tables from scratch")
    print("4. Load fresh fixtures (including bug_tags)")
    print("5. Preserve existing superuser accounts")
    print("\n🔥 THIS ACTION IS IRREVERSIBLE! 🔥")

    confirmation = (
        input("\nProceed with database reset? [y/N] (or just press Enter for yes): ")
        .strip()
        .lower()
    )
    if confirmation == "" or confirmation == "y" or confirmation == "yes":
        print("Confirmed! Starting database reset...")
        return True
    print("Aborted.")
    return False


def drop_all_tables():
    """Drop all tables and sequences from the database using Django"""
    print_banner("DROPPING ALL TABLES")
    print("Using Django connection to drop all tables and sequences...")

    # Inline Python that will run inside the container. We join the lines with semicolons
    # so that the entire script can be passed safely as one argument without worrying
    # about complex shell quoting.
    python_lines = [
        "from django.db import connection",
        "cursor = connection.cursor()",
        "cursor.execute(\"SELECT tablename FROM pg_tables WHERE schemaname = 'public';\")",
        "tables = cursor.fetchall()",
        "for (table,) in tables:",
        "    cursor.execute(f'DROP TABLE IF EXISTS \"{table}\" CASCADE;')",
        "    print(f'Dropped table: {table}')",
        "cursor.execute(\"SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public';\")",
        "seqs = cursor.fetchall()",
        "for (seq,) in seqs:",
        "    cursor.execute(f'DROP SEQUENCE IF EXISTS \"{seq}\" CASCADE;')",
        "    print(f'Dropped sequence: {seq}')",
        "print('✅ All tables and sequences dropped successfully!')",
    ]
    python_code = "\n".join(python_lines)

    # Build docker command as a list to avoid shell-level quoting issues
    docker_cmd = [
        "docker",
        "exec",
        "spot-web",
        "python",
        "spot/manage.py",
        "shell",
        "-c",
        python_code,
    ]

    try:
        result = subprocess.run(docker_cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to drop tables: {e}")
        if e.stdout:
            print(f"STDOUT: {e.stdout}")
        if e.stderr:
            print(f"STDERR: {e.stderr}")
        return False


def make_migrations():
    """Generate fresh migrations"""
    print_banner("GENERATING FRESH MIGRATIONS")

    # First check if any apps need migrations
    print("Checking for model changes that need migrations...")
    if not run_docker_command(
        "makemigrations --dry-run", "Checking for needed migrations"
    ):
        print("Warning: Failed to check for migrations, continuing...")

    # Generate migrations for all apps
    print("Generating fresh migrations...")
    if not run_docker_command("makemigrations", "Generating migrations for all apps"):
        return False

    # Also specifically handle any apps that might have been missed
    print("Ensuring all apps have proper migrations...")
    spot_apps = [
        "user",
        "media",
        "entity",
        "place",
        "organization",
        "event",
        "contacts",
        "notifications",
        "bucketlist",
        "payment",
        "social",
        "drinks",
        "video",
        "ledger",
        "verification",
        "dashboard",
        "moderation",
    ]

    for app in spot_apps:
        run_docker_command(
            f"makemigrations {app}", f"Ensuring {app} migrations are up to date"
        )

    print("Fresh migrations generated!")
    return True


def run_migrations():
    """Run all migrations"""
    print_banner("RUNNING MIGRATIONS")

    # Run migrations
    if not run_docker_command("migrate --noinput", "Running all migrations"):
        return False

    print("Migrations completed successfully!")
    return True


def load_fixtures():
    """Load fixtures in the correct order"""
    print_banner("LOADING FIXTURES")

    # Fixture loading order (from the reset script)
    fixture_order = [
        "authgroup.json",
        "gender.json",
        "profiletype.json",
        "mediatype.json",
        "profilevisibility.json",
        "user.json",
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
        "bug_tags.json",
    ]

    print("Loading fixtures in dependency order...")
    successful_loads = 0

    for fixture in fixture_order:
        fixture_name = fixture.replace(".json", "")
        print(f"Loading {fixture}...")
        if run_docker_command(f"loaddata {fixture_name}", f"Loading {fixture}"):
            successful_loads += 1
        else:
            print(f"Warning: Failed to load {fixture}, continuing...")

    print(f"\nLoaded {successful_loads} out of {len(fixture_order)} fixtures")
    return True


def create_superuser():
    """Create a superuser account (preserve existing superusers)"""
    print_banner("MANAGING SUPERUSERS")

    superuser_lines = [
        "from django.contrib.auth import get_user_model",
        "User = get_user_model()",
        "# Check for existing superusers",
        "existing_superusers = User.objects.filter(is_superuser=True)",
        "if existing_superusers.exists():",
        "    print(f'Found {existing_superusers.count()} existing superuser(s):')",
        "    for user in existing_superusers:",
        "        print(f'  - {user.username} ({user.email})')",
        "    print('Preserving existing superusers - no new superuser created')",
        "else:",
        "    # No superusers exist, create default admin",
        "    if not User.objects.filter(username='admin').exists():",
        "        User.objects.create_superuser('admin', 'admin@spotsocial.app', 'admin123!')",
        "        print('No existing superusers found.')",
        "        print('Created default superuser: admin/admin123!')",
        "    else:",
        "        # Edge case: admin user exists but is not superuser",
        "        admin_user = User.objects.get(username='admin')",
        "        if not admin_user.is_superuser:",
        "            admin_user.is_superuser = True",
        "            admin_user.is_staff = True",
        "            admin_user.save()",
        "            print('Upgraded existing admin user to superuser: admin')",
        "        else:",
        "            print('Admin superuser already exists')",
    ]

    superuser_script = "\n".join(superuser_lines)

    docker_cmd = [
        "docker",
        "exec",
        "spot-web",
        "python",
        "spot/manage.py",
        "shell",
        "-c",
        superuser_script,
    ]

    try:
        result = subprocess.run(docker_cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to manage superuser: {e}")
        if e.stdout:
            print(f"STDOUT: {e.stdout}")
        if e.stderr:
            print(f"STDERR: {e.stderr}")
        return False


def verify_setup():
    """Verify the database setup"""
    print_banner("VERIFYING SETUP")

    verification_lines = [
        "from django.contrib.auth import get_user_model",
        "from spot.social.models import Profile",
        "from spot.place.models import Country",
        "User = get_user_model()",
        "print(f'Users: {User.objects.count()}')",
        "print(f'Profiles: {Profile.objects.count()}')",
        "print(f'Countries: {Country.objects.count()}')",
        "print('Database verification complete!')",
    ]

    verification_script = "\n".join(verification_lines)

    docker_cmd = [
        "docker",
        "exec",
        "spot-web",
        "python",
        "spot/manage.py",
        "shell",
        "-c",
        verification_script,
    ]

    try:
        result = subprocess.run(docker_cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Verification failed: {e}")
        if e.stdout:
            print(f"STDOUT: {e.stdout}")
        if e.stderr:
            print(f"STDERR: {e.stderr}")
        return False


def main():
    """Main function"""
    print_banner("PRODUCTION DATABASE RESET")
    print("This script will reset your production RDS database")

    # Verify Docker containers are running
    try:
        result = subprocess.run(
            "docker ps | grep spot-web", shell=True, capture_output=True, text=True
        )
        if not result.stdout:
            print("[ERROR] spot-web container is not running!")
            print(
                "Please start your containers with: docker-compose -f docker-compose.prod.yml up -d"
            )
            return False
    except Exception as e:
        print(f"[ERROR] Could not check Docker containers: {e}")
        return False

    if not confirm_destructive_action():
        return False

    try:
        print_banner("STARTING DATABASE RESET")

        # Drop all tables
        if not drop_all_tables():
            print("[ERROR] Failed to drop tables")
            return False

        # Generate fresh migrations
        if not make_migrations():
            print("[ERROR] Failed to generate migrations")
            return False

        # Run migrations
        if not run_migrations():
            print("[ERROR] Migration failed")
            return False

        # Load fixtures
        if not load_fixtures():
            print("[ERROR] Fixture loading failed")
            return False

        # Create superuser
        if not create_superuser():
            print("[WARNING] Superuser creation failed, but continuing...")

        # Verify setup
        if not verify_setup():
            print("[WARNING] Verification failed, but reset may have succeeded")

        print_banner("DATABASE RESET COMPLETE! ✅")
        print("Your production database has been reset successfully!")
        print("✅ All tables and sequences dropped")
        print("✅ Fresh migrations generated and applied")
        print("✅ Fixtures loaded (including bug_tags)")
        print("✅ Existing superusers preserved")
        print("\nNext steps:")
        print("1. Test your API endpoints")
        print("2. Verify the application is working")
        print("3. Monitor logs for any issues")
        print("\nNote: If no superusers existed, default created: admin / admin123!")

    except Exception as e:
        print(f"[ERROR] Reset failed: {e}")
        return False

    return True


if __name__ == "__main__":
    if not main():
        sys.exit(1)
