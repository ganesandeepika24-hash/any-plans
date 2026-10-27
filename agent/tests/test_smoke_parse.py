"""scripts/smoke_parse.py against a local fake agent and fake PostgREST (no real hosts)."""

import importlib.util
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_parse.py"
spec = importlib.util.spec_from_file_location("smoke_parse", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

TOKEN = "eyJ.fake-user-token.sig"
QUERY_ID = "6f1c2b8e-3a4d-4e5f-9a0b-1c2d3e4f5a6b"


class Fake:
    """One server playing both the agent and Supabase's REST API."""

    def __init__(self):
        self.parse_status = 200
        self.parse_body: object = {
            "status": "ok",
            "reason": None,
            "message": None,
            "query_id": QUERY_ID,
            "filters": {"categories": ["Music"]},
            "embedding_text": "jazz",
            "notes": [],
            "trace_id": "a" * 32,
        }
        self.row = {
            "id": QUERY_ID,
            "status": "ok",
            "attempts": [{"provider": "groq", "outcome": "ok"}],
        }
        self.row_after = 0  # GETs that return [] before the row appears
        self.rest_status = 200
        self.requests: list[dict] = []


@pytest.fixture
def fake():
    state = Fake()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body):
            raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            state.requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            self._send(state.parse_status, state.parse_body)

        def do_GET(self):
            state.requests.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                }
            )
            if state.rest_status != 200:
                self._send(state.rest_status, {"message": "permission denied for table queries"})
                return
            gets = sum(1 for r in state.requests if r["method"] == "GET")
            self._send(200, [] if gets <= state.row_after else [state.row])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def env_for(fake, **overrides):
    env = {
        "AGENT_URL": fake.url,
        "SUPABASE_URL": fake.url,
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
        "SUPABASE_USER_TOKEN": TOKEN,
    }
    return {k: v for k, v in (env | overrides).items() if v is not None}


def run(argv, env, monkeypatch=None):
    out, err = io.StringIO(), io.StringIO()
    code = smoke.main(argv, env, out, err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)


def test_ok_prints_the_response_and_the_row(fake):
    code, out, err = run(["jazz in soho tonight"], env_for(fake))
    assert code == 0 and err == ""
    assert "POST /parse -> HTTP 200" in out
    assert '"embedding_text": "jazz"' in out
    assert f"queries row {QUERY_ID}:" in out and '"provider": "groq"' in out
    assert "attempts (" not in out  # only printed when resting

    post, get = fake.requests
    assert post["path"] == "/parse" and post["body"] == {"query": "jazz in soho tonight"}
    assert post["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert get["path"] == f"/rest/v1/queries?id=eq.{QUERY_ID}&select=%2A"
    assert get["headers"]["apikey"] == "sb_publishable_test"
    assert get["headers"]["authorization"] == f"Bearer {TOKEN}"


def test_ai_resting_prints_the_attempts(fake):
    fake.parse_body = fake.parse_body | {
        "status": "ai_resting",
        "reason": "providers",
        "message": "AI search is resting; filters still work",
        "filters": None,
    }
    fake.row = {
        "id": QUERY_ID,
        "status": "ai_resting",
        "attempts": [
            {"provider": "groq", "model": "openai/gpt-oss-20b", "outcome": "http_429"},
            {
                "provider": "cloudflare",
                "model": "@cf/openai/gpt-oss-20b",
                "outcome": "invalid_json",
            },
        ],
    }
    code, out, _ = run(["jazz tonight"], env_for(fake))
    assert code == 0
    attempts = out.split("attempts (providers):", 1)[1]
    assert [a["outcome"] for a in json.loads(attempts)] == ["http_429", "invalid_json"]


def test_the_row_is_retried_until_the_agent_has_written_it(fake):
    fake.row_after = 2
    code, out, _ = run(["jazz tonight"], env_for(fake))
    assert code == 0 and f"queries row {QUERY_ID}:" in out
    assert sum(1 for r in fake.requests if r["method"] == "GET") == 3


def test_a_row_that_never_appears_says_why(fake):
    fake.row_after = 99
    code, _, err = run(["jazz tonight"], env_for(fake))
    assert code == 1 and "not found after 5 tries" in err and "012" in err


def test_a_refused_row_read_shows_the_error(fake):
    fake.rest_status = 401
    code, _, err = run(["jazz tonight"], env_for(fake))
    assert code == 1 and "HTTP 401" in err and "permission denied" in err


@pytest.mark.parametrize("status", [401, 422, 503])
def test_a_failed_parse_prints_status_and_body_and_reads_nothing(fake, status):
    fake.parse_status = status
    fake.parse_body = {"detail": "Invalid or missing token"}
    code, out, _ = run(["jazz tonight"], env_for(fake))
    assert code == 1
    assert f"HTTP {status}" in out and "Invalid or missing token" in out
    assert [r["method"] for r in fake.requests] == ["POST"]


def test_no_query_id_means_no_row_read(fake):
    fake.parse_body = {"status": "ok"}
    assert run(["jazz tonight"], env_for(fake))[0] == 0
    assert [r["method"] for r in fake.requests] == ["POST"]


def test_an_unreachable_agent_is_reported():
    env = {
        "AGENT_URL": "http://127.0.0.1:9",
        "SUPABASE_URL": "http://127.0.0.1:9",
        "SUPABASE_PUBLISHABLE_KEY": "k",
        "SUPABASE_USER_TOKEN": TOKEN,
    }
    code, out, _ = run(["jazz"], env)
    assert code == 1 and "HTTP 0" in out and "no response" in out


@pytest.mark.parametrize("name", smoke.ENV)
def test_each_variable_is_required(fake, name):
    code, out, err = run(["jazz"], env_for(fake, **{name: None}))
    assert code == 2 and name in err and fake.requests == []


@pytest.mark.parametrize("argv", [[], [""], ["  "], ["jazz", "--token", TOKEN]])
def test_exactly_one_query_argument(fake, argv):
    code, _, err = run(argv, env_for(fake))
    assert code == 2 and "usage" in err and fake.requests == []


@pytest.mark.parametrize(
    "url",
    ["http://agent.example.com", "ftp://agent.example.com", "agent.example.com"],
    ids=["plain http to a real host", "ftp", "no scheme"],
)
def test_urls_must_be_https_unless_local(fake, url):
    code, _, err = run(["jazz"], env_for(fake, AGENT_URL=url))
    assert code == 2 and "AGENT_URL" in err and fake.requests == []


def test_the_token_is_never_printed(fake):
    fake.parse_status = 401
    fake.parse_body = {"detail": "Invalid or missing token"}
    for overrides in ({}, {"SUPABASE_URL": None}):
        _, out, err = run(["jazz"], env_for(fake, **overrides))
        assert TOKEN not in out and TOKEN not in err


def test_the_script_has_no_secret_settings_or_token_option():
    source = SCRIPT.read_text()
    assert "argparse" not in source and "--token" not in source
    assert "sb_publishable" not in source and "eyJ" not in source
