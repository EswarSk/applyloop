"""Deterministic learning rules shared by the in-memory and Neo4j stores.

Agents (LLMs) only report evidence. These rules decide word status, lesson pass/fail and level.
"""
import json
from functools import lru_cache
from server.settings import ROOT

# A word is FLUENT only after correct real-world use (from recordings), not from lesson quizzes.
FLUENT_MIN_REAL_USES = 2
FLUENT_MIN_CONTEXTS = 2
# Passing a lesson moves its words to PRACTICED.
LESSON_PASS_SCORE = 0.7
# A level counts as reached once this share of its words is practiced or fluent.
LEVEL_KNOWLEDGE_THRESHOLD = 0.8
# A level counts as spoken fluently once this share of its words is fluent.
LEVEL_FLUENCY_THRESHOLD = 0.5

STATUSES = ('new', 'introduced', 'practiced', 'fluent')
WORD_OUTCOMES = ('used_correctly', 'used_incorrectly', 'not_understood')


def application_delta(score):
    return .15 if score >= .8 else .14 if score >= .6 else .08 if score >= .4 else .03


def reflection_delta(result):
    # No practice evidence means no proficiency gain, even if the recorder captured unrelated speech.
    return application_delta(result.success_score) if result.demonstrated or result.gaps or result.word_evidence else 0


def word_status(lesson_passed, real_uses, contexts):
    """Status from evidence. Mirrors STATUS_CYPHER in neo4j_store.py — keep them in sync."""
    if real_uses >= FLUENT_MIN_REAL_USES and contexts >= FLUENT_MIN_CONTEXTS:
        return 'fluent'
    if lesson_passed or real_uses >= 1:
        return 'practiced'
    return 'introduced'


def level_summary(rows):
    """rows: [{code, name, order, total, practiced, fluent}] where practiced includes fluent words."""
    levels, current, fluent_level = [], None, None
    for row in sorted(rows, key=lambda r: r['order']):
        total = row['total'] or 0
        practiced_pct = round(row['practiced'] / total, 2) if total else 0
        fluent_pct = round(row['fluent'] / total, 2) if total else 0
        reached = total > 0 and practiced_pct >= LEVEL_KNOWLEDGE_THRESHOLD
        fluent = total > 0 and fluent_pct >= LEVEL_FLUENCY_THRESHOLD
        if reached and current == (levels[-1]['code'] if levels else None):
            current = row['code']
        if fluent and fluent_level == (levels[-1]['code'] if levels else None):
            fluent_level = row['code']
        levels.append(dict(code=row['code'], name=row['name'], total_words=total,
                           practiced_words=row['practiced'], fluent_words=row['fluent'],
                           practiced_pct=practiced_pct, fluent_pct=fluent_pct,
                           reached=reached, fluent=fluent))
    return {'current': current or 'Pre-A1', 'fluent_level': fluent_level, 'levels': levels}


@lru_cache
def curriculum():
    data = json.loads((ROOT / 'server' / 'data' / 'curriculum_es.json').read_text())
    words = {w['id'] for w in data['words']}
    lessons = {l['id'] for l in data['lessons']}
    referenced = {w for l in data['lessons'] for w in l['words']} | {w for s in data['scenarios'] for w in s['words']}
    referenced |= {w for ws in data['skill_words'].values() for w in ws} | set(data['learner_seed']['real_uses'])
    if not referenced <= words:
        raise ValueError(f'Curriculum references unknown words: {sorted(referenced - words)}')
    if not set(data['learner_seed']['completed']) | {data['learner_seed']['current']['lesson_id']} <= lessons:
        raise ValueError('Curriculum learner seed references unknown lessons')
    phrase_words = {w for s in data['scenarios'] for p in s.get('phrases', []) for w in p['words']}
    replay_words = {e['word_id'] for r in data.get('demo_replays', {}).values() for e in r['reflection']['word_evidence']}
    if not (phrase_words | replay_words) <= words:
        raise ValueError(f'Curriculum phrases/replays reference unknown words: {sorted((phrase_words | replay_words) - words)}')
    scenarios = {s['id'] for s in data['scenarios']}
    if not {a['scenario'] for a in data.get('activities', [])} <= scenarios:
        raise ValueError('Curriculum activity references an unknown scenario')
    return data


def scenario_skill(scenario_id):
    """The skill a practice mission in this scenario trains (restaurant -> restaurant-ordering, ...)."""
    return next((s.get('skill') for s in curriculum()['scenarios'] if s['id'] == scenario_id), None)


def seed_knows(data):
    """Initial KNOWS state for the demo learner: passed lessons, the lesson in progress, prior real-world uses."""
    lessons = {l['id']: l for l in data['lessons']}
    seed = data['learner_seed']
    knows = {}
    def entry(word_id):
        return knows.setdefault(word_id, dict(lesson_passed=False, real_uses=0, contexts=[], struggles=0))
    for lesson_id, score in seed['completed'].items():
        for word_id in lessons[lesson_id]['words']:
            entry(word_id)['lesson_passed'] = entry(word_id)['lesson_passed'] or score >= LESSON_PASS_SCORE
    if seed['current']['step'] > 0:
        for word_id in lessons[seed['current']['lesson_id']]['words']:
            entry(word_id)
    for word_id, contexts in seed['real_uses'].items():
        entry(word_id).update(real_uses=len(contexts), contexts=list(dict.fromkeys(contexts)))
    for k in knows.values():
        k['status'] = word_status(k['lesson_passed'], k['real_uses'], len(k['contexts']))
    return knows


def lesson_statuses(lessons):
    """lessons: [{id, order, best_score, current_step}] -> {id: completed|current|available|locked}.
    A lesson unlocks once every earlier lesson has been passed (or it is the resume point)."""
    statuses, unlocked = {}, True
    for lesson in sorted(lessons, key=lambda l: l['order']):
        if lesson.get('current_step') is not None:
            statuses[lesson['id']] = 'current'
        elif lesson.get('best_score') is not None:
            statuses[lesson['id']] = 'completed'
        else:
            statuses[lesson['id']] = 'available' if unlocked else 'locked'
        unlocked = unlocked and lesson.get('best_score') is not None
    return statuses


def dedupe_word_evidence(word_evidence):
    """One observation per (word, outcome) per recording, so repeating a word in one recording counts once."""
    seen, result = set(), []
    for ev in word_evidence:
        key = (ev['word_id'], ev['outcome'])
        if key not in seen:
            seen.add(key)
            result.append(ev)
    return result


def scenario_for(context):
    """Map a context (e.g. type 'restaurant') onto a curriculum scenario id, if one exists."""
    ids = {s['id'] for s in curriculum()['scenarios']}
    return context.get('type') if context and context.get('type') in ids else None
