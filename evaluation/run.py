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

from evaluation.scenarios import build_scenarios
from models import StudyPlan
from planner import GenerationMetrics, PlanGenerationError, generate_study_plan


OUTPUT_DIR = Path(__file__).parents[1] / ".learning_compass" / "evaluations"
VAGUE_DELIVERABLES = {"掌握知识", "理解知识", "完成学习", "学习笔记"}


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


def run_live(limit: int, output_dir: Path = OUTPUT_DIR) -> Path:
    scenarios = build_scenarios()[:limit]
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    manual_rows = []
    for scenario in scenarios:
        measured: list[GenerationMetrics] = []
        try:
            plan = generate_study_plan(scenario.profile, on_metrics=measured.append)
            quality = inspect_plan(plan)
            rows.append(
                {
                    "scenario": scenario.id,
                    "passed": True,
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
                "scenario", "week", "day", "title", "description", "deliverable",
                "可执行性评分_1到5", "先修顺序合理_是或否", "备注",
            ],
        )
        writer.writeheader()
        writer.writerows(manual_rows)
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="评测 Python 入门学习计划")
    parser.add_argument("--live", action="store_true", help="真实请求 DeepSeek，会消耗 API 额度")
    parser.add_argument("--limit", type=int, default=1, help="真实请求的场景数，最多 30")
    args = parser.parse_args()
    scenarios = build_scenarios()
    if not args.live:
        print(f"已验证 {len(scenarios)} 个场景；使用 --live 才会调用模型。")
        return
    load_dotenv()
    if not os.getenv("DEEPSEEK_API_KEY"):
        parser.error("未找到 DEEPSEEK_API_KEY，请先配置 .env")
    if not 1 <= args.limit <= len(scenarios):
        parser.error("--limit 必须在 1 到 30 之间")
    print(f"评测报告：{run_live(args.limit)}")


if __name__ == "__main__":
    main()
