"""End-to-end checks against a local HTTP server, without internet access."""

import contextlib
import http.server
import pathlib
import subprocess
import sys
import threading
import time
import unittest


SCRIPT = pathlib.Path(__file__).with_name("speed.py")


@contextlib.contextmanager
def test_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            server = self.server
            server.requests += 1
            server.active += 1
            server.max_active = max(server.max_active, server.active)
            body = b"x" * (65536 + server.requests)
            try:
                if self.path == "/error":
                    self.send_error(503)
                    return
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/file")
                    self.end_headers()
                    return
                self.send_response(200)
                if self.path == "/chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                elif self.path != "/no-length":
                    expected = len(body) + (100 if self.path == "/truncated" else 0)
                    self.send_header("Content-Length", str(expected))
                self.end_headers()
                if self.path == "/slow":
                    time.sleep(0.15)
                if self.path == "/chunked":
                    self.wfile.write(f"{len(body):X}\r\n".encode() + body + b"\r\n0\r\n\r\n")
                else:
                    self.wfile.write(body[:100])
                    self.wfile.flush()
                    time.sleep(0.01)
                    self.wfile.write(body[100:])
                self.wfile.flush()
                server.bytes_sent += len(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                server.active -= 1

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = 0
    server.active = 0
    server.max_active = 0
    server.bytes_sent = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_script(url, *options):
    return subprocess.run(
        [sys.executable, str(SCRIPT), url, *options],
        capture_output=True,
        text=True,
        timeout=10,
    )


class SpeedTests(unittest.TestCase):
    def test_reads_ten_complete_responses_sequentially_and_calculates_speed(self):
        with test_server() as (server, url):
            result = run_script(url + "/file")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(server.requests, 10)
            self.assertEqual(server.max_active, 1)
            lines = result.stdout.splitlines()
            downloaded = int(next(line for line in lines if line.startswith("Total bytes:")).split()[2])
            total_time = float(next(line for line in lines if line.startswith("Total time:")).split()[2])
            average = float(next(line for line in lines if line.startswith("Average time:")).split()[2])
            speed = float(next(line for line in lines if line.startswith("Download speed:")).split()[2])
            self.assertEqual(downloaded, server.bytes_sent)
            self.assertGreaterEqual(total_time, 0.1)
            self.assertAlmostEqual(average, total_time / 10, places=5)
            self.assertAlmostEqual(speed, downloaded / total_time / 1024**2, delta=0.02)

    def test_counts_actual_bytes_without_content_length_or_with_chunked_encoding(self):
        for path in ("/no-length", "/chunked"):
            with self.subTest(path=path), test_server() as (server, url):
                result = run_script(url + path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(server.requests, 10)
                self.assertIn(f"Total bytes: {server.bytes_sent}\n", result.stdout)

    def test_follows_redirects(self):
        with test_server() as (server, url):
            result = run_script(url + "/redirect")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(server.requests, 20)
            self.assertIn(f"Total bytes: {server.bytes_sent}\n", result.stdout)

    def test_http_error_and_truncated_body_fail_without_success_summary(self):
        for path in ("/error", "/truncated"):
            with self.subTest(path=path), test_server() as (server, url):
                result = run_script(url + path)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(server.requests, 1)
                self.assertIn("Request 1/10 failed:", result.stderr)
                self.assertNotIn("Download speed:", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_body_timeout_fails(self):
        with test_server() as (server, url):
            result = run_script(url + "/slow", "--timeout", "0.03")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(server.requests, 1)
            self.assertNotIn("Download speed:", result.stdout)

    def test_excessive_timeout_reports_error_without_traceback(self):
        result = run_script("http://127.0.0.1:1", "--timeout", "1e308")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Request 1/10 failed:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_rejects_invalid_arguments(self):
        for args in (("file:///etc/hosts",), ("http://",), ("https://example.com", "--timeout", "0"),
                     ("https://example.com", "--timeout", "nan")):
            with self.subTest(args=args):
                result = run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
