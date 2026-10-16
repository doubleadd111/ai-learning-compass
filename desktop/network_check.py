"""No real keys or external URLs: exercise the frozen gateway against loopback."""
import json
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from demo_sample import sample_learning_plan
from desktop import VERSION
from desktop.gateway import ModelGateway
from desktop.models import DesktopProfile, ModelConnection


def run_anonymous_check(directory, network_mode):
    """Fixed public metadata endpoint; no authentication or model generation."""
    from time import perf_counter
    import httpx
    from desktop.diagnostics import ConnectionDiagnostic, classify
    from desktop.network import CancellableTransport, RequestControl
    control = RequestControl()
    control.begin(10)
    started = perf_counter()
    category, names = "success", []
    try:
        with httpx.Client(transport=CancellableTransport(control, network_mode=network_mode), trust_env=False) as client:
            client.get("https://api.deepseek.com/models")
    except Exception as error:
        category, names = classify(error)
    diagnostic = ConnectionDiagnostic(operation="anonymous_models", stage=control.stage,
        elapsed_ms=round((perf_counter()-started)*1000), http_status=control.response_status,
        category=category, exception_types=names, network_mode=network_mode)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "anonymous-check.json").write_text(diagnostic.model_dump_json(indent=2), encoding="utf-8")
    return 0 if category == "success" else 1


def run_network_check(url, directory):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("网络自检仅允许127.0.0.1的本机HTTP模拟服务")
    results = []
    profile, _plan = sample_learning_plan()
    profile = DesktopProfile.model_validate(profile.model_dump())
    def exercise(protocol, mode):
        config = ModelConnection(name="隔离网络自检", provider="DeepSeek", base_url=url, model="loopback-model", api_type=protocol, output_format="json_schema", network_mode=mode)
        gateway = ModelGateway(config, "loopback-placeholder-not-a-real-key")
        try:
            for operation in ("models", "connection_test", "planning_clarification", "study_plan"):
                try:
                    if operation == "models":
                        assert gateway.list_models() == ["loopback-model"]
                    elif operation == "connection_test":
                        gateway.test()
                    elif operation == "planning_clarification":
                        from desktop.planning import PlanningDraft
                        draft = PlanningDraft(profile=profile, start_date=date.today(),
                                              weekdays=list(range(profile.days_per_week)), quick=True, connection=config)
                        turn, _event = gateway.clarify(draft)
                        assert turn.decision == "outline"
                        assert _event["metrics"]["summary_excerpted_count"] == 3
                        assert sum(len(p.summary) for p in turn.outline.points) == 100
                        assert all("完整概略（标题为原文摘录）" in p.content for p in turn.outline.points)
                    else:
                        gateway.generate(profile)
                    success = True
                except Exception:
                    success = False
                results.append(dict(protocol=protocol, operation=operation, network_mode=mode, success=success,
                                    diagnostic=gateway.last_diagnostic.model_dump() if gateway.last_diagnostic else None))
        finally:
            gateway.close()
    # Same synchronous→async bridge as GUI background work, not HTTP MockTransport.
    with ThreadPoolExecutor(max_workers=1) as pool:
        for mode in ("direct", "system"):
            for protocol in ("responses", "chat"):
                pool.submit(exercise, protocol, mode).result()
    report = dict(version=VERSION, success=all(r["success"] for r in results), results=results)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "network-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1
