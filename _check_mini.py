"""Check OthelloMini current questionnaire state."""
import psycopg2, json
from app.config import settings

conn = psycopg2.connect(settings.DATABASE_URL)
cur = conn.cursor()
cur.execute("SELECT name, build_mode, status, questionnaire_state FROM projects WHERE build_mode='mini' ORDER BY created_at DESC LIMIT 1")
row = cur.fetchone()
if row:
    print(f"Project: {row[0]}  mode={row[1]}  status={row[2]}")
    qs = row[3] if row[3] else {}
    if isinstance(qs, str):
        qs = json.loads(qs)
    print(f"Completed sections: {qs.get('completed_sections', [])}")
    answers = qs.get('answers', {})
    print(f"Answer keys: {list(answers.keys())}")
    history = qs.get('conversation_history', [])
    print(f"History length: {len(history)}")
    for m in history:
        print(f"  [{m['role']}] section={m.get('section','?')} | {m['content'][:120]}...")
conn.close()
