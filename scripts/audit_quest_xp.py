#!/usr/bin/env python3
"""
DDO Quest XP Audit Script

Scrapes XP values from DDO Wiki and compares them against a quest database CSV.
Generates audit reports highlighting discrepancies.

Usage:
    python audit_quest_xp.py input.csv --output-dir ./audit_results
    python audit_quest_xp.py input.csv --delay 1.0 --retries 3 --timeout 10
"""

import argparse
import csv
import json
import time
import sys
import re
from pathlib import Path
from urllib.parse import quote
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, asdict

import requests
from bs4 import BeautifulSoup


@dataclass
class XPValues:
    """Container for XP values across all difficulties."""

    heroic_casual: Optional[int] = None
    heroic_normal: Optional[int] = None
    heroic_hard: Optional[int] = None
    heroic_elite: Optional[int] = None
    epic_casual: Optional[int] = None
    epic_normal: Optional[int] = None
    epic_hard: Optional[int] = None
    epic_elite: Optional[int] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict) -> "XPValues":
        return XPValues(
            **{k: v for k, v in d.items() if k in XPValues.__dataclass_fields__}
        )

    def __eq__(self, other: "XPValues") -> bool:
        return all(
            getattr(self, f) == getattr(other, f) for f in self.__dataclass_fields__
        )


