"""Mountable WSGI app. authenticate(environ) verifies credentials and returns Principal."""
import json
import re
from http import HTTPStatus
from uuid import UUID
from .report import markdown
from .store import Conflict


def application(store, authenticate):
    def handle(env):
        actor = authenticate(env)
        route = re.fullmatch(r"/v1/postmortems/([^/]+)(?:/(markdown|revisions/[1-9]\d*|review|rectify|approve))?", env["PATH_INFO"])
        if not route:
            raise LookupError
        iid, command = str(UUID(route[1])), route[2]
        if env["REQUEST_METHOD"] == "GET" and command not in {"review", "rectify", "approve"}:
            version = int(command.split("/")[1]) if command and command.startswith("revisions/") else None
            report = store.get(actor, iid, version)
            if command == "markdown":
                return 200, "text/markdown", markdown(report)
            return 200, "application/json", report
        if env["REQUEST_METHOD"] != "POST" or command not in {"review", "rectify", "approve"}:
            return 405, "application/json", {"error":"method_not_allowed"}
        match = re.fullmatch(r'"([1-9]\d*)"', env.get("HTTP_IF_MATCH", ""))
        if not match:
            return 428, "application/json", {"error":"expected_version_required"}
        length = int(env.get("CONTENT_LENGTH") or 0)
        if not 0 <= length <= 1_000_000:
            return 413, "application/json", {"error":"body_too_large"}
        body = json.loads(env["wsgi.input"].read(length) or b"{}")
        if not isinstance(body, dict) or set(body) - {"sections", "reason"}:
            raise ValueError
        report = store.write(actor, iid, int(match[1]), command, **body)
        return 201, "application/json", report

    def app(env, start_response):
        try:
            code, mime, body = handle(env)
        except PermissionError:
            code, mime, body = 403, "application/json", {"error":"forbidden"}
        except LookupError as exc:
            # Malformed claim keys are input errors.
            code = 422 if isinstance(exc, (KeyError, IndexError)) else 404
            mime, body = "application/json", {"error":HTTPStatus(code).phrase}
        except Conflict:
            code, mime, body = 409, "application/json", {"error":"revision_conflict"}
        except (ValueError, TypeError, AttributeError):
            code, mime, body = 422, "application/json", {"error":"invalid_input"}
        encoded = (json.dumps(body, ensure_ascii=False) if mime == "application/json" else body).encode()
        start_response(f"{code} {HTTPStatus(code).phrase}", [("Content-Type",mime+"; charset=utf-8"),
                       ("Cache-Control","no-store"), ("Content-Length",str(len(encoded)))])
        return [encoded]
    return app
