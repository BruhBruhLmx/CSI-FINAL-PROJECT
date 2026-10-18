import re
import asyncio
import json
import tempfile
from pathlib import Path
import edge_tts
from pydub import AudioSegment
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from config import API_KEY

client = genai.Client(api_key=API_KEY) if API_KEY else None

VOICE_MAP = {
    "officer": "en-GB-SoniaNeural", "clerk": "en-GB-SoniaNeural", "receptionist": "en-GB-SoniaNeural",
    "customer": "en-AU-NatashaNeural", "sarah": "en-AU-NatashaNeural", "student": "en-AU-WilliamNeural",
    "professor": "en-CA-ClaraNeural", "lecturer": "en-US-BrianNeural", "examiner": "en-GB-RyanNeural",
    "greg": "en-GB-RyanNeural",
}
VOICE_GENDER = {
    "en-GB-SoniaNeural": "female", "en-AU-NatashaNeural": "female", "en-AU-WilliamNeural": "male",
    "en-CA-ClaraNeural": "female", "en-US-BrianNeural": "male", "en-GB-RyanNeural": "male",
}
DEFAULT_VOICE_BY_GENDER = {"female": "en-GB-SoniaNeural", "male": "en-GB-RyanNeural"}

PART_COUNTS = {"Listening": 4, "Reading": 4, "Writing": 2, "Speaking": 3}
AUDIO_ROOT = Path(__file__).resolve().parent / "audio"
GENERATION_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
)

WRITING_CRITERIA = {
    1: ("Task Achievement", "Coherence and Cohesion", "Lexical Resource", "Grammatical Range and Accuracy"),
    2: ("Task Response", "Coherence and Cohesion", "Lexical Resource", "Grammatical Range and Accuracy"),
}
SPEAKING_CRITERIA = (
    "Fluency and Coherence", "Lexical Resource", "Grammatical Range and Accuracy", "Pronunciation",
)


def band_difficulty_guidance(target_band):
    if target_band < 5.5:
        return "Use familiar, concrete contexts, common vocabulary, mostly simple language, direct questions, and limited inference. Keep distractors obvious but plausible."
    if target_band < 6.5:
        return "Use familiar and moderately abstract contexts, common plus some less-common vocabulary, a mixture of simple and compound language, paraphrase, and moderate inference."
    if target_band < 7.5:
        return "Use a broader range of abstract contexts, flexible paraphrase, varied complex language, precise vocabulary, implied relationships, and plausible distractors requiring synthesis."
    return "Use nuanced or unfamiliar abstract contexts, dense but coherent information, subtle distinctions, precise less-common vocabulary, complex syntax, and multi-step inference without relying on ambiguity or obscure outside knowledge."


def criteria_for_part(skill, part_number):
    if skill == "Writing":
        return WRITING_CRITERIA[part_number]
    if skill == "Speaking":
        return SPEAKING_CRITERIA
    if skill in ("Listening", "Reading"):
        return ("Answer Accuracy",)
    raise ValueError(f"Unsupported IELTS skill: {skill}")


def parse_exam_sections(raw_data, skill):
    expected_parts = PART_COUNTS[skill]
    sections = raw_data.split("===SECTION_SPLIT===")
    if len(sections) != expected_parts:
        raise ValueError(f"Expected {expected_parts} parts, received {len(sections)}.")

    parsed_sections = []
    for part_number, section in enumerate(sections, start=1):
        blocks = [block.strip() for block in section.split("===DATA_SPLIT===")]
        if len(blocks) != 3 or any(not block for block in blocks):
            raise ValueError(f"Part {part_number} must include content, questions, and an answer key.")
        parsed_sections.append(tuple(blocks))
    return parsed_sections


def _generate_content_with_fallback(contents, config=None, validator=None):
    if client is None:
        raise RuntimeError("Missing Gemini API key. Set GEMINI_API_KEY in your .env file.")

    failures = []
    last_error = None
    for model_name in GENERATION_MODELS:
        request = {"model": model_name, "contents": contents}
        if config is not None:
            request["config"] = config
        try:
            response = client.models.generate_content(**request)
            if not response.text:
                raise ValueError("Gemini returned an empty response.")
            if validator is not None:
                validator(response.text)
            return response
        except ClientError as exc:
            if exc.code == 429:
                raise RuntimeError(
                    "Gemini rate limit or quota reached. Wait for quota reset or configure a higher API quota; switching Gemini models will not bypass a shared project quota."
                ) from exc
            last_error = exc
            failures.append(f"{model_name}: {exc}")
        except (ServerError, ValueError) as exc:
            last_error = exc
            failures.append(f"{model_name}: {exc}")

    raise RuntimeError(
        "Gemini could not complete the request with any configured model. " + "; ".join(failures)
    ) from last_error

