import argparse
import os
from pathlib import Path
import subprocess

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description="Back up and restore the PostgreSQL database.")
    parser.add_argument("command", choices=("backup", "restore"))
    parser.add_argument("file", type=Path, help="Path to the .dump archive")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().with_name(".env"), override=True)
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        parser.error("Missing database environment variables: " + ", ".join(missing))

    env = {
        **os.environ,
        "PGHOST": os.environ["DB_HOST"],
        "PGPORT": os.getenv("DB_PORT", "5432"),
        "PGDATABASE": os.environ["DB_NAME"],
        "PGUSER": os.environ["DB_USER"],
        "PGPASSWORD": os.environ["DB_PASSWORD"],
        "PGCONNECT_TIMEOUT": "3",
    }
    if args.command == "backup":
        command = [
            "pg_dump", "--no-password", "--format=custom", "--no-owner", "--no-acl",
            "--file", str(args.file),
        ]
    else:
        command = [
            "pg_restore", "--no-password", "--dbname", env["PGDATABASE"],
            "--clean", "--if-exists", "--no-owner", "--no-acl",
            "--single-transaction", "--exit-on-error", str(args.file),
        ]
    try:
        result = subprocess.run(command, env=env, check=False)
    except FileNotFoundError:
        parser.error(f"{command[0]} was not found. Install PostgreSQL client tools and add them to PATH.")
    if result.returncode == 0:
        print(f"{args.command.capitalize()} completed: {args.file}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
