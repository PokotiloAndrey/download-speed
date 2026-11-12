"""Measure download speed with ten sequential HTTP(S) requests."""

import argparse
from http.client import HTTPException
import math
import sys
from time import perf_counter
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


REQUEST_COUNT = 10
CHUNK_SIZE = 64 * 1024
BYTES_PER_MIB = 1024 * 1024


def download(url: str, timeout: float) -> tuple[int, float]:
    request = Request(url, headers={
        "User-Agent": "download-speed/1.0",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    })
    downloaded = 0
    started = perf_counter()
    with urlopen(request, timeout=timeout) as response:
        while chunk := response.read(CHUNK_SIZE):
            downloaded += len(chunk)
        # Проверяем обрыв ответа с известным размером.
        expected = response.headers.get("Content-Length")
        if expected is not None and not response.headers.get("Transfer-Encoding"):
            if downloaded != int(expected):
                raise ValueError(f"incomplete response: expected {expected} bytes, received {downloaded}")
    return downloaded, perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="HTTP(S) URL of a file to download ten times")
    parser.add_argument("--timeout", type=float, default=30, help="socket timeout in seconds (default: 30)")
    args = parser.parse_args()
    try:
        address = urlsplit(args.url)
        valid_url = address.scheme in ("http", "https") and bool(address.hostname)
        address.port  # Проверка диапазона порта.
    except ValueError:
        valid_url = False
    if not valid_url:
        parser.error("url must be an HTTP(S) address with a valid host and port")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be a positive finite number")

    total_bytes = 0
    total_time = 0.0
    for number in range(1, REQUEST_COUNT + 1):
        try:
            downloaded, elapsed = download(args.url, args.timeout)
        except (URLError, OSError, HTTPException, ValueError, OverflowError) as error:
            print(f"Request {number}/{REQUEST_COUNT} failed: {error}", file=sys.stderr)
            return 1
        total_bytes += downloaded
        total_time += elapsed
        print(f"Request {number}/{REQUEST_COUNT}: {downloaded} bytes in {elapsed:.6f} s")

    print(f"\nTotal bytes: {total_bytes}")
    print(f"Total time: {total_time:.6f} s")
    print(f"Average time: {total_time / REQUEST_COUNT:.6f} s")
    print(f"Download speed: {total_bytes / total_time / BYTES_PER_MIB:.6f} MiB/s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nMeasurement interrupted.", file=sys.stderr)
        sys.exit(130)
