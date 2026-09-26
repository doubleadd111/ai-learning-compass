"""运行计划质量评测。默认只检查场景；--live 才调用付费 API。"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from dotenv import load_dotenv

from evaluation.scenarios import Scenario, build_scenarios
from models import StudyPlan
from planner import GenerationMetrics, PlanGenerationError, _get_client, generate_study_plan


OUTPUT_DIR = Path(__file__).parents[1] / ".learning_compass" / "evaluations"
VAGUE_DELIVERABLES = {"掌握知识", "理解知识", "完成学习", "学习笔记"}
PILOT_SCENARIO_IDS = (
    "short-daily",
    "goal2-days3-minutes45",
    "long-weekly",
    "limited-english",
)


def select_scenarios(limit: int, *, pilot: bool = False) -> list[Scenario]:
    scenarios = build_scenarios()
    if pilot:
        by_id = {scenario.id: scenario for scenario in scenarios}
        return [by_id[scenario_id] for scenario_id in PILOT_SCENARIO_IDS]
    if not 1 <= limit <= len(scenarios):
        raise ValueError(f"--limit 必须在 1 到 {len(scenarios)} 之间")
    return scenarios[:limit]


def inspect_plan(plan: StudyPlan) -> dict[str, int]:
    tasks = [task for week in plan.weekly_plans for task in week.tasks]
    titles = [task.title.strip() for task in tasks]
    return {
        "task_count": len(tasks),
        "duplicate_title_count": len(titles) - len(set(titles)),
        "vague_deliverable_count": sum(
            task.deliverable.strip() in VAGUE_DELIVERABLES for task in tasks
        ),
    }


def run_live(limit: int, output_dir: Path = OUTPUT_DIR, *, pilot: bool = False) -> Path:
    scenarios = select_scenarios(limit, pilot=pilot)
    output_dir.mkdir(parents=True, exist_ok=True)
    # 评测额度可控：SDK 不再暗中重试；每组只由 planner 的两次尝试控制。
    client = _get_client().with_options(max_retries=0)
    rows = []
    manual_rows = []
    for scenario in scenarios:
        measured: list[GenerationMetrics] = []
        try:
            plan = generate_study_plan(
                scenario.profile, client=client, on_metrics=measured.append
            )
            quality = inspect_plan(plan)
            rows.append(
                {
                    "scenario": scenario.id,
                    "passed": True,
                    "profile": scenario.profile.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "quality": quality,
                    "metrics": measured[0].__dict__ if measured else None,
                }
            )
            for week in plan.weekly_plans:
                for task in week.tasks:
                    manual_rows.append(
                        {
                            "scenario": scenario.id,
                            "week": week.week_number,
                            "day": task.day,
                            "title": task.title,
                            "description": task.description,
                            "duration_minutes": task.duration_minutes,
                            "deliverable": task.deliverable,
                            "可执行性评分_1到5": "",
                            "先修顺序合理_是或否": "",
                            "备注": "",
                        }
                    )
        except PlanGenerationError:
            rows.append(
                {
                    "scenario": scenario.id,
                    "passed": False,
                    "profile": scenario.profile.model_dump(mode="json"),
                    "plan": None,
                    "quality": None,
                    "metrics": measured[0].__dict__ if measured else None,
                }
            )

    successful = [row for row in rows if row["passed"]]
    timed = [
        row["metrics"]["latency_ms"]
        for row in rows
        if row["metrics"] is not None
    ]
    errors = Counter(
        row["metrics"]["error_category"]
        for row in rows
        if row["metrics"] is not None and row["metrics"]["error_category"]
    )
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sdk_max_retries": 0,
        "scenario_count": len(rows),
        "passed_count": len(successful),
        "constraint_pass_rate": round(len(successful) / len(rows), 3) if rows else None,
        "median_latency_ms": median(timed) if timed else None,
        "input_tokens": sum(
            row["metrics"]["input_tokens"] or 0
            for row in rows if row["metrics"] is not None
        ),
        "output_tokens": sum(
            row["metrics"]["output_tokens"] or 0
            for row in rows if row["metrics"] is not None
        ),
        "error_categories": dict(errors),
        "results": rows,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = output_dir / f"report-{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    manual_path = output_dir / f"human-review-{stamp}.csv"
    with manual_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "scenario", "week", "day", "title", "description", "duration_minutes",
                "deliverable",
                "可执行性评分_1到5", "先修顺序合理_是或否", "备注",
            ],
        )
        writer.writeheader()
        writer.writerows(manual_rows)
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="评测 Python 入门学习计划")
    parser.add_argument("--live", action="store_true", help="真实请求 DeepSeek，会消耗 API 额度")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=int, default=1, help="真实请求的场景数，最多 30")
    selection.add_argument(
        "--pilot", action="store_true", help="选择 4 个不同限制的代表性场景；需加 --live 才调用 API"
    )
    args = parser.parse_args()
    scenarios = build_scenarios()
    try:
        selected = select_scenarios(args.limit, pilot=args.pilot)
    except ValueError as error:
        parser.error(str(error))
    if not args.live:
        if args.pilot:
            print(f"代表性小样本：{', '.join(scenario.id for scenario in selected)}")
            print("仅预览场景；加 --live 后才会调用模型，最多 4 组、每组可能重试一次。")
        else:
            print(f"已验证 {len(scenarios)} 个场景；使用 --live 才会调用模型。")
        return
    load_dotenv()
    if not os.getenv("DEEPSEEK_API_KEY"):
        parser.error("未找到 DEEPSEEK_API_KEY，请先配置 .env")
    print(f"评测报告：{run_live(args.limit, pilot=args.pilot)}")


if __name__ == "__main__":
    main()
