"""Streamlit 入口：把结构化学习计划展示为一条清晰的学习轨迹。"""

from __future__ import annotations

import os
from html import escape

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from demo_quota import QuotaError, live_generation_available, reserve_generation
from demo_sample import sample_learning_plan
from models import LearningProfile, StudyPlan
from plan_transfer import export_bundle, import_bundle
from planner import GenerationMetrics, PlanGenerationError, generate_study_plan
from progress_store import StudyRepository, TaskProgress
from session_store import SessionRepository
from weekly_review import build_adjusted_plan, build_demo_adjustment, decide_week


load_dotenv()

st.set_page_config(page_title="学习罗盘", page_icon="🧭", layout="wide")


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Noto+Sans+SC:wght@400;500;700;900&display=swap');
        :root { --ink:#102A43; --blue:#176BFF; --mist:#F3F7FC; --mint:#CBF3E0; --coral:#FF8A65; --line:#D7E1EF; }
        .stApp { background: #F8FBFF; color: var(--ink); font-family: 'Noto Sans SC','Microsoft YaHei',sans-serif; }
        .block-container { max-width: 1180px; padding-top: 2.1rem; padding-bottom: 3rem; }
        .hero { background: var(--ink); color: white; padding: 2.4rem 2.5rem 2.15rem; border-radius: 6px; position: relative; overflow: hidden; }
        .hero:after { content:'↗'; position:absolute; right:2.1rem; top:-1.35rem; font: 11rem/1 'DM Mono',monospace; color:rgba(203,243,224,.18); }
        .eyebrow { font:500 .76rem 'DM Mono',monospace; letter-spacing:.13em; color:var(--mint); text-transform:uppercase; }
        .hero h1 { position:relative; margin:.45rem 0 .65rem; font-size:clamp(2.15rem,5vw,4.2rem); line-height:1; letter-spacing:-.06em; }
        .hero p { position:relative; max-width:44rem; color:#DCE8F6; margin:0; font-size:1.04rem; }
        .section-label { font:500 .73rem 'DM Mono',monospace; letter-spacing:.12em; color:#53718D; margin:1.7rem 0 .45rem; }
        [data-testid='stForm'] { background:white; border:1px solid var(--line); border-top:4px solid var(--blue); border-radius:4px; padding:1.25rem 1.3rem .5rem; }
        .stButton>button, [data-testid='stFormSubmitButton']>button { background:var(--blue); color:white; border:0; border-radius:3px; font-weight:700; min-height:2.7rem; }
        .stButton>button:hover, [data-testid='stFormSubmitButton']>button:hover { background:#0F56D6; color:white; }
        .plan-head { border-left:6px solid var(--coral); padding:.2rem 0 .3rem 1rem; margin:1.8rem 0 1rem; }
        .plan-head h2 { margin:0; color:var(--ink); }
        .plan-head p { margin:.35rem 0 0; color:#486581; }
        .tip { background:var(--mint); border-radius:3px; padding:.7rem .9rem; margin:.45rem 0; color:#173D38; }
        .task-card { border-bottom:1px solid var(--line); padding:.8rem 0; }
        .task-card:last-child { border-bottom:0; }
        .task-title { color:var(--ink); font-weight:800; font-size:1.02rem; }
        .task-meta { font:500 .72rem 'DM Mono',monospace; color:#53718D; margin:.18rem 0 .3rem; }
        .deliverable { color:#24564D; font-size:.88rem; }
        [data-testid='stMetric'] { background:white; border:1px solid var(--line); padding:.7rem; border-radius:3px; }
        @media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def plan_to_markdown(plan: StudyPlan) -> str:
    lines = [f"# {plan.title}", "", plan.overview, ""]
    for week in plan.weekly_plans:
        lines.extend([f"## 第 {week.week_number} 周｜{week.theme}", week.milestone, ""])
        for task in week.tasks:
            lines.append(
                f"- 第 {task.day} 天（{task.duration_minutes} 分钟）：**{task.title}**"
            )
            lines.append(f"  - {task.description}")
            lines.append(f"  - 产出：{task.deliverable}")
        lines.append("")
    lines.extend(["## 学习小贴士", *[f"- {tip}" for tip in plan.learning_tips]])
    return "\n".join(lines)


def render_plan(
    plan: StudyPlan,
    profile: LearningProfile,
    repository: StudyRepository | SessionRepository,
    plan_id: str,
    *,
    demo_mode: bool,
    sample_plan_id: str | None,
    invite_code: str,
) -> None:
    progress_by_day = repository.get_progress(plan_id)
    reviews = repository.get_reviews(plan_id)
    active_week = next(
        (week.week_number for week in plan.weekly_plans if week.week_number not in reviews),
        None,
    )
    total_days = profile.plan_weeks * profile.days_per_week
    completed_days = sum(
        item.status == "已完成" for item in progress_by_day.values()
    )
    st.markdown(
        f"""<div class='plan-head'><h2>{escape(plan.title)}</h2>
        <p>{escape(plan.overview)}</p></div>""",
        unsafe_allow_html=True,
    )
    metric_one, metric_two, metric_three = st.columns(3)
    metric_one.metric("计划周期", f"{profile.plan_weeks} 周")
    metric_two.metric("每周节奏", f"{profile.days_per_week} 天")
    metric_three.metric("单日上限", f"{profile.minutes_per_day} 分钟")
    st.progress(completed_days / total_days, text=f"已完成 {completed_days}/{total_days} 个学习日")

    st.markdown("<p class='section-label'>YOUR LEARNING TRAIL</p>", unsafe_allow_html=True)
    for week in plan.weekly_plans:
        with st.expander(
            f"第 {week.week_number} 周 · {week.theme}",
            expanded=week.week_number == active_week,
        ):
            st.caption(f"本周里程碑：{week.milestone}")
            for task in week.tasks:
                current = progress_by_day.get((week.week_number, task.day), TaskProgress())
                with st.container(border=True):
                    st.markdown(
                        f"""<div class='task-card'>
                    <div class='task-title'>第 {task.day} 天 · {escape(task.title)}</div>
                    <div class='task-meta'>{task.duration_minutes} MINUTES</div>
                    <div>{escape(task.description)}</div>
                    <div class='deliverable'>产出：{escape(task.deliverable)}</div>
                    </div>""",
                        unsafe_allow_html=True,
                    )
                    if week.week_number != active_week:
                        if current.status != "未开始":
                            st.caption(
                                f"记录：{current.status} · 实际 {current.actual_minutes} 分钟"
                            )
                        continue
                    form_key = f"progress-{plan_id}-{week.week_number}-{task.day}"
                    with st.form(form_key, border=False):
                        status = st.selectbox(
                            "完成情况",
                            ["未开始", "进行中", "已完成", "延期"],
                            index=["未开始", "进行中", "已完成", "延期"].index(current.status),
                            key=f"{form_key}-status",
                        )
                        actual_minutes = st.number_input(
                            "实际用时（分钟）",
                            min_value=0,
                            max_value=1440,
                            value=current.actual_minutes,
                            step=5,
                            key=f"{form_key}-minutes",
                        )
                        difficulty_options: list[int | str] = ["未评估", 1, 2, 3, 4, 5]
                        difficulty = st.selectbox(
                            "难度（1 最容易，5 最困难）",
                            difficulty_options,
                            index=difficulty_options.index(
                                current.difficulty if current.difficulty is not None else "未评估"
                            ),
                            key=f"{form_key}-difficulty",
                        )
                        note = st.text_area(
                            "遇到的问题或收获",
                            value=current.note,
                            max_chars=500,
                            key=f"{form_key}-note",
                        )
                        saved = st.form_submit_button("保存当天记录")
                    if saved:
                        repository.save_progress(
                            plan_id,
                            week.week_number,
                            task.day,
                            TaskProgress(
                                status=status,
                                actual_minutes=actual_minutes,
                                difficulty=difficulty if isinstance(difficulty, int) else None,
                                note=note.strip(),
                            ),
                        )
                        st.success("记录已保存；下次打开这份计划时仍会保留。")
                        st.rerun()

            if week.week_number in reviews:
                st.success(f"本周复盘：{reviews[week.week_number]}")
            elif week.week_number == active_week:
                ready = all(
                    progress_by_day.get((week.week_number, task.day), TaskProgress()).status
                    in {"已完成", "延期"}
                    for task in week.tasks
                )
                if ready:
                    label = (
                        "完成最终复盘"
                        if week.week_number == profile.plan_weeks
                        else "生成本周复盘并调整后续计划"
                    )
                    if st.button(label, key=f"review-{plan_id}-{week.week_number}"):
                        try:
                            decision = decide_week(profile, week, progress_by_day)
                            if week.week_number < profile.plan_weeks:
                                if demo_mode and plan_id == sample_plan_id:
                                    adjusted = build_demo_adjustment(
                                        plan, profile, week.week_number, decision
                                    )
                                else:
                                    if demo_mode:
                                        reserve_generation(invite_code)
                                    with st.spinner("正在根据本周反馈调整后续任务…"):
                                        adjusted = build_adjusted_plan(
                                            plan,
                                            profile,
                                            week.week_number,
                                            decision,
                                            on_metrics=metrics_recorder(
                                                repository, "adjustment", plan_id
                                            ),
                                        )
                            else:
                                adjusted = None
                            repository.apply_weekly_review(
                                plan_id, week.week_number, decision.summary, adjusted
                            )
                            st.rerun()
                        except (PlanGenerationError, QuotaError, ValueError) as error:
                            st.error(str(error))
                else:
                    st.caption("将本周每一天标记为“已完成”或“延期”后，即可生成复盘。")

    st.markdown("<p class='section-label'>KEEP GOING</p>", unsafe_allow_html=True)
    for tip in plan.learning_tips:
        st.markdown(f"<div class='tip'>✦ {escape(tip)}</div>", unsafe_allow_html=True)

    st.markdown("<p class='section-label'>COPY AS MARKDOWN</p>", unsafe_allow_html=True)
    st.code(plan_to_markdown(plan), language="markdown")
    st.caption("点击代码块右上角的复制图标，即可保存你的计划。")
    saved = repository.load_plan(plan_id)
    if saved is not None:
        st.download_button(
            "下载计划与学习记录（JSON）",
            data=export_bundle(saved, progress_by_day, reviews),
            file_name=f"study-plan-{plan_id[:8]}.json",
            mime="application/json",
            on_click="ignore",
            width="stretch",
        )


def make_profile(
    goal: str,
    current_level: str,
    plan_weeks: int,
    days_per_week: int,
    minutes_per_day: int,
    preferences: str,
) -> LearningProfile:
    return LearningProfile(
        goal=goal.strip(),
        current_level=current_level,  # type: ignore[arg-type]
        plan_weeks=plan_weeks,
        days_per_week=days_per_week,
        minutes_per_day=minutes_per_day,
        preferences=preferences.strip() or "无",
    )


def metrics_recorder(
    repository: StudyRepository | SessionRepository, kind: str, plan_id: str | None = None
):
    def save(metrics: GenerationMetrics) -> None:
        repository.record_generation(
            plan_id=plan_id,
            kind=kind,
            success=metrics.success,
            latency_ms=metrics.latency_ms,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            error_category=metrics.error_category,
        )

    return save


def main() -> None:
    # 只有明确选择 local 才读写本机 SQLite；公开部署缺少配置时安全地展示会话样例。
    demo_mode = os.getenv("APP_MODE", "demo") != "local"
    sample_plan_id: str | None = None
    if demo_mode:
        st.session_state.setdefault("demo_store", {})
        repository = SessionRepository(st.session_state.demo_store)
        if "sample_plan_id" not in st.session_state:
            sample_profile, sample_plan = sample_learning_plan()
            st.session_state.sample_plan_id = repository.save_plan(sample_profile, sample_plan)
        sample_plan_id = st.session_state.sample_plan_id
    else:
        repository = StudyRepository()
    inject_styles()
    st.markdown(
        """<section class='hero'><div class='eyebrow'>LEARNING COMPASS / 01</div>
        <h1>把 Python 学习，变成今天能做。</h1>
        <p>告诉我你想用 Python 完成什么、当前基础和可用时间，我会为你安排不超载的学习轨迹。</p></section>""",
        unsafe_allow_html=True,
    )
    st.markdown("<p class='section-label'>BUILD YOUR PLAN</p>", unsafe_allow_html=True)
    invite_code = ""
    if demo_mode:
        st.info("公开样例可以直接记录进度和体验复盘。真实生成需要邀请码，并受每日总额度限制。")
        if live_generation_available():
            invite_code = st.text_input("真实生成邀请码", type="password")

    with st.form("learning_profile"):
        goal = st.text_input(
            "想用 Python 学会或做出什么？",
            placeholder="例如：从零做出一个 Python 小工具，并理解基础语法",
            help="写得越具体，任务越可执行。",
        )
        left, middle, right = st.columns(3)
        with left:
            current_level = st.selectbox("当前基础", ["零基础", "入门", "已有基础"])
            plan_weeks = st.number_input("计划周期（周）", min_value=1, max_value=8, value=4)
        with middle:
            days_per_week = st.number_input("每周学习天数", min_value=1, max_value=7, value=5)
            minutes_per_day = st.number_input(
                "每天可投入时间（分钟）", min_value=15, max_value=240, value=45, step=15
            )
        with right:
            st.info("建议先选择 2–4 周。完成一轮比做一张过长的计划更重要。")
        preferences = st.text_area(
            "偏好或限制（可选）",
            placeholder="例如：只在工作日晚上学习；喜欢先做项目；不想使用英文教程",
            max_chars=500,
        )
        submitted = st.form_submit_button(
            "生成我的学习计划",
            width="stretch",
            disabled=demo_mode and not live_generation_available(),
        )

    if submitted:
        try:
            profile = make_profile(
                goal, current_level, plan_weeks, days_per_week, minutes_per_day, preferences
            )
        except ValidationError:
            st.error("请至少用 4 个字写清楚学习目标，例如“从零学习 Python 基础”。")
        else:
            with st.spinner("正在安排你的学习轨迹…"):
                try:
                    if demo_mode:
                        reserve_generation(invite_code)
                    plan = generate_study_plan(
                        profile,
                        on_metrics=metrics_recorder(repository, "initial"),
                    )
                    st.session_state.active_plan_id = repository.save_plan(profile, plan)
                except (PlanGenerationError, QuotaError) as error:
                    st.error(str(error))

    uploaded = st.file_uploader(
        "导入之前下载的计划与学习记录（JSON）", type="json", max_upload_size=1
    )
    if uploaded is not None and st.button("导入计划"):
        try:
            st.session_state.active_plan_id = import_bundle(repository, uploaded.getvalue())
            st.success("计划已导入。")
            st.rerun()
        except ValueError as error:
            st.error(str(error))

    saved_plans = repository.list_plans()
    if saved_plans:
        plan_ids = [saved.id for saved in saved_plans]
        selected_id = st.selectbox(
            "查看已保存计划",
            plan_ids,
            index=plan_ids.index(st.session_state.active_plan_id)
            if st.session_state.get("active_plan_id") in plan_ids
            else 0,
            format_func=lambda value: next(
                saved.plan.title for saved in saved_plans if saved.id == value
            ),
        )
        st.session_state.active_plan_id = selected_id
        selected = repository.load_plan(selected_id)
        if selected is not None:
            render_plan(
                selected.plan,
                selected.profile,
                repository,
                selected.id,
                demo_mode=demo_mode,
                sample_plan_id=sample_plan_id,
                invite_code=invite_code,
            )
    else:
        st.markdown(
            """<p class='section-label'>WHAT YOU'LL GET</p>
            <p>生成后会看到每周里程碑、每天一件聚焦任务，以及能实际检查完成度的产出物。</p>""",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