async def download_line(text, voice, path):
    communicate = edge_tts.Communicate(text, voice, rate="+2%", pitch="+0Hz")
    await communicate.save(path)


def select_voice(speaker, gender=None):
    normalized_gender = gender.casefold() if gender else None
    if normalized_gender not in (None, "female", "male"):
        raise ValueError(f"Unsupported speaker gender: {gender}")

    mapped_voice = VOICE_MAP.get(speaker.casefold())
    if normalized_gender:
        if mapped_voice and VOICE_GENDER.get(mapped_voice) == normalized_gender:
            return mapped_voice
        return DEFAULT_VOICE_BY_GENDER[normalized_gender]
    return mapped_voice or DEFAULT_VOICE_BY_GENDER["male"]


def compile_audio_track(script_text, test_id, section_num):
    final_audio = AudioSegment.empty()
    lines = [l.strip() for l in script_text.strip().split('\n') if l.strip()]
    dialogue_lines = []
    for line in lines:
        match = re.match(r"^([^:]+):\s*(.*)$", line)
        if match:
            speaker_label = match.group(1).strip()
            gender_match = re.match(r"^(.*?)\s*\[(female|male)\]$", speaker_label, re.IGNORECASE)
            if gender_match:
                speaker = gender_match.group(1).strip()
                gender = gender_match.group(2).casefold()
            else:
                speaker = speaker_label
                gender = None
            dialogue_lines.append((speaker, gender, match.group(2).strip()))
        else:
            dialogue_lines.append(("Examiner", None, line))

    dialogue_lines = [(speaker, gender, text) for speaker, gender, text in dialogue_lines if text]

    if not dialogue_lines:
        raise ValueError(f"Listening part {section_num} has no speaker dialogue to convert to audio.")

    safe_test_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(test_id))
    audio_dir = AUDIO_ROOT / safe_test_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = audio_dir / f"section_{section_num}.mp3"

    with tempfile.TemporaryDirectory(dir=audio_dir, prefix=f".part_{section_num}_") as cache_dir:
        for idx, (speaker, gender, text) in enumerate(dialogue_lines):
            voice = select_voice(speaker, gender)
            temp_file = Path(cache_dir) / f"line_{idx}.mp3"
            asyncio.run(download_line(text, voice, str(temp_file)))
            chunk = AudioSegment.from_mp3(temp_file)
            final_audio += chunk + AudioSegment.silent(duration=350)

    final_audio.export(output_path, format="mp3")
    return str(output_path)


def audio_track_path(test_id, section_num):
    safe_test_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(test_id))
    audio_path = AUDIO_ROOT / safe_test_id / f"section_{section_num}.mp3"
    legacy_path = Path(__file__).resolve().parent / f"ielts_{test_id}_sec{section_num}.mp3"
    if not audio_path.exists() and legacy_path.exists():
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.replace(audio_path)
    return audio_path

