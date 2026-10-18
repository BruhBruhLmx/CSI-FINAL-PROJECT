import uuid
import base64
import json
import sqlite3
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import time
from config import DB_FILE, API_KEY
from engine import PART_COUNTS, parse_exam_sections, generate_full_ielts_exam, evaluate_ielts_exam, compile_audio_track, audio_track_path

st.set_page_config(page_title="Complete IELTS Simulation", layout="wide")

if not API_KEY:
    st.warning("Gemini API key is not configured. Saved tests and analytics remain available, but generation and AI grading are disabled.")

st.title("🎓 Complete AI IELTS Exam Simulation Terminal")

SECTION_TIME_LIMIT_SECONDS = 600


@st.fragment(run_every=1, key="section-countdown")
def render_section_timer(deadline):
        remaining = max(0, int(deadline - time.time() + 0.999))
        minutes, seconds = divmod(remaining, 60)
        st.markdown(
                f"<div style='font-size:24px;font-weight:bold;color:#c0392b;background:#fff0ed;padding:10px;border-radius:6px;text-align:center'>"
                f"Remaining time: <span>{minutes:02d}:{seconds:02d}</span></div>",
                unsafe_allow_html=True,
        )
        if remaining == 0:
                st.warning("Time expired. Submit your responses to continue.")


def render_single_play_audio(audio_path, playback_key):
        audio_data = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        storage_key = json.dumps(f"ielts-audio-played:{playback_key}")
        audio_html = f"""
        <style>
            body {{ margin: 0; font-family: sans-serif; color: #202124; }}
            .player {{ display: flex; align-items: center; gap: 12px; padding: 8px 0; }}
            button {{ padding: 9px 14px; border: 0; border-radius: 4px; background: #176b55; color: white; font-size: 14px; cursor: pointer; }}
            button:disabled {{ background: #727b78; cursor: default; }}
            .progress {{ height: 6px; background: #d9e2de; flex: 1; overflow: hidden; }}
            .progress span {{ display: block; height: 100%; width: 0; background: #176b55; }}
            #status {{ font-size: 12px; min-width: 145px; }}
        </style>
        <div class="player">
            <button id="play" type="button">Play once</button>
            <div class="progress" aria-hidden="true"><span id="progress"></span></div>
            <span id="status" aria-live="polite">One playback; seeking is disabled.</span>
        </div>
        <script>
            const audio = new Audio("data:audio/mpeg;base64,{audio_data}");
            const button = document.getElementById("play");
            const status = document.getElementById("status");
            const progress = document.getElementById("progress");
            const storageKey = {storage_key};
            let consumed = false;
            let maxPosition = 0;

            function readPlayed() {{
                try {{ return localStorage.getItem(storageKey) === "1"; }} catch (error) {{
                    try {{ return sessionStorage.getItem(storageKey) === "1"; }} catch (ignored) {{ return false; }}
                }}
            }}
            function writePlayed() {{
                try {{ localStorage.setItem(storageKey, "1"); return; }} catch (error) {{}}
                try {{ sessionStorage.setItem(storageKey, "1"); }} catch (error) {{}}
            }}

            if (readPlayed()) {{
                consumed = true;
                button.disabled = true;
                button.textContent = "Already played";
                status.textContent = "This part cannot be replayed.";
            }}

            button.addEventListener("click", async () => {{
                if (consumed) return;
                consumed = true;
                button.disabled = true;
                try {{
                    await audio.play();
                    writePlayed();
                    status.textContent = "Playing once; seeking is disabled.";
                }} catch (error) {{
                    consumed = false;
                    button.disabled = false;
                    status.textContent = "Playback could not start. Press Play once to retry.";
                }}
            }});

            audio.addEventListener("timeupdate", () => {{
                maxPosition = Math.max(maxPosition, audio.currentTime);
                if (audio.duration) progress.style.width = `${{(audio.currentTime / audio.duration) * 100}}%`;
            }});
            audio.addEventListener("seeking", () => {{
                if (Math.abs(audio.currentTime - maxPosition) > 0.5) audio.currentTime = maxPosition;
            }});
            audio.addEventListener("ended", () => {{
                button.textContent = "Playback complete";
                status.textContent = "This part cannot be replayed.";
            }});
            document.addEventListener("contextmenu", event => event.preventDefault());
        </script>
        """
        components.html(audio_html, height=62, scrolling=False)


