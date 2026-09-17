"""The browser shell's transport: the engine's frames over Server-Sent
Events, commands over POST, a token on both, loopback only.

Run: python tests/test_web.py
"""

import http.client
import json
import urllib.error
import urllib.request

import paths  # noqa: F401  - puts the project root on sys.path

from gpo_macro.config import AppConfig, ConfigStore
from gpo_macro.web import start_server


def post(url: str, token: str, body: dict, origin: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(f"{url}/cmd", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> int:
    store = ConfigStore(paths.scratch() / "web-settings.json", AppConfig())
    server, engine, url, token = start_server(store, host="127.0.0.1", port=0)
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1", "the shell must not listen beyond this machine"
        assert len(token) >= 32

        # The page carries the token, and the static files come with it.
        page = urllib.request.urlopen(f"{url}/", timeout=10).read().decode("utf-8")
        assert "<html" in page.lower() and token in page, "page lacks the token"
        for name in ("app.css", "app.js"):
            body = urllib.request.urlopen(f"{url}/{name}", timeout=10).read()
            assert len(body) > 100, name
        try:
            urllib.request.urlopen(f"{url}/../main.py", timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, exc.code
        else:
            raise AssertionError("served a file outside the web folder")

        # A command without the token, or from another origin, is refused.
        for kwargs, code in (({"token": ""}, 401), ({"token": token, "origin": "http://evil.test"}, 403)):
            try:
                post(url, body={"cmd": "ping"}, **kwargs)
            except urllib.error.HTTPError as exc:
                assert exc.code == code, (kwargs, exc.code)
            else:
                raise AssertionError(f"accepted {kwargs}")

        reply = post(url, token, {"id": 1, "cmd": "ping"})
        assert reply["ok"] and reply["id"] == 1 and "pong" in reply["result"], reply
        before = store.config.controller.bar_lead
        reply = post(url, token, {"cmd": "set_config", "patch": {"controller": {"bar_lead": before + 0.01}}})
        assert reply["ok"] and abs(reply["result"]["config"]["controller"]["bar_lead"] - before - 0.01) < 1e-6
        post(url, token, {"cmd": "set_config", "patch": {"controller": {"bar_lead": before}}})

        # The stream: hello first, then live ticks.
        conn = http.client.HTTPConnection(host, port, timeout=10)
        conn.request("GET", f"/events?token={token}")
        response = conn.getresponse()
        assert response.status == 200, response.status
        assert response.getheader("Content-Type", "").startswith("text/event-stream")
        kinds = []
        while len(kinds) < 3:
            line = response.readline().decode("utf-8")
            if line.startswith("data:"):
                kinds.append(json.loads(line[5:])["t"])
        assert kinds[0] == "hello" and "tick" in kinds, kinds
        conn.close()
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/events")
            assert conn.getresponse().status == 401, "stream served without the token"
        finally:
            conn.close()
    finally:
        server.shutdown()
        engine.shutdown()

    print("all web checks passed")
    return 0


def test_web() -> None:
    """pytest entry: the browser shell's transport. `python tests/test_web.py` runs the same."""
    main()


if __name__ == "__main__":
    raise SystemExit(main())
