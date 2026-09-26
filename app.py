"""Streamlit 入口：把结构化学习计划展示为一条清晰的学习轨迹。"""

from __future__ import annotations

from html import escape

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from models import LearningProfile, StudyPlan
from planner import PlanGenerationError, generate_study_plan


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


def render_plan(plan: StudyPlan, profile: LearningProfile) -> None:
    st.markdown(
        f"""<div class='plan-head'><h2>{escape(plan.title)}</h2>
        <p>{escape(plan.overview)}</p></div>""",
        unsafe_allow_html=True,
    )
    metric_one, metric_two, metric_three = st.columns(3)
    metric_one.metric("计划周期", f"{profile.plan_weeks} 周")
    metric_two.metric("每周节奏", f"{profile.days_per_week} 天")
    metric_three.metric("单日上限", f"{profile.minutes_per_day} 分钟")

    st.markdown("<p class='section-label'>YOUR LEARNING TRAIL</p>", unsafe_allow_html=True)
    for week in plan.weekly_plans:
        with st.expander(f"第 {week.week_number} 周 · {week.theme}", expanded=week.week_number == 1):
            st.caption(f"本周里程碑：{week.milestone}")
            for task in week.tasks:
                st.markdown(
                    f"""<div class='task-card'>
                    <div class='task-title'>第 {task.day} 天 · {escape(task.title)}</div>
                    <div class='task-meta'>{task.duration_minutes} MINUTES</div>
                    <div>{escape(task.description)}</div>
                    <div class='deliverable'>产出：{escape(task.deliverable)}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )

    st.markdown("<p class='section-label'>KEEP GOING</p>", unsafe_allow_html=True)
    for tip in plan.learning_tips:
        st.markdown(f"<div class='tip'>✦ {escape(tip)}</div>", unsafe_allow_html=True)

    st.markdown("<p class='section-label'>COPY AS MARKDOWN</p>", unsafe_allow_html=True)
    st.code(plan_to_markdown(plan), language="markdown")
    st.caption("点击代码块右上角的复制图标，即可保存你的计划。")


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


def main() -> None:
    inject_styles()
    st.markdown(
        """<section class='hero'><div class='eyebrow'>LEARNING COMPASS / 01</div>
        <h1>把想学，变成今天能做。</h1>
        <p>告诉我你的目标和可用时间，我会为你画出一条不超载的学习轨迹。</p></section>""",
        unsafe_allow_html=True,
    )
    st.markdown("<p class='section-label'>BUILD YOUR PLAN</p>", unsafe_allow_html=True)

    with st.form("learning_profile"):
        goal = st.text_input(
            "这次想学会什么？",
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
        submitted = st.form_submit_button("生成我的学习计划", use_container_width=True)

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
                    st.session_state.plan = generate_study_plan(profile)
                    st.session_state.profile = profile
                except PlanGenerationError as error:
                    st.error(str(error))

    if "plan" in st.session_state and "profile" in st.session_state:
        render_plan(st.session_state.plan, st.session_state.profile)
        if st.button("清除当前计划", type="secondary"):
            del st.session_state.plan
            del st.session_state.profile
            st.rerun()
    else:
        st.markdown(
            """<p class='section-label'>WHAT YOU'LL GET</p>
            <p>生成后会看到每周里程碑、每天一件聚焦任务，以及能实际检查完成度的产出物。</p>""",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