tab1, tab2, tab3 = st.tabs(
        ["✨ Test Constructor Studio", "✍️ Examination Center Room", "📊 Analytical Performance Log"],
        key="main_tabs",
        on_change="rerun",
)

# ================= TAB 1: GENERATE BULK SECTIONS =================
with tab1:
    st.subheader("Generate an IELTS Practice Exam")
    colA, colB = st.columns(2)
    with colA:
        skill = st.selectbox("Select Target Skill Framework Module:", ["Listening", "Reading", "Writing", "Speaking"])
        theme = st.text_input("Enter Focus Theme Context Prompt:", "Global macroeconomic supply chain bottlenecks")
    with colB:
        band = st.slider("Configure Difficulty Settings (Target Band):", 4.0, 9.0, 6.5, 0.5)
        
    if st.button("🚀 Construct Full Multi-Part Exam Paper", type="primary", disabled=not API_KEY):
        with st.spinner(f"Generating the {PART_COUNTS[skill]}-part {skill.lower()} practice exam..."):
            try:
                raw_data, quality_report = generate_full_ielts_exam(skill, theme, band, with_quality_report=True)
                if not quality_report["passed"]:
                    st.error("The generated exam did not pass the quality screen and was not saved.")
                    st.write(quality_report["summary"])
                    st.write(quality_report["issues"])
                else:
                    parsed_data = parse_exam_sections(raw_data, skill)
                    stored_parts = parsed_data + [("", "", "")] * (4 - len(parsed_data))
                    new_id = str(uuid.uuid4())[:8]

                    if skill == "Listening":
                        for idx, sec_data in enumerate(parsed_data, start=1):
                            compile_audio_track(sec_data[0], new_id, idx)

                    values = (new_id, skill, theme, band, *(value for part in stored_parts for value in part))
                    with sqlite3.connect(DB_FILE) as conn:
                        conn.execute("""
                            INSERT INTO tests (
                                test_id, skill_type, theme, target_band,
                                sec1_content, sec1_questions, sec1_answers,
                                sec2_content, sec2_questions, sec2_answers,
                                sec3_content, sec3_questions, sec3_answers,
                                sec4_content, sec4_questions, sec4_answers
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, values)
                        conn.execute("""
                            INSERT INTO exam_quality (
                                test_id, target_band, estimated_band, difficulty_fit,
                                answer_key_status, quality_passed, issues, summary
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            new_id, band, quality_report["estimated_band"], quality_report["difficulty_fit"],
                            quality_report["answer_key_status"], int(quality_report["passed"]),
                            json.dumps(quality_report["issues"], ensure_ascii=True), quality_report["summary"],
                        ))
                    st.success(f"Practice exam generated and QA-screened. ID: #{new_id}")
                    st.caption(
                        f"QA estimated Band {quality_report['estimated_band']:.1f}; "
                        f"target fit {quality_report['difficulty_fit']:.0%}. AI QA is a screening check, not a guarantee."
                    )
            except Exception as exc:
                st.error(f"Could not generate the practice exam: {exc}")