def validate_exam_quality(skill, theme, target_band, sections):
    source = json.dumps(
        [
            {"part_number": index, "content": part[0], "questions": part[1], "answer_key": part[2]}
            for index, part in enumerate(sections, start=1)
        ],
        ensure_ascii=True,
    )
    answer_audit_values = "pass or fail" if skill in ("Listening", "Reading") else "not_applicable"
    prompt = f"""
    Independently quality-check this AI-generated IELTS practice paper. Do not assume its answer key is correct.
    Skill: {skill}; theme: {theme!r}; requested practice target band: {target_band}.
    Difficulty guidance for the target: {band_difficulty_guidance(target_band)}

    Check that every part is coherent, questions are answerable from the supplied content, objective answer keys
    are directly supported by that content, writing/speaking tasks fit the skill, and the actual difficulty is
    reasonably close to the target. Flag ambiguity, unsupported facts, contradictions, or answer-key mismatches.
    Do not use outside factual knowledge as a requirement for answering. This is a screening pass, not proof that
    the paper is error-free. For Listening/Reading, answer_key_status must be pass or fail. For Writing/Speaking,
    answer_key_status must be not_applicable.

    Return only a JSON object with these fields: estimated_band (number), difficulty_fit (number from 0 to 1),
    answer_key_status (one of: {answer_audit_values}), issues (array of strings), and summary (string).
    estimated_band is the difficulty you judge the paper to require.

    Paper data:
    {source}
    """

    def validate_report(text):
        report = json.loads(text)
        if not isinstance(report, dict):
            raise ValueError("Quality report must be a JSON object.")
        estimated_band = report.get("estimated_band")
        fit = report.get("difficulty_fit")
        if not isinstance(estimated_band, (int, float)) or not 0 <= estimated_band <= 9:
            raise ValueError("Quality report contains an invalid estimated band.")
        if not isinstance(fit, (int, float)) or not 0 <= fit <= 1:
            raise ValueError("Quality report contains an invalid difficulty-fit value.")
        if report.get("answer_key_status") not in ("pass", "fail", "not_applicable"):
            raise ValueError("Quality report contains an invalid answer-key status.")
        if not isinstance(report.get("issues"), list) or not all(isinstance(item, str) for item in report["issues"]):
            raise ValueError("Quality report issues must be a list of strings.")
        if not isinstance(report.get("summary"), str) or not report["summary"].strip():
            raise ValueError("Quality report is missing its summary.")

    response = _generate_content_with_fallback(
        prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
        validator=validate_report,
    )
    report = json.loads(response.text)
    expected_status = "pass" if skill in ("Listening", "Reading") else "not_applicable"
    report["passed"] = (
        abs(report["estimated_band"] - target_band) <= 0.5
        and report["difficulty_fit"] >= 0.8
        and report["answer_key_status"] == expected_status
        and not report["issues"]
    )
    return report


def generate_full_ielts_exam(skill, theme, band, with_quality_report=False):
    part_count = PART_COUNTS[skill]
    listening_voice_guidance = """
    For Listening dialogue, format every spoken line as `Speaker Name [female]: words` or `Speaker Name [male]: words`.
    Keep each speaker's name and gender tag consistent throughout the script. Make the lines natural to speak aloud:
    use contractions, varied sentence lengths, and realistic conversational punctuation without adding stage directions.
    """ if skill == "Listening" else ""
    prompt = f"""
    Create a realistic IELTS practice exam for {skill} with exactly {part_count} parts.
    Theme: {theme}
    Target Candidate Difficulty: IELTS Band {band}
    Difficulty calibration: {band_difficulty_guidance(band)}
    {listening_voice_guidance}

    Keep every question answerable from the generated content. For Listening and Reading, verify that every
    answer-key entry is explicitly supported by the corresponding script or passage. Do not rely on outside facts.
    Avoid ambiguous questions, contradictory details, or answer keys with multiple plausible answers.

    Follow the IELTS task structure for this skill: Writing has 2 tasks, Speaking has 3 parts, and Listening/Reading have 4 parts. Use the exact delimiters '===SECTION_SPLIT===' between parts and '===DATA_SPLIT===' inside each part.

    OUTPUT STRUCTURE REQUIREMENT:
    [Part 1 Content Text / Script Dialogue]
    ===DATA_SPLIT===
    [Part 1 Questions]
    ===DATA_SPLIT===
    [Part 1 Answer Key]
    ===SECTION_SPLIT===
    [Part 2 Content]
    ===DATA_SPLIT===
    [Part 2 Questions]
    ===DATA_SPLIT===
    [Part 2 Answer Key]
    Continue this exact three-block format for all {part_count} parts. Return only the delimited exam data.
    """
    
    current_prompt = prompt
    last_report = None
    for attempt in range(2):
        response = _generate_content_with_fallback(
            current_prompt,
            validator=lambda response_text: parse_exam_sections(response_text, skill),
        )
        sections = parse_exam_sections(response.text, skill)
        last_report = validate_exam_quality(skill, theme, band, sections)
        if last_report["passed"]:
            return (response.text, last_report) if with_quality_report else response.text
        if attempt == 0:
            current_prompt += f"\nRevise the paper to fix these independent review findings before returning it: {json.dumps(last_report, ensure_ascii=True)}"

    if with_quality_report:
        return response.text, last_report
    raise ValueError(f"Generated paper did not pass the quality screen: {last_report['summary']}")