class WikiScraper:
    """Scrapes DDO Wiki pages for quest XP values."""

    BASE_URL = "https://ddowiki.com/page"
    SUIT_SYMBOLS = {"♣": "casual", "♦": "normal", "♥": "hard", "♠": "elite"}

    def __init__(self, delay: float = 0.5, retries: int = 3, timeout: int = 10):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "DDO-Audit-Script/1.0"})

    def _sanitize_quest_name(self, name: str) -> str:
        """Convert quest name to wiki URL format."""
        # Replace spaces with underscores, handle special characters
        name = name.replace(" ", "_")
        return quote(name, safe="")

    def _fetch_page(self, quest_name: str) -> Optional[str]:
        """Fetch wiki page with retry logic."""
        url = f"{self.BASE_URL}/{self._sanitize_quest_name(quest_name)}"

        for attempt in range(self.retries):
            try:
                response = self.session.get(url, timeout=self.timeout)

                if response.status_code == 200:
                    return response.text
                elif response.status_code == 404:
                    return None  # Page doesn't exist

            except requests.RequestException as e:
                if attempt < self.retries - 1:
                    wait_time = 2**attempt  # Exponential backoff
                    print(
                        f"  ⚠ Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                else:
                    print(f"  ✗ Failed after {self.retries} retries")
                    return None

        return None

    def _extract_xp_from_html(self, html: str) -> Tuple[XPValues, bool]:
        """
        Extract XP values from wiki HTML.
        Returns (XPValues, is_valid_quest_page)
        """
        # Check if it's a "page not found" response
        if "We don't currently have an article" in html or "Redirect to:" in html:
            return XPValues(), False

        soup = BeautifulSoup(html, "html.parser")
        xp = XPValues()

        try:
            # Find the infobox (right-side info table)
            infobox = soup.find("table", {"class": "borderless"})
            if not infobox:
                return xp, True  # Page exists but no infobox found

            rows = infobox.find_all("tr")

            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue

                label = cells[0].get_text(strip=True).lower()

                # Check for XP rows
                if "heroic xp" in label:
                    xp_values = self._parse_xp_row(cells[1])
                    for difficulty, value in xp_values.items():
                        setattr(xp, f"heroic_{difficulty}", value)

                elif "epic xp" in label:
                    cell_text = cells[1].get_text(strip=True)
                    # Check for "N/A" or "None" indicating no epic content
                    if "n/a" in cell_text.lower() or "none" in cell_text.lower():
                        pass  # Leave epic values as None
                    else:
                        xp_values = self._parse_xp_row(cells[1])
                        for difficulty, value in xp_values.items():
                            setattr(xp, f"epic_{difficulty}", value)

            return xp, True

        except Exception as e:
            print(f"  ⚠ Error parsing HTML: {e}")
            return xp, True

    def _parse_xp_row(self, cell) -> Dict[str, Optional[int]]:
        """
        Parse XP row to extract casual, normal, hard, elite values.
        Looks for suit symbols: ♣(casual), ♦(normal), ♥(hard), ♠(elite)
        Format: ♣1,230 ♦2,200 ♥2,350 ♠2,500
        """
        result = {"casual": None, "normal": None, "hard": None, "elite": None}

        cell_text = cell.get_text(strip=True)

        # Process each suit symbol in order (casual, normal, hard, elite)
        symbols_in_order = ["♣", "♦", "♥", "♠"]

        for symbol in symbols_in_order:
            if symbol not in cell_text:
                continue

            # Find position of this symbol
            pos = cell_text.find(symbol)
            if pos == -1:
                continue

            # Extract number immediately after the symbol
            after_symbol = cell_text[pos + 1 :]
            numbers = re.findall(r"\d+(?:,\d+)*", after_symbol)

            if numbers:
                # Take the first number found after the symbol
                num_str = numbers[0].replace(",", "")
                try:
                    result[self.SUIT_SYMBOLS[symbol]] = int(num_str)
                except ValueError:
                    pass

        return result

    def scrape_quest(self, quest_name: str) -> Tuple[XPValues, bool, str]:
        """
        Scrape a single quest's XP values.
        Returns (XPValues, page_exists, error_message)
        """
        html = self._fetch_page(quest_name)

        if html is None:
            return XPValues(), False, "Page not found (404)"

        xp, is_valid = self._extract_xp_from_html(html)

        error_msg = "" if is_valid else "Page exists but no XP data found"

        # Add delay between requests to be respectful
        time.sleep(self.delay)

        return xp, is_valid, error_msg


def parse_database_xp(xp_json_str: str) -> XPValues:
    """Parse XP JSON from database into XPValues object."""
    try:
        data = json.loads(xp_json_str)
        xp = XPValues()
        for key, value in data.items():
            if hasattr(xp, key):
                # Handle string values from JSON
                try:
                    setattr(xp, key, int(value) if value and value != "0" else None)
                except (ValueError, TypeError):
                    pass
        return xp
    except (json.JSONDecodeError, TypeError):
        return XPValues()


def load_csv_quests(csv_path: Path) -> List[Dict]:
    """Load quests from CSV file."""
    quests = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            quests.append(row)
    return quests


def init_csv_file(details_file: Path) -> Tuple[csv.DictWriter, object]:
    """Initialize CSV file with headers and return writer and file handle."""
    fieldnames = [
        "quest_name",
        "quest_id",
        "wiki_heroic_casual",
        "wiki_heroic_normal",
        "wiki_heroic_hard",
        "wiki_heroic_elite",
        "wiki_epic_casual",
        "wiki_epic_normal",
        "wiki_epic_hard",
        "wiki_epic_elite",
    ]
    file_handle = open(details_file, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
    writer.writeheader()
    return writer, file_handle


def audit_quests(
    csv_path: Path,
    output_dir: Path,
    csv_writer,
    csv_file_handle,
    delay: float = 0.5,
    retries: int = 3,
    timeout: int = 10,
):
    """Main audit function. Writes mismatches to CSV continuously as they're found."""
    # Load quests from CSV
    print(f"[LOADING] Loading quests from {csv_path}...")
    quests = load_csv_quests(csv_path)
    print(f"   Found {len(quests)} quests")

    scraper = WikiScraper(delay=delay, retries=retries, timeout=timeout)

    print(f"\n[SCRAPING] Scraping XP data from wiki...")

    results = {}
    for idx, quest in enumerate(quests, start=1):
        quest_name = quest.get("name", "Unknown")

        print(f"[{idx}/{len(quests)}] {quest_name}...", end=" ", flush=True)

        # Scrape wiki
        wiki_xp, page_exists, error_msg = scraper.scrape_quest(quest_name)

        # Parse database XP
        db_xp_str = quest.get("xp", "{}")
        db_xp = parse_database_xp(db_xp_str)

        # Compare
        matches = wiki_xp == db_xp

        result_data = {
            "quest_id": quest.get("id", ""),
            "page_exists": page_exists,
            "error": error_msg,
            "matches": matches,
            "wiki_xp": wiki_xp.to_dict(),
            "db_xp": db_xp.to_dict(),
        }
        results[quest_name] = result_data

        # Print result
        if not page_exists:
            print("[WARN] PAGE NOT FOUND")
        elif matches:
            print("[OK] Match")
        else:
            print("[ERR] MISMATCH")
            # Write mismatch to CSV immediately
            row = {"quest_name": quest_name, "quest_id": result_data["quest_id"]}
            for key, val in wiki_xp.to_dict().items():
                row[f"wiki_{key}"] = val
            csv_writer.writerow(row)
            csv_file_handle.flush()  # Flush to disk immediately

    return results, quests


def generate_reports(results: Dict, quests: List[Dict], output_dir: Path):
    """Generate audit reports."""
    print(f"\n📊 Generating reports...")

    # Statistics
    total = len(results)
    matches = sum(1 for r in results.values() if r["matches"])
    mismatches = sum(
        1 for r in results.values() if not r["matches"] and r["page_exists"]
    )
    missing_pages = sum(1 for r in results.values() if not r["page_exists"])

    # Generate summary
    summary_file = output_dir / "audit_summary.txt"
    with open(summary_file, "w") as f:
        f.write("DDO QUEST XP AUDIT REPORT\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("STATISTICS\n")
        f.write("-" * 60 + "\n")
        f.write(f"Total Quests:        {total}\n")
        f.write(f"Matching XP:         {matches} ({100*matches/total:.1f}%)\n")
        f.write(f"Mismatched XP:       {mismatches} ({100*mismatches/total:.1f}%)\n")
        f.write(
            f"Missing Wiki Pages:  {missing_pages} ({100*missing_pages/total:.1f}%)\n\n"
        )

        if mismatches > 0:
            f.write("MISMATCHES\n")
            f.write("-" * 60 + "\n")
            for quest_name, result in sorted(results.items()):
                if not result["matches"] and result["page_exists"]:
                    f.write(f"\n{quest_name} (ID: {result['quest_id']})\n")
                    f.write(f"  Wiki:     {json.dumps(result['wiki_xp'], indent=12)}\n")
                    f.write(f"  Database: {json.dumps(result['db_xp'], indent=12)}\n")

        if missing_pages > 0:
            f.write("\nMISSING WIKI PAGES (Manual Review Needed)\n")
            f.write("-" * 60 + "\n")
            for quest_name, result in sorted(results.items()):
                if not result["page_exists"]:
                    f.write(
                        f"  - {quest_name} (ID: {result['quest_id']}): {result['error']}\n"
                    )

    print(f"   ✓ Summary: {summary_file}")

    # Print summary to console
    print(f"\n[REPORT] AUDIT SUMMARY")
    print(f"   Total Quests:       {total}")
    print(f"   Matching:           {matches} ({100*matches/total:.1f}%)")
    print(f"   Mismatched:         {mismatches} ({100*mismatches/total:.1f}%)")
    print(f"   Missing Pages:      {missing_pages} ({100*missing_pages/total:.1f}%)")


def main():
    parser = argparse.ArgumentParser(
        description="Audit DDO quest XP values against wiki",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python audit_quest_xp.py quests.csv
  python audit_quest_xp.py quests.csv --output-dir ./audit_results --delay 1.0
  python audit_quest_xp.py quests.csv --retries 5 --timeout 15
  python audit_quest_xp.py quests.csv --resume-from checkpoint.json
        """,
    )

    parser.add_argument("csv_file", type=Path, help="Input CSV file with quests")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("audit_results"),
        help="Output directory for reports (default: ./audit_results)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between wiki requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries for failed requests (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Request timeout in seconds (default: 10)",
    )

    args = parser.parse_args()

    # Validate input file
    if not args.csv_file.exists():
        print(f"[ERROR] CSV file not found: {args.csv_file}", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    details_file = args.output_dir / "audit_details.csv"

    try:
        # Initialize CSV file
        csv_writer, csv_file_handle = init_csv_file(details_file)

        # Run audit
        results, quests = audit_quests(
            csv_path=args.csv_file,
            output_dir=args.output_dir,
            csv_writer=csv_writer,
            csv_file_handle=csv_file_handle,
            delay=args.delay,
            retries=args.retries,
            timeout=args.timeout,
        )

        # Close CSV file
        csv_file_handle.close()

        # Generate reports
        generate_reports(results, quests, args.output_dir)

        print(f"\n[SUCCESS] Audit complete! Reports saved to {args.output_dir}")
        print(f"   Details: {details_file}")

    except KeyboardInterrupt:
        print("\n[WARN] Audit interrupted by user")
        print(f"   Partial results saved to {details_file}")
        csv_file_handle.close()
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        print(f"   Partial results saved to {details_file}")
        csv_file_handle.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
