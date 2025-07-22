#!/usr/bin/env python3
"""
Convert game_info.csv to SQL INSERT statements for the game_info table.
"""

import csv
import json
import sys


def convert_csv_to_sql(input_file, output_file=None):
    """
    Convert CSV file to SQL INSERT statements.

    Args:
        input_file (str): Path to the input CSV file
        output_file (str): Path to the output SQL file (optional)
    """

    if output_file is None:
        output_file = input_file.replace(".csv", "_inserts.sql")

    sql_statements = []

    # Add header comment
    sql_statements.append("-- SQL INSERT statements for game_info table")
    sql_statements.append("-- Generated from: " + input_file)
    sql_statements.append("")

    try:
        with open(input_file, "r", encoding="utf-8") as csvfile:
            # Read CSV with proper handling of quoted fields
            reader = csv.reader(csvfile)

            row_count = 0
            for row in reader:
                # Skip header row if it contains column names
                if row_count == 0 and row[0].lower() == "id":
                    row_count += 1
                    continue

                if len(row) != 3:
                    print(
                        f"Warning: Row {row_count + 1} has {len(row)} columns, expected 3. Skipping."
                    )
                    continue

                id_val, timestamp, data = row

                # Escape single quotes in the data field for SQL
                escaped_data = data.replace("'", "''")

                # Create INSERT statement
                sql = f"INSERT INTO game_info (id, timestamp, data) VALUES ({id_val}, '{timestamp}', '{escaped_data}');"
                sql_statements.append(sql)

                row_count += 1
                if row_count % 100 == 0:
                    print(f"Processed {row_count} rows...")

    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found.")
        return False
    except Exception as e:
        print(f"Error processing file: {e}")
        return False

    # Write to output file
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(sql_statements))

        print(f"Successfully converted {row_count} rows to SQL.")
        print(f"Output written to: {output_file}")
        return True

    except Exception as e:
        print(f"Error writing output file: {e}")
        return False


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: python convert_game_info_csv_to_sql.py <input_csv_file> [output_sql_file]"
        )
        print(
            "Example: python convert_game_info_csv_to_sql.py game_info.csv game_info_inserts.sql"
        )
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    success = convert_csv_to_sql(input_file, output_file)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