def evaluate_ielts_exam(skill, theme, target_band, sections, responses):
    if len(sections) != len(responses):
        raise ValueError("Each exam part must have a submitted response.")

    submitted_parts = []
    for part_number, (section, response) in enumerate(zip(sections, responses), start=1):
        content, questions, answer_key = section
        submitted_parts.append({
            "part_number": part_number,
            "content": content,
            "questions": questions,
            "answer_key": answer_key,
            "candidate_response": response,
        })

    rubric_by_part = {
        part_number: criteria_for_part(skill, part_number)
        for part_number in range(1, len(sections) + 1)
    }

    prompt = f"""
    Evaluate this {skill} IELTS practice attempt about the theme {theme!r}.
        Target band: {target_band}. Apply IELTS public assessment dimensions consistently and score each part separately.
        Required criteria by part: {json.dumps(rubric_by_part)}.
        For Writing, provide a band and brief evidence for Task Achievement (Task 1) or Task Response (Task 2),
        Coherence and Cohesion, Lexical Resource, and Grammatical Range and Accuracy.
        For Speaking, assess Fluency and Coherence, Lexical Resource, Grammatical Range and Accuracy, and Pronunciation.
        This submission is text-only, so Pronunciation must have band_score null and clearly state it cannot be assessed.
        For Listening/Reading, compare with the supplied answer key and report correct_count and question_count when countable.
        Return only JSON with this shape:
        {{"overall_band": 6.5, "overall_feedback": "...", "parts": [
            {{"part_number": 1, "band_score": 6.0, "correct_count": null, "question_count": null,
            "criteria": [{{"name": "criterion", "band_score": 6.0, "evidence": "..."}}],
            "strengths": ["..."], "weaknesses": ["..."], "feedback": "..."}}
        ]}}.
        Scores are practice estimates, not official IELTS results. Do not fabricate pronunciation or objective correctness.

    Submitted parts:
    {json.dumps(submitted_parts, ensure_ascii=True)}
    """
    result = _generate_content_with_fallback(
        prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
        validator=json.loads,
    )
    evaluation = json.loads(result.text)
    parts = evaluation.get("parts")
    overall_band = evaluation.get("overall_band")
    if not isinstance(parts, list) or len(parts) != len(sections):
        raise ValueError("The evaluation did not include one result for each exam part.")
    if not isinstance(overall_band, (int, float)) or not 0 <= overall_band <= 9:
        raise ValueError("The evaluation returned an invalid overall band score.")
    if not isinstance(evaluation.get("overall_feedback"), str) or not evaluation["overall_feedback"].strip():
        raise ValueError("The evaluation did not include overall feedback.")

    for part_number, part in enumerate(parts, start=1):
        if not isinstance(part, dict):
            raise ValueError(f"The evaluation returned an invalid result for part {part_number}.")
        score = part.get("band_score")
        if part.get("part_number") != part_number or not isinstance(score, (int, float)) or not 0 <= score <= 9:
            raise ValueError(f"The evaluation returned an invalid result for part {part_number}.")
        for key in ("strengths", "weaknesses"):
            if not isinstance(part.get(key), list) or not all(isinstance(item, str) for item in part[key]):
                raise ValueError(f"The evaluation returned invalid {key} for part {part_number}.")
        if not isinstance(part.get("feedback"), str) or not part["feedback"].strip():
            raise ValueError(f"The evaluation did not include feedback for part {part_number}.")
        criteria = part.get("criteria")
        expected_criteria = criteria_for_part(skill, part_number)
        if not isinstance(criteria, list) or {item.get("name") for item in criteria if isinstance(item, dict)} != set(expected_criteria):
            raise ValueError(f"The evaluation returned incomplete criteria for part {part_number}.")
        for criterion in criteria:
            score = criterion.get("band_score")
            if criterion["name"] == "Pronunciation" and skill == "Speaking":
                if score is not None:
                    raise ValueError("Pronunciation cannot be scored from the current text-only response.")
            elif not isinstance(score, (int, float)) or not 0 <= score <= 9:
                raise ValueError(f"The evaluation returned an invalid {criterion['name']} score for part {part_number}.")
            if not isinstance(criterion.get("evidence"), str) or not criterion["evidence"].strip():
                raise ValueError(f"The evaluation omitted evidence for {criterion['name']} in part {part_number}.")

    return evaluation
