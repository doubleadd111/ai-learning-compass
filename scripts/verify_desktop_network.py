"""Source/frozen actual SDK + transport against a local server, zero API calls."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo_sample import sample_learning_plan
from planner import UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS


def incompatible(node):
    if isinstance(node, dict):
        return bool(set(node) & UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS) or any(incompatible(v) for v in node.values())
    if isinstance(node, list):
        return any(incompatible(v) for v in node)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--report", type=Path, default=ROOT / ".learning_compass/qa/network-2026.10.5.json")
    args = parser.parse_args()
    captured = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def send_json(self, payload):
            content = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        def do_GET(self):
            captured.append("models")
            self.send_json(dict(object="list", data=[dict(id="loopback-model", object="model", created=1, owned_by="local")]))
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/responses"):
                operation = body["text"]["format"].get("name", "connection_test")
            else:
                operation = body["response_format"].get("json_schema", {}).get("name", "connection_test")
            captured.append(operation)
            fmt = body.get("text", {}).get("format", body.get("response_format", {}))
            schema = fmt.get("schema", fmt.get("json_schema", {}).get("schema", {}))
            if incompatible(schema):
                content = b'{"error":{"code":"invalid_json_schema","param":"text.format.schema"}}'
                self.send_response(400)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if operation == "connection_test":
                text = '{"ok":true}'
            elif operation == "planning_clarification":
                text = json.dumps(dict(decision="outline", questions=[], outline=dict(points=[
                    dict(id=f"stage-{i}", summary="从基础概念开始，通过短练习巩固关键步骤，并留下可以检查的学习产出。",
                         objective="完成基础学习", content="运行并修改最小练习", deliverable="可检查的练习", rationale="根据可用时间安排")
                    for i in range(3)], assumptions=["使用已有学习设备"])), ensure_ascii=False)
            else:
                text = sample_learning_plan()[1].model_dump_json()
            usage = dict(input_tokens=10, output_tokens=20, total_tokens=30)
            if self.path.endswith("/responses"):
                self.send_json(dict(id="resp_local", object="response", created_at=1, model="loopback-model", status="completed",
                    output=[dict(type="message", id="msg_local", role="assistant", status="completed",
                                 content=[dict(type="output_text", text=text, annotations=[])])], usage=usage))
            else:
                self.send_json(dict(id="chat_local", object="chat.completion", created=1, model="loopback-model",
                    choices=[dict(index=0, finish_reason="stop", message=dict(role="assistant", content=text))],
                    usage=dict(prompt_tokens=10, completion_tokens=20, total_tokens=30)))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="learning-network-") as directory:
            command = [str(args.executable.resolve())] if args.executable else [sys.executable, str(ROOT / "run_desktop.py")]
            command += ["--network-self-test", f"http://127.0.0.1:{server.server_port}/v1", "--data-dir", directory]
            environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
            # Controlled local HTTP proxy for system mode; no external proxy is used.
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                environment[key] = f"http://127.0.0.1:{server.server_port}"
                environment.pop(key.lower(), None)
            environment["NO_PROXY"] = ""
            environment.pop("no_proxy", None)
            if args.executable:
                environment["PATH"] = str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32")
                environment.pop("PYTHONPATH", None)
                environment.pop("VIRTUAL_ENV", None)
            result = subprocess.run(command, env=environment, cwd=ROOT, timeout=120)
            report = json.loads((Path(directory) / "network-check.json").read_text(encoding="utf-8"))
            report.update(frozen=bool(args.executable), exit_code=result.returncode, requests_reached_server=captured)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["success"] else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