# ================= TAB 2: ACTIVE SEQUENCE EXAMINATION ROOM =================
with tab2:
    st.subheader("Active Test Room Enclosure")
    
    conn = sqlite3.connect(DB_FILE)
    available_tests = conn.execute("SELECT test_id, skill_type, theme, target_band FROM tests").fetchall()
    conn.close()
    
    if not available_tests:
        st.info("No exam elements found in database bank. Complete a generation cycle in Tab 1 first.")
    else:
        test_options = {f"[{t[1]}] #{t[0]} - {t[2]} (Target Band {t[3]})": t[0] for t in available_tests}
        selected_test = st.selectbox("Select Test Package to Deploy:", list(test_options.keys()))
        active_id = test_options[selected_test]
        
        # Pull selected exam layout straight from DB environment matrix
        conn = sqlite3.connect(DB_FILE)
        active_exam = conn.execute("SELECT * FROM tests WHERE test_id = ?", (active_id,)).fetchone()
        conn.close()
        
        skill_type, test_theme, d_band = active_exam[1], active_exam[2], active_exam[3]
        
        # Initialize active exam phase tracking flags within session state
        if "active_test_id" not in st.session_state or st.session_state.active_test_id != active_id:
            st.session_state.active_test_id = active_id
            st.session_state.current_section = 1
            st.session_state.user_answers_accumulation = {}
            st.session_state.test_completed = False
            st.session_state.graded_evaluation = None
            st.session_state.graded_submission_id = None
            st.session_state.grading_error = None
            st.session_state.section_timer_key = None
            st.session_state.section_deadline = None
            st.session_state.attempt_id = uuid.uuid4().hex

        # Map current structural variables dynamically
        sec_num = st.session_state.current_section
        part_count = PART_COUNTS.get(skill_type, 4)

        timer_key = (active_id, sec_num)
        if tab2.open and not st.session_state.test_completed:
            if st.session_state.section_timer_key != timer_key:
                st.session_state.section_timer_key = timer_key
                st.session_state.section_deadline = time.time() + SECTION_TIME_LIMIT_SECONDS
            st.markdown("#### ⏱️ Section Countdown Timeline")
            render_section_timer(st.session_state.section_deadline)
        
        if not st.session_state.test_completed:
            st.markdown(f"## 📚 Active Part: {sec_num} / {part_count}")
            
            # Map index points out of SQLite record columns
            body_col_idx = 4 + (sec_num - 1) * 3
            q_col_idx = 5 + (sec_num - 1) * 3
            
            sec_body = active_exam[body_col_idx]
            sec_questions = active_exam[q_col_idx]
            
            # Context deployment interface nodes
            if skill_type == "Listening":
                audio_path = audio_track_path(active_id, sec_num)
                if audio_path.exists():
                    render_single_play_audio(audio_path, f"{st.session_state.attempt_id}:{active_id}:{sec_num}")
                else:
                    st.warning(f"Audio for part {sec_num} is missing.")
                    if st.button("Generate audio for this part", key=f"generate_audio_{active_id}_{sec_num}"):
                        try:
                            with st.spinner(f"Creating audio for part {sec_num}..."):
                                compile_audio_track(sec_body, active_id, sec_num)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not create audio for part {sec_num}: {exc}")
            elif skill_type == "Reading":
                st.info(sec_body)
            elif skill_type in ["Writing", "Speaking"]:
                st.info(sec_body)
                
            st.warning(sec_questions)
            
            # Collect response structures dynamically
            st.subheader("⌨️ Input Area")
            with st.form(f"response_form_{active_id}_{sec_num}"):
                if skill_type in ["Writing", "Speaking"]:
                    user_text_response = st.text_area("Provide your extended textual output block:", height=250, key=f"user_resp_p{sec_num}")
                else:
                    user_text_response = st.text_input("Enter comma-separated answers (e.g. Smith, 0912, Yes, No):", key=f"user_resp_p{sec_num}")
                section_submitted = st.form_submit_button("➡️ Complete Section & Advance", type="primary")

            if section_submitted:
                # Append data safely inside tracking dictionaries
                st.session_state.user_answers_accumulation[f"Section_{sec_num}"] = user_text_response
                
                if st.session_state.current_section < part_count:
                    st.session_state.current_section += 1
                    st.rerun()
                else:
                    st.session_state.test_completed = True
                    st.rerun()
        else:
            if st.session_state.graded_evaluation is None and st.session_state.grading_error is None:
                exam_sections = [
                    tuple(active_exam[4 + (part - 1) * 3:7 + (part - 1) * 3])
                    for part in range(1, part_count + 1)
                ]
                responses = [
                    st.session_state.user_answers_accumulation.get(f"Section_{part}", "")
                    for part in range(1, part_count + 1)
                ]
                if not API_KEY:
                    st.session_state.grading_error = "Gemini API key is not configured. You can review the saved test, but AI grading needs a connection and API key."
                else:
                    try:
                        with st.spinner("Evaluating each part and preparing your practice feedback..."):
                            st.session_state.graded_evaluation = evaluate_ielts_exam(
                                skill_type, test_theme, d_band, exam_sections, responses
                            )
                    except Exception as exc:
                        st.session_state.grading_error = str(exc)

            if st.session_state.grading_error:
                st.error(f"Could not complete the evaluation: {st.session_state.grading_error}")
                if API_KEY and st.button("Retry evaluation", key=f"retry_grade_{active_id}"):
                    st.session_state.grading_error = None
                    st.rerun()
            elif st.session_state.graded_evaluation is not None:
                evaluation = st.session_state.graded_evaluation
                final_answers = st.session_state.user_answers_accumulation
                if st.session_state.graded_submission_id is None:
                    submission_id = str(uuid.uuid4())[:8]
                    try:
                        with sqlite3.connect(DB_FILE) as conn:
                            conn.execute(
                                "INSERT INTO submissions (submission_id, test_id, user_responses, graded_band, feedback) VALUES (?, ?, ?, ?, ?)",
                                (submission_id, active_id, str(final_answers), evaluation["overall_band"], evaluation["overall_feedback"]),
                            )
                            conn.executemany(
                                "INSERT INTO submission_parts (submission_id, test_id, part_number, band_score, strengths, weaknesses, feedback) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                [
                                    (
                                        submission_id,
                                        active_id,
                                        part["part_number"],
                                        part["band_score"],
                                        "; ".join(part["strengths"]),
                                        "; ".join(part["weaknesses"]),
                                        part["feedback"],
                                    )
                                    for part in evaluation["parts"]
                                ],
                            )
                            conn.executemany(
                                "INSERT INTO submission_criteria (submission_id, test_id, part_number, criterion_name, band_score, evidence) VALUES (?, ?, ?, ?, ?, ?)",
                                [
                                    (
                                        submission_id,
                                        active_id,
                                        part["part_number"],
                                        criterion["name"],
                                        criterion["band_score"],
                                        criterion["evidence"],
                                    )
                                    for part in evaluation["parts"]
                                    for criterion in part["criteria"]
                                ],
                            )
                        st.session_state.graded_submission_id = submission_id
                    except sqlite3.Error as exc:
                        st.session_state.grading_error = f"Could not save your result: {exc}"
                        st.error(st.session_state.grading_error)

                if st.session_state.graded_submission_id:
                    st.metric("AI-estimated practice band", f"{evaluation['overall_band']:.1f}")
                    st.write(evaluation["overall_feedback"])
                    part_results = pd.DataFrame(evaluation["parts"])
                    part_results["part"] = part_results["part_number"].map(lambda number: f"Part {number}")
                    st.bar_chart(part_results.set_index("part")["band_score"])
                    st.dataframe(
                        part_results[["part", "band_score", "strengths", "weaknesses", "feedback"]],
                        use_container_width=True,
                        hide_index=True,
                    )
                    criterion_results = pd.DataFrame([
                        {
                            "part": f"Part {part['part_number']}",
                            "criterion": criterion["name"],
                            "band_score": criterion["band_score"],
                            "evidence": criterion["evidence"],
                        }
                        for part in evaluation["parts"]
                        for criterion in part["criteria"]
                    ])
                    st.subheader("Criterion-level practice estimates")
                    st.dataframe(criterion_results, use_container_width=True, hide_index=True)

                    if st.button("Take another attempt", key=f"another_attempt_{active_id}"):
                        st.session_state.test_completed = False
                        st.session_state.current_section = 1
                        st.session_state.user_answers_accumulation = {}
                        st.session_state.graded_evaluation = None
                        st.session_state.graded_submission_id = None
                        st.session_state.grading_error = None
                        st.session_state.section_timer_key = None
                        st.session_state.section_deadline = None
                        st.session_state.attempt_id = uuid.uuid4().hex
                        st.rerun()
