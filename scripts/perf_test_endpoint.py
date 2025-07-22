import requests
import time
import statistics
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import json
import math

ENDPOINT = "https://api.ddoaudit.com/v1/game/server-info"
CALL_COUNT = 100
DEFAULT_TIMEOUT = 30
DEFAULT_THREADS = 1


@dataclass
class RequestResult:
    """Represents the result of a single HTTP request."""

    success: bool
    response_time: float
    status_code: Optional[int] = None
    response_size: Optional[int] = None
    error_message: Optional[str] = None


class PerformanceTester:
    """Enhanced performance testing class with detailed metrics."""

    def __init__(self, endpoint: str, timeout: int = DEFAULT_TIMEOUT):
        self.endpoint = endpoint
        self.timeout = timeout
        self.session = requests.Session()
        # Add some reasonable defaults for the session
        self.session.headers.update({"User-Agent": "DDO-Audit-Service-PerfTest/1.0"})

    def make_request(self) -> RequestResult:
        """Make a single GET request and return detailed results."""
        start_time = time.time()
        try:
            response = self.session.get(self.endpoint, timeout=self.timeout)
            end_time = time.time()

            response_time = end_time - start_time
            response_size = len(response.content) if response.content else 0

            # Try to parse JSON to ensure it's valid
            try:
                response.json()
            except json.JSONDecodeError:
                pass  # We still want to record the metrics even if JSON is invalid

            return RequestResult(
                success=200 <= response.status_code < 300,
                response_time=response_time,
                status_code=response.status_code,
                response_size=response_size,
            )

        except requests.RequestException as e:
            end_time = time.time()
            return RequestResult(
                success=False, response_time=end_time - start_time, error_message=str(e)
            )

    def run_sequential_test(
        self, call_count: int, show_progress: bool = True
    ) -> List[RequestResult]:
        """Run performance test sequentially."""
        results = []

        print(f"Running {call_count} sequential requests to {self.endpoint}")
        start_time = time.time()

        for i in range(call_count):
            if show_progress and i % max(1, call_count // 10) == 0:
                print(f"Progress: {i}/{call_count} ({i/call_count*100:.1f}%)")

            result = self.make_request()
            results.append(result)

        end_time = time.time()
        total_time = end_time - start_time

        if show_progress:
            print(f"Completed {call_count} requests in {total_time:.2f} seconds")

        return results

    def run_concurrent_test(
        self, call_count: int, max_workers: int = 5, show_progress: bool = True
    ) -> List[RequestResult]:
        """Run performance test with concurrent requests."""
        results = []

        print(
            f"Running {call_count} concurrent requests (max {max_workers} threads) to {self.endpoint}"
        )
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all requests
            futures = [executor.submit(self.make_request) for _ in range(call_count)]

            # Collect results as they complete
            completed = 0
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                completed += 1

                if show_progress and completed % max(1, call_count // 10) == 0:
                    print(
                        f"Progress: {completed}/{call_count} ({completed/call_count*100:.1f}%)"
                    )

        end_time = time.time()
        total_time = end_time - start_time

        if show_progress:
            print(f"Completed {call_count} requests in {total_time:.2f} seconds")

        return results


def analyze_results(results: List[RequestResult]) -> Dict[str, Any]:
    """Analyze performance test results and return detailed statistics."""
    if not results:
        return {"error": "No results to analyze"}

    # Filter successful requests for timing analysis
    successful_results = [r for r in results if r.success]
    response_times = [r.response_time for r in successful_results]

    # Status code distribution
    status_codes = {}
    for result in results:
        if result.status_code:
            status_codes[result.status_code] = (
                status_codes.get(result.status_code, 0) + 1
            )

    # Response size analysis
    response_sizes = [
        r.response_size for r in successful_results if r.response_size is not None
    ]

    # Error analysis
    errors = [r.error_message for r in results if not r.success and r.error_message]
    error_types = {}
    for error in errors:
        # Group similar errors
        error_type = error.split(":")[0] if ":" in error else error
        error_types[error_type] = error_types.get(error_type, 0) + 1

    analysis = {
        "total_requests": len(results),
        "successful_requests": len(successful_results),
        "failed_requests": len(results) - len(successful_results),
        "success_rate": len(successful_results) / len(results) * 100 if results else 0,
        "status_codes": status_codes,
        "error_types": error_types,
    }

    if response_times:
        analysis.update(
            {
                "response_time_stats": {
                    "min": min(response_times),
                    "max": max(response_times),
                    "mean": statistics.mean(response_times),
                    "median": statistics.median(response_times),
                    "std_dev": (
                        statistics.stdev(response_times)
                        if len(response_times) > 1
                        else 0
                    ),
                    "p95": (
                        statistics.quantiles(response_times, n=20)[18]
                        if len(response_times) >= 20
                        else max(response_times)
                    ),
                    "p99": (
                        statistics.quantiles(response_times, n=100)[98]
                        if len(response_times) >= 100
                        else max(response_times)
                    ),
                }
            }
        )

    if response_sizes:
        analysis.update(
            {
                "response_size_stats": {
                    "min_bytes": min(response_sizes),
                    "max_bytes": max(response_sizes),
                    "mean_bytes": statistics.mean(response_sizes),
                    "median_bytes": statistics.median(response_sizes),
                }
            }
        )

    return analysis


def create_response_time_histogram(
    response_times: List[float], bins: int = 20, width: int = 50
) -> str:
    """Create an ASCII histogram of response times with adaptive binning."""
    if not response_times:
        return "No response time data available for histogram."

    min_time = min(response_times)
    max_time = max(response_times)
    mean_time = statistics.mean(response_times)
    std_dev = statistics.stdev(response_times) if len(response_times) > 1 else 0

    # Handle edge case where all response times are the same
    if min_time == max_time:
        return f"All response times were identical: {min_time:.3f}s"

    # Adaptive binning: if std dev is very low relative to mean, use finer granularity
    range_span = max_time - min_time
    if std_dev > 0 and range_span / mean_time < 0.1:  # Less than 10% variation
        # Use more granular binning for low-variation data
        bins = min(bins, len(set(response_times)))  # Don't exceed unique values
        if bins < 10:
            bins = 10

    # Create bins
    bin_width = range_span / bins
    bin_counts = [0] * bins
    bin_ranges = []

    # Calculate bin ranges and count occurrences
    for i in range(bins):
        bin_start = min_time + i * bin_width
        bin_end = min_time + (i + 1) * bin_width
        bin_ranges.append((bin_start, bin_end))

        # Count how many response times fall in this bin
        for rt in response_times:
            if i == bins - 1:  # Last bin includes the maximum value
                if bin_start <= rt <= bin_end:
                    bin_counts[i] += 1
            else:
                if bin_start <= rt < bin_end:
                    bin_counts[i] += 1

    # Find the maximum count for scaling
    max_count = max(bin_counts) if bin_counts else 1

    # Create the histogram
    histogram_lines = []
    histogram_lines.append("Response Time Distribution:")
    histogram_lines.append(
        f"Range: {min_time:.3f}s - {max_time:.3f}s (span: {range_span:.3f}s)"
    )
    histogram_lines.append(f"Mean: {mean_time:.3f}s, Std Dev: {std_dev:.3f}s")
    histogram_lines.append("-" * (width + 25))

    # Only show bins that have data or are adjacent to bins with data
    non_empty_bins = [i for i, count in enumerate(bin_counts) if count > 0]
    if non_empty_bins:
        start_bin = max(0, min(non_empty_bins) - 1)
        end_bin = min(len(bin_counts), max(non_empty_bins) + 2)
    else:
        start_bin, end_bin = 0, len(bin_counts)

    for i in range(start_bin, end_bin):
        count = bin_counts[i]
        bin_start, bin_end = bin_ranges[i]

        # Calculate bar length
        if max_count > 0:
            bar_length = int((count / max_count) * width)
        else:
            bar_length = 0

        # Create the bar with different characters for better visualization
        if count == 0:
            bar = "·" * min(2, width // 10)  # Show empty bins with dots
        else:
            bar = "█" * bar_length

        # Format the range and count with better precision for small ranges
        if range_span < 1.0:  # Less than 1 second range, show more precision
            range_str = f"{bin_start:.4f}-{bin_end:.4f}s"
        else:
            range_str = f"{bin_start:.3f}-{bin_end:.3f}s"

        count_str = f"({count:3d})"
        percentage = f"{count/len(response_times)*100:4.1f}%"

        # Create the line
        line = f"{range_str:>18} |{bar:<{width}} {count_str} {percentage}"
        histogram_lines.append(line)

    # Add summary statistics
    histogram_lines.append("-" * (width + 25))
    histogram_lines.append(f"Total samples: {len(response_times)}")

    # Show outlier information if relevant
    if std_dev > 0:
        outlier_threshold = mean_time + 2 * std_dev
        outliers = [rt for rt in response_times if rt > outlier_threshold]
        if outliers:
            histogram_lines.append(
                f"Outliers (>2σ): {len(outliers)} requests ({len(outliers)/len(response_times)*100:.1f}%)"
            )

    return "\n".join(histogram_lines)


def create_percentile_distribution(response_times: List[float], width: int = 50) -> str:
    """Create a percentile-based distribution view for low-variation data."""
    if not response_times:
        return "No response time data available."

    if len(response_times) < 10:
        return "Not enough data points for percentile distribution (need at least 10)."

    # Calculate percentiles
    percentiles = [0, 5, 10, 25, 50, 75, 90, 95, 99, 100]
    percentile_values = []

    sorted_times = sorted(response_times)
    for p in percentiles:
        if p == 0:
            value = sorted_times[0]
        elif p == 100:
            value = sorted_times[-1]
        else:
            index = (p / 100) * (len(sorted_times) - 1)
            if index.is_integer():
                value = sorted_times[int(index)]
            else:
                lower = sorted_times[int(index)]
                upper = sorted_times[int(index) + 1]
                value = lower + (upper - lower) * (index - int(index))
        percentile_values.append(value)

    # Create visualization
    lines = []
    lines.append("Percentile Distribution:")
    lines.append("-" * (width + 15))

    min_val = min(percentile_values)
    max_val = max(percentile_values)
    range_span = max_val - min_val

    for i, (p, value) in enumerate(zip(percentiles, percentile_values)):
        # Calculate position on the scale
        if range_span > 0:
            position = int(((value - min_val) / range_span) * width)
        else:
            position = 0

        # Create visualization bar
        bar = " " * position + "●"

        # Format the line
        if range_span < 1.0:
            value_str = f"{value:.4f}s"
        else:
            value_str = f"{value:.3f}s"

        line = f"P{p:3d}: {value_str:>10} |{bar:<{width+1}}"
        lines.append(line)

    return "\n".join(lines)


def print_results(
    analysis: Dict[str, Any],
    total_time: float,
    response_times: List[float] = None,
    show_histogram: bool = True,
    histogram_bins: int = 20,
):
    """Print formatted performance test results."""
    print("\n" + "=" * 60)
    print("PERFORMANCE TEST RESULTS")
    print("=" * 60)

    # Basic stats
    print(f"Total Requests: {analysis['total_requests']}")
    print(
        f"Successful: {analysis['successful_requests']} ({analysis['success_rate']:.1f}%)"
    )
    print(f"Failed: {analysis['failed_requests']}")
    print(f"Total Time: {total_time:.2f} seconds")
    print(f"Requests/Second: {analysis['total_requests']/total_time:.2f}")

    # Response time stats
    if "response_time_stats" in analysis:
        stats = analysis["response_time_stats"]
        print(f"\nResponse Time Statistics (seconds):")
        print(f"  Min:    {stats['min']:.3f}")
        print(f"  Max:    {stats['max']:.3f}")
        print(f"  Mean:   {stats['mean']:.3f}")
        print(f"  Median: {stats['median']:.3f}")
        print(f"  Std Dev: {stats['std_dev']:.3f}")
        print(f"  95th percentile: {stats['p95']:.3f}")
        print(f"  99th percentile: {stats['p99']:.3f}")

        # Add histogram if response times are provided
        if response_times and show_histogram:
            print(
                f"\n{create_response_time_histogram(response_times, bins=histogram_bins)}"
            )
            print(f"\n{create_percentile_distribution(response_times)}")

    # Status codes
    if analysis["status_codes"]:
        print(f"\nStatus Code Distribution:")
        for code, count in sorted(analysis["status_codes"].items()):
            print(f"  {code}: {count} requests")

    # Response sizes
    if "response_size_stats" in analysis:
        sizes = analysis["response_size_stats"]
        print(f"\nResponse Size Statistics:")
        print(f"  Min:    {sizes['min_bytes']} bytes")
        print(f"  Max:    {sizes['max_bytes']} bytes")
        print(f"  Mean:   {sizes['mean_bytes']:.0f} bytes")
        print(f"  Median: {sizes['median_bytes']:.0f} bytes")

    # Errors
    if analysis["error_types"]:
        print(f"\nError Types:")
        for error_type, count in analysis["error_types"].items():
            print(f"  {error_type}: {count} occurrences")


def main():
    """Main function with command line argument parsing."""
    parser = argparse.ArgumentParser(description="Performance test for API endpoints")
    parser.add_argument(
        "--endpoint", "-e", default=ENDPOINT, help="API endpoint to test"
    )
    parser.add_argument(
        "--count", "-c", type=int, default=CALL_COUNT, help="Number of requests to make"
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Request timeout in seconds",
    )
    parser.add_argument(
        "--concurrent", action="store_true", help="Run requests concurrently"
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="Number of concurrent threads (only with --concurrent)",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress progress output"
    )
    parser.add_argument(
        "--json-output", action="store_true", help="Output results in JSON format"
    )
    parser.add_argument(
        "--no-histogram", action="store_true", help="Skip the response time histogram"
    )
    parser.add_argument(
        "--histogram-bins",
        type=int,
        default=20,
        help="Number of bins for the histogram (default: 20)",
    )

    args = parser.parse_args()

    if args.count <= 0:
        print("Error: Request count must be positive")
        sys.exit(1)

    if args.concurrent and args.threads <= 0:
        print("Error: Thread count must be positive")
        sys.exit(1)

    # Create tester instance
    tester = PerformanceTester(args.endpoint, args.timeout)

    # Run the test
    start_time = time.time()
    if args.concurrent:
        results = tester.run_concurrent_test(args.count, args.threads, not args.quiet)
    else:
        results = tester.run_sequential_test(args.count, not args.quiet)
    end_time = time.time()

    total_time = end_time - start_time

    # Analyze results
    analysis = analyze_results(results)

    # Extract response times for histogram
    successful_results = [r for r in results if r.success]
    response_times = [r.response_time for r in successful_results]

    # Output results
    if args.json_output:
        analysis["total_time"] = total_time
        print(json.dumps(analysis, indent=2))
    else:
        print_results(
            analysis,
            total_time,
            response_times,
            not args.no_histogram,
            args.histogram_bins,
        )


if __name__ == "__main__":
    main()
