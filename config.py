import os
import sqlite3
from dotenv import load_dotenv

# Load environmental variables
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
DB_FILE = "ielts_platform_pro.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Stores master test metadata and comprehensive generated packages
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tests (
            test_id TEXT PRIMARY KEY,
            skill_type TEXT,
            theme TEXT,
            target_band REAL,
            sec1_content TEXT, sec1_questions TEXT, sec1_answers TEXT,
            sec2_content TEXT, sec2_questions TEXT, sec2_answers TEXT,
            sec3_content TEXT, sec3_questions TEXT, sec3_answers TEXT,
            sec4_content TEXT, sec4_questions TEXT, sec4_answers TEXT
        )
    """)
    # Tracks user attempt histories across the architecture
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            submission_id TEXT PRIMARY KEY,
            test_id TEXT,
            user_responses TEXT,
            graded_band REAL,
            feedback TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submission_parts (
            submission_id TEXT NOT NULL,
            test_id TEXT NOT NULL,
            part_number INTEGER NOT NULL,
            band_score REAL NOT NULL,
            strengths TEXT NOT NULL,
            weaknesses TEXT NOT NULL,
            feedback TEXT NOT NULL,
            PRIMARY KEY (submission_id, part_number)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submission_criteria (
            submission_id TEXT NOT NULL,
            test_id TEXT NOT NULL,
            part_number INTEGER NOT NULL,
            criterion_name TEXT NOT NULL,
            band_score REAL,
            evidence TEXT NOT NULL,
            PRIMARY KEY (submission_id, part_number, criterion_name)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exam_quality (
            test_id TEXT PRIMARY KEY,
            target_band REAL NOT NULL,
            estimated_band REAL NOT NULL,
            difficulty_fit REAL NOT NULL,
            answer_key_status TEXT NOT NULL,
            quality_passed INTEGER NOT NULL,
            issues TEXT NOT NULL,
            summary TEXT NOT NULL,
            validated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS human_ratings (
            rating_id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL,
            test_id TEXT NOT NULL,
            part_number INTEGER NOT NULL,
            criterion_name TEXT NOT NULL,
            band_score REAL NOT NULL CHECK (band_score >= 0 AND band_score <= 9),
            reviewer TEXT NOT NULL,
            notes TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()
