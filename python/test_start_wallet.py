"""The script that serves the wallet to a browser on a computer (scripts/start_wallet.py)."""
import http.client
import importlib.util
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "start_wallet.py"
spec = importlib.util.spec_from_file_location("start_wallet", SCRIPT)
start_wallet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start_wallet)


def package(folder: Path) -> Path:
    """A folder shaped like a wallet package, with two files that are not part of one."""
    folder.mkdir()
    (folder / "app").mkdir()
    (folder / "app" / "index.html").write_text("<!doctype html><title>wallet</title>")
    (folder / "app" / "main.js").write_text("export {};")
    (folder / "vendor").mkdir()
    (folder / "wheels").mkdir()
    (folder / "wheels" / "a.whl").write_bytes(b"PK")
    (folder / "pyscript-ci.toml").write_text('interpreter = "./vendor/x.mjs"\n')
    (folder / "runtime-closure.json").write_text("{}")
    (folder / "start-wallet.py").write_text("# the script itself is not served")
    (folder / "README.txt").write_text("not served")
    return folder


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.outside = Path(self.temp.name).resolve() / "outside.txt"
        self.outside.write_text("secret")
        self.root = package(Path(self.temp.name).resolve() / "wallet")

    def test_names_a_served_file(self):
        self.assertEqual(start_wallet.resolve(self.root, "/app/index.html"), self.root / "app" / "index.html")
        self.assertEqual(start_wallet.resolve(self.root, "/pyscript-ci.toml?x=1"), self.root / "pyscript-ci.toml")
        self.assertEqual(start_wallet.resolve(self.root, "/wheels/a.whl"), self.root / "wheels" / "a.whl")

    def test_names_nothing_outside_what_a_package_holds(self):
        for target in ("/", "/README.txt", "/start-wallet.py", "/app", "/app/", "/app/missing.js", "/vendor",
                       "/../outside.txt", "/app/../../outside.txt", "/app/%2e%2e/%2e%2e/outside.txt",
                       "/app/..%2f..%2foutside.txt", "/app\\..\\..\\outside.txt", "/app/index.html%00.js",
                       "app/index.html", "//app/index.html", "/APP/index.html" if os.name != "nt" else "/nothing"):
            with self.subTest(target=target):
                self.assertIsNone(start_wallet.resolve(self.root, target))

    def test_a_link_out_of_the_folder_is_not_followed(self):
        link = self.root / "app" / "leak.txt"
        try:
            link.symlink_to(self.outside)
        except OSError:
            self.skipTest("this system makes no symbolic links")
        self.assertIsNone(start_wallet.resolve(self.root, "/app/leak.txt"))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = package(Path(self.temp.name).resolve() / "wallet")
        self.server = start_wallet.Server(self.root, 0)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, target, host=None, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.putrequest(method, target, skip_host=True)
        connection.putheader("Host", host if host is not None else f"127.0.0.1:{self.port}")
        connection.endheaders()
        response = connection.getresponse()
        return response, response.read()

    def test_serves_a_file_with_its_type_and_the_isolation_headers(self):
        response, body = self.get("/app/main.js")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"export {};")
        self.assertEqual(response.getheader("Content-Type"), "text/javascript; charset=utf-8")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("Cross-Origin-Opener-Policy"), "same-origin")
        self.assertEqual(response.getheader("Cross-Origin-Embedder-Policy"), "require-corp")

    def test_head_sends_no_body(self):
        response, body = self.get("/app/main.js", method="HEAD")
        self.assertEqual((response.status, body, response.getheader("Content-Length")), (200, b"", "10"))

    def test_the_root_goes_to_the_start_page(self):
        response, _ = self.get("/")
        self.assertEqual(response.status, 307)
        self.assertEqual(response.getheader("Location"), f"http://127.0.0.1:{self.port}/app/index.html")

    def test_localhost_is_sent_on_to_the_one_address(self):
        response, _ = self.get("/app/index.html?a=b", host=f"localhost:{self.port}")
        self.assertEqual(response.status, 307)
        self.assertEqual(response.getheader("Location"), f"http://127.0.0.1:{self.port}/app/index.html?a=b")

    def test_another_host_is_refused(self):
        for host in ("wallet.example.test", f"wallet.example.test:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", ""):
            with self.subTest(host=host):
                response, body = self.get("/app/index.html", host=host)
                self.assertEqual(response.status, 421)
                self.assertNotIn(b"wallet</title>", body)

    def test_what_is_not_in_a_package_is_not_found(self):
        for target in ("/README.txt", "/start-wallet.py", "/app/", "/app/../README.txt", "/nothing"):
            with self.subTest(target=target):
                self.assertEqual(self.get(target)[0].status, 404)

    def test_other_methods_are_refused(self):
        response, _ = self.get("/app/index.html", method="POST")
        self.assertEqual(response.status, 501)

    def test_it_listens_on_this_computer_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


class MainTest(unittest.TestCase):
    def test_a_folder_that_is_not_a_package_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(start_wallet.main(["--root", folder, "--no-browser"]), 2)

    def test_a_taken_port_stops_the_script(self):
        with tempfile.TemporaryDirectory() as folder:
            root = package(Path(folder).resolve() / "wallet")
            holder = socket.socket()
            self.addCleanup(holder.close)
            try:
                holder.bind(("127.0.0.1", start_wallet.PORT))
            except OSError:
                self.skipTest(f"port {start_wallet.PORT} is already in use on this machine")
            holder.listen(1)
            self.assertEqual(start_wallet.main(["--root", str(root), "--no-browser"]), 1)


if __name__ == "__main__":
    unittest.main()