#================= TAB 3: USER METRICS RECORDS =================
with tab3:
    st.subheader("📈 Performance History")
    with sqlite3.connect(DB_FILE) as conn:
        df_subs = pd.read_sql_query("SELECT * FROM submissions ORDER BY timestamp DESC", conn)
        df_parts = pd.read_sql_query("""
            SELECT sp.part_number, sp.band_score, sp.strengths, sp.weaknesses, sp.feedback,
                   t.skill_type, t.theme, s.timestamp
            FROM submission_parts AS sp
            JOIN tests AS t ON t.test_id = sp.test_id
            JOIN submissions AS s ON s.submission_id = sp.submission_id
            ORDER BY s.timestamp DESC
        """, conn)
        df_criteria = pd.read_sql_query("""
            SELECT sc.submission_id, sc.part_number, sc.criterion_name, sc.band_score, sc.evidence,
                   t.skill_type, t.theme, s.timestamp
            FROM submission_criteria AS sc
            JOIN tests AS t ON t.test_id = sc.test_id
            JOIN submissions AS s ON s.submission_id = sc.submission_id
            ORDER BY s.timestamp DESC, sc.part_number
        """, conn)
        df_quality = pd.read_sql_query("""
            SELECT q.*, t.skill_type, t.theme
            FROM exam_quality AS q
            JOIN tests AS t ON t.test_id = q.test_id
            ORDER BY q.validated_at DESC
        """, conn)
        df_human = pd.read_sql_query("""
            SELECT hr.*, s.timestamp, t.skill_type, t.theme
            FROM human_ratings AS hr
            JOIN submissions AS s ON s.submission_id = hr.submission_id
            JOIN tests AS t ON t.test_id = hr.test_id
            ORDER BY hr.created_at DESC
        """, conn)

    if df_subs.empty:
        st.info("No exam history yet. Complete and evaluate a practice exam to start tracking scores.")
    else:
        st.subheader("Overall practice score by attempt")
        st.line_chart(df_subs.set_index("timestamp")["graded_band"])

    if df_parts.empty:
        st.info("Per-part averages will appear after the first evaluated attempt.")
    else:
        skills = sorted(df_parts["skill_type"].dropna().unique().tolist())
        selected_skill = st.selectbox("Skill", skills, key="analytics_skill")
        skill_history = df_parts[df_parts["skill_type"] == selected_skill]

        st.subheader("Average practice score by theme and part")
        theme_part_scores = skill_history.pivot_table(
            index="theme", columns="part_number", values="band_score", aggfunc="mean"
        )
        theme_part_scores.columns = [f"Part {number}" for number in theme_part_scores.columns]
        st.bar_chart(theme_part_scores)

        st.subheader("Areas to revisit")
        weak_areas = (
            df_parts.groupby(["skill_type", "theme", "part_number"], as_index=False)
            .agg(average_band=("band_score", "mean"), attempts=("band_score", "count"))
            .sort_values("average_band")
            .head(5)
        )
        weak_areas["part"] = weak_areas["part_number"].map(lambda number: f"Part {number}")
        st.dataframe(
            weak_areas[["skill_type", "theme", "part", "average_band", "attempts"]],
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Part feedback history")
        df_parts["part"] = df_parts["part_number"].map(lambda number: f"Part {number}")
        st.dataframe(
            df_parts[["timestamp", "skill_type", "theme", "part", "band_score", "strengths", "weaknesses"]],
            use_container_width=True,
            hide_index=True,
        )

    if df_criteria.empty:
        st.info("Criterion-level trends will appear for attempts evaluated after rubric scoring was added.")
    else:
        st.subheader("Average scores by assessment criterion")
        criterion_summary = (
            df_criteria.dropna(subset=["band_score"])
            .groupby(["skill_type", "theme", "criterion_name"], as_index=False)
            .agg(average_band=("band_score", "mean"), attempts=("submission_id", "nunique"))
            .sort_values(["skill_type", "average_band"])
        )
        st.dataframe(criterion_summary.round({"average_band": 2}), use_container_width=True, hide_index=True)
        if not criterion_summary.empty:
            selected_criterion_skill = st.selectbox(
                "Criterion chart skill", sorted(criterion_summary["skill_type"].unique()), key="criterion_skill"
            )
            chart_data = criterion_summary[criterion_summary["skill_type"] == selected_criterion_skill]
            criterion_chart = chart_data.pivot(index="theme", columns="criterion_name", values="average_band")
            st.bar_chart(criterion_chart)
        st.dataframe(
            df_criteria[["timestamp", "skill_type", "theme", "part_number", "criterion_name", "band_score", "evidence"]],
            use_container_width=True,
            hide_index=True,
        )

    if df_quality.empty:
        st.info("Generation QA history will appear for exams generated after quality screening was added.")
    else:
        st.subheader("Generation quality-screen history")
        st.dataframe(
            df_quality[["validated_at", "skill_type", "theme", "target_band", "estimated_band", "difficulty_fit", "answer_key_status", "quality_passed", "summary"]],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Human-reviewed criterion labels")
    scoreable_criteria = df_criteria[
        df_criteria["band_score"].notna() & (df_criteria["criterion_name"] != "Pronunciation")
    ]
    if scoreable_criteria.empty:
        st.info("Human review can be entered after a rubric-scored attempt. Text-only Speaking pronunciation is intentionally excluded.")
    else:
        attempt_options = {
            f"{row.submission_id} | {row.skill_type} | {row.theme} | {row.timestamp}": row.submission_id
            for row in scoreable_criteria[["submission_id", "skill_type", "theme", "timestamp"]].drop_duplicates().itertuples(index=False)
        }
        selected_attempt_label = st.selectbox("Attempt to review", list(attempt_options), key="human_review_attempt")
        selected_submission_id = attempt_options[selected_attempt_label]
        review_rows = scoreable_criteria[scoreable_criteria["submission_id"] == selected_submission_id]
        criterion_options = {
            f"Part {row.part_number} | {row.criterion_name}": row
            for row in review_rows.itertuples(index=False)
        }
        with st.form("human_criterion_review"):
            selected_criterion_label = st.selectbox("Criterion", list(criterion_options))
            reviewer_name = st.text_input("Reviewer name or ID")
            selected_criterion = criterion_options[selected_criterion_label]
            ai_score = selected_criterion.band_score
            default_score = float(ai_score) if pd.notna(ai_score) else 6.0
            human_score = st.slider("Human-reviewed band", 0.0, 9.0, min(9.0, max(0.0, default_score)), 0.5)
            reviewer_notes = st.text_area("Evidence or notes")
            save_review = st.form_submit_button("Save human review", type="primary")
        if save_review:
            if not reviewer_name.strip():
                st.error("Enter a reviewer name or ID to preserve label provenance.")
            else:
                with sqlite3.connect(DB_FILE) as conn:
                    conn.execute("""
                        INSERT INTO human_ratings (
                            rating_id, submission_id, test_id, part_number, criterion_name,
                            band_score, reviewer, notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(uuid.uuid4()), selected_submission_id, selected_criterion.test_id,
                        selected_criterion.part_number, selected_criterion.criterion_name,
                        human_score, reviewer_name.strip(), reviewer_notes.strip(),
                    ))
                st.success("Human review saved separately from the AI estimate.")
                st.rerun()

    if df_human.empty:
        st.info("No human-reviewed criterion labels have been entered yet. The criterion ML model uses only these reviewed labels.")
    else:
        human_summary = (
            df_human.groupby(["skill_type", "theme", "part_number", "criterion_name"], as_index=False)
            .agg(human_average_band=("band_score", "mean"), reviews=("rating_id", "count"), distinct_attempts=("submission_id", "nunique"))
            .sort_values(["skill_type", "theme", "part_number", "criterion_name"])
        )
        st.dataframe(human_summary.round({"human_average_band": 2}), use_container_width=True, hide_index=True)
        st.dataframe(
            df_human[["created_at", "reviewer", "submission_id", "skill_type", "theme", "part_number", "criterion_name", "band_score", "notes"]],
            use_container_width=True,
            hide_index=True,
        )
