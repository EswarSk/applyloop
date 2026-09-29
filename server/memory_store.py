"""In-memory store: the no-accounts fallback used when NEO4J_URI is not set, and by unit tests.
Implements exactly the same interface and rules as Neo4jStore (see test_graph_store.py)."""
from copy import deepcopy
from datetime import datetime, timezone
from server.demo_seed import example
from server.graph_view import build_activity, build_graph, build_progress
from server.learning_rules import (LESSON_PASS_SCORE, reflection_delta, curriculum, dedupe_word_evidence,
                                   lesson_statuses, level_summary, scenario_for, seed_knows, word_status)


def now():
    return datetime.now(timezone.utc).isoformat()


class DemoStore:
    backend = 'memory'

    def __init__(self):
        self.reset()

    def connect(self):
        pass

    def close(self):
        pass

    # ---------- lifecycle ----------
    def reset(self):
        cur = curriculum()
        self.data = example('state')
        self.data['context'] = self._activity_context(self.data['context']['id']) or self.data['context']
        self.recordings, self.experiences = {}, {}
        self.knows = seed_knows(cur)
        self.completed = {lid: {'best_score': score, 'attempts': 1} for lid, score in cur['learner_seed']['completed'].items()}
        self.attempts = {lid: 1 for lid in self.completed}
        seed = cur['learner_seed']['current']
        self.current = {'lesson_id': seed['lesson_id'], 'step': seed['step'], 'updated_at': now()}

    # ---------- learner state ----------
    def state(self):
        state = deepcopy(self.data)
        state.update(graph_backend=self.backend, focus_words=self.focus_words())
        return state

    def feedback(self):
        return deepcopy(next(reversed(self.experiences.values()))['result']) if self.experiences else None

    def active_mission(self):
        return deepcopy(self.data['active_mission'])

    def _archive_mission(self):
        mission = self.data['active_mission']
        if mission and mission['status'] == 'assigned':
            raise ValueError('Finish (or reset) the current mission before switching activity')
        self.data['active_mission'] = None

    def set_context(self, context):
        self._archive_mission()
        self.data['context'] = dict(context)
        return deepcopy(self.data['context'])

    @staticmethod
    def _activity_context(activity_id):
        a = next((x for x in curriculum()['activities'] if x['id'] == activity_id), None)
        if not a:
            return None
        ctx = dict(id=a['id'], title=a['title'], type=a['scenario'], location=a['location'], when=a['when'],
                   with_whom=a['with_whom'])
        if a.get('starts_at'):
            ctx['starts_at'] = a['starts_at']
        return ctx

    def start_activity(self, activity_id):
        """The learner is heading to this activity now: it becomes the context for the next mission."""
        ctx = self._activity_context(activity_id)
        if not ctx:
            raise LookupError('Unknown activity')
        self._archive_mission()
        self.data['context'] = ctx
        return deepcopy(ctx)

    def skill_ids(self):
        return {s['id'] for s in self.data['skills']}

    def assign(self, mission):
        if self.data['active_mission']:
            raise ValueError('Complete or reset the active mission first')
        if mission.skill_id not in self.skill_ids() or mission.context_id != self.data['context']['id']:
            raise ValueError('Mission references an unknown skill or context')
        self.data['active_mission'] = mission.model_dump()

    # ---------- evidence ----------
    def record(self, recording_id, title):
        if recording_id in self.recordings:
            return False
        mission = self.data['active_mission']
        if not mission or mission['status'] != 'assigned':
            raise ValueError('Create an active mission before recording')
        self.recordings[recording_id] = {'title': title, 'mission_id': mission['id']}
        return True

    def recording(self, recording_id):
        return deepcopy(self.recordings.get(recording_id))

    def dismiss_recording(self, recording_id, reason):
        self.recordings[recording_id].update(status='irrelevant', reason=reason)

    def has_experience(self, recording_id):
        return recording_id in self.experiences

    def apply(self, result, transcript):
        """Validate everything first, then mutate — so a rejected reflection changes nothing."""
        if result.recording_id in self.experiences:
            return False
        recording = self.recordings.get(result.recording_id)
        mission = self.data['active_mission']
        if not recording or not mission or recording['mission_id'] != result.mission_id or mission['id'] != result.mission_id:
            raise ValueError('Unknown recording or mission mismatch')
        if mission['status'] != 'assigned':
            raise ValueError('Mission already completed')
        referenced = {x.skill_id for x in result.demonstrated + result.gaps} | {result.next_target.skill_id}
        if not referenced <= self.skill_ids():
            raise ValueError('Reflection references an unknown skill')
        if not result.relevant:
            raise ValueError('Irrelevant evidence cannot update learning progress')
        evidence = dedupe_word_evidence([w.model_dump() for w in result.word_evidence])
        known_words = {w['id'] for w in curriculum()['words']}
        unknown = {w['word_id'] for w in evidence} - known_words
        if unknown:
            raise ValueError(f'Reflection references unknown words: {sorted(unknown)}')

        for skill in self.data['skills']:
            if skill['id'] == mission['skill_id']:
                skill['application_score'] = round(min(1, skill['application_score'] + reflection_delta(result)), 2)
        before = {w: k['status'] for w, k in self.knows.items()}
        for ev in evidence:
            k = self.knows.setdefault(ev['word_id'], dict(lesson_passed=False, real_uses=0, contexts=[], struggles=0))
            if ev['outcome'] == 'used_correctly':
                k['real_uses'] += 1
                if mission['context_id'] not in k['contexts']:
                    k['contexts'].append(mission['context_id'])
            else:
                k['struggles'] += 1
            k['status'] = word_status(k['lesson_passed'], k['real_uses'], len(k['contexts']))
        self.experiences[result.recording_id] = {'result': result.model_dump(), 'transcript': transcript, 'word_evidence': evidence,
                                                 'context_id': mission['context_id'], 'mission_title': mission['title'], 'at': now()}
        mission['status'] = 'completed'
        self.data['next_target'] = result.next_target.model_dump()
        return vocabulary_summary(evidence, before, {w: k['status'] for w, k in self.knows.items()})

    # ---------- lessons / resume ----------
    def _lesson_rows(self):
        rows = []
        for lesson in curriculum()['lessons']:
            done = self.completed.get(lesson['id'])
            current = self.current if self.current and self.current['lesson_id'] == lesson['id'] else None
            rows.append(dict(id=lesson['id'], title=lesson['title'], level=lesson['level'], order=lesson['order'],
                             steps=lesson['steps'], best_score=done['best_score'] if done else None,
                             attempts=self.attempts.get(lesson['id'], 0),
                             current_step=current['step'] if current else None,
                             updated_at=current['updated_at'] if current else None))
        return rows

    def _lesson(self, lesson_id, step=None):
        rows = {r['id']: r for r in self._lesson_rows()}
        if lesson_id not in rows:
            raise LookupError('Unknown lesson')
        if lesson_statuses(rows.values())[lesson_id] == 'locked':
            raise ValueError('Lesson is locked; pass the earlier lessons first')
        if step is not None and step > rows[lesson_id]['steps']:
            raise ValueError('Step is beyond the end of the lesson')
        return next(l for l in curriculum()['lessons'] if l['id'] == lesson_id)

    def _introduce(self, words):
        for word_id in words:
            if word_id not in self.knows:
                self.knows[word_id] = dict(lesson_passed=False, real_uses=0, contexts=[], struggles=0, status='introduced')

    def save_step(self, lesson_id, step):
        lesson = self._lesson(lesson_id, step)
        self.current = {'lesson_id': lesson_id, 'step': step, 'updated_at': now()}
        if step > 0:
            self._introduce(lesson['words'])
        return self.progress()

    def complete_lesson(self, lesson_id, score):
        lesson = self._lesson(lesson_id)
        passed = score >= LESSON_PASS_SCORE
        self.attempts[lesson_id] = self.attempts.get(lesson_id, 0) + 1
        if passed:
            best = self.completed.get(lesson_id, {}).get('best_score')
            self.completed[lesson_id] = {'best_score': score if best is None or score > best else best}
            for word_id in lesson['words']:
                k = self.knows.setdefault(word_id, dict(lesson_passed=False, real_uses=0, contexts=[], struggles=0))
                k['lesson_passed'] = True
                k['status'] = word_status(True, k['real_uses'], len(k['contexts']))
            if self.current and self.current['lesson_id'] == lesson_id:
                nxt = next((l for l in curriculum()['lessons'] if l['order'] == lesson['order'] + 1), None)
                self.current = {'lesson_id': nxt['id'], 'step': 0, 'updated_at': now()} if nxt else \
                    {'lesson_id': lesson_id, 'step': lesson['steps'], 'updated_at': now()}
        else:
            self._introduce(lesson['words'])
            self.current = {'lesson_id': lesson_id, 'step': 0, 'updated_at': now()}
        return {'passed': passed, 'progress': self.progress()}

    def progress(self):
        cur = curriculum()
        words = []
        level_rows = {lv['code']: dict(lv, total=0, practiced=0, fluent=0) for lv in cur['levels']}
        for lesson in sorted(cur['lessons'], key=lambda l: l['order']):
            for word_id in lesson['words']:
                w = next(x for x in cur['words'] if x['id'] == word_id)
                k = self.knows.get(word_id)
                status = k['status'] if k else 'new'
                words.append(dict(id=word_id, lemma=w['lemma'], meaning=w['meaning'], lesson_id=lesson['id'],
                                  status=status, real_uses=k['real_uses'] if k else 0,
                                  contexts=len(k['contexts']) if k else 0, struggles=k['struggles'] if k else 0))
                row = level_rows[lesson['level']]
                row['total'] += 1
                row['practiced'] += status in ('practiced', 'fluent')
                row['fluent'] += status == 'fluent'
        lessons = self._lesson_rows()
        return build_progress(self.data['learner_id'], cur['language'], level_summary(level_rows.values()),
                              lessons, words, lesson_statuses(lessons))

    def focus_words(self, limit=5):
        """Words the learner has practiced in lessons but not yet used fluently, needed by the current context."""
        scenario = scenario_for(self.data['context'])
        if not scenario:
            return []
        cur = curriculum()
        needed = next(s['words'] for s in cur['scenarios'] if s['id'] == scenario)
        info = {w['id']: w for w in cur['words']}
        rows = [(w, self.knows[w]) for w in needed if w in self.knows and self.knows[w]['status'] == 'practiced']
        rows.sort(key=lambda r: (-r[1]['struggles'], -r[1]['real_uses'], r[0]))
        return [dict(id=w, lemma=info[w]['lemma'], meaning=info[w]['meaning'], real_uses=k['real_uses']) for w, k in rows[:limit]]

    # ---------- graph ----------
    def _experience_rows(self):
        rows = []
        for rid, exp in self.experiences.items():
            r = exp['result']
            rows.append(dict(recording_id=rid, title=self.recordings[rid]['title'], mission_id=r['mission_id'],
                             mission_title=exp['mission_title'], context_id=exp['context_id'],
                             demonstrated=[d['skill_id'] for d in r['demonstrated']],
                             gaps=[dict(skill_id=g['skill_id'], name=g['name'], evidence=g['evidence']) for g in r['gaps']],
                             used=[w['word_id'] for w in exp['word_evidence'] if w['outcome'] == 'used_correctly'],
                             struggled=[w['word_id'] for w in exp['word_evidence'] if w['outcome'] != 'used_correctly']))
        return rows

    def activities(self):
        cur = curriculum()
        info = {w['id']: w for w in cur['words']}
        scenarios = {s['id']: s for s in cur['scenarios']}
        progress = self.progress()
        lesson_status = {l['id']: l['status'] for l in progress['lessons']}
        status = lambda w: self.knows[w]['status'] if w in self.knows else 'new'
        result = []
        for a in cur['activities']:
            sc = scenarios[a['scenario']]
            visits = [dict(at=e['at'], success=e['result']['success_score'],
                           used=[info[w['word_id']]['lemma'] for w in e['word_evidence'] if w['outcome'] == 'used_correctly'],
                           struggled=[info[w['word_id']]['lemma'] for w in e['word_evidence'] if w['outcome'] != 'used_correctly'])
                      for e in self.experiences.values() if e['context_id'] == a['id']]
            phrases = [dict(id=p['id'], text=p['text'], meaning=p['meaning'],
                            words=[dict(id=w, lemma=info[w]['lemma'], status=status(w)) for w in p['words']])
                       for p in sc.get('phrases', [])]
            practice = [dict(id=w, lemma=info[w]['lemma'], meaning=info[w]['meaning'], status=k['status'],
                             struggles=k['struggles'], real_uses=k['real_uses'])
                        for w in sc['words'] if (k := self.knows.get(w)) and k['status'] != 'fluent'
                        and (k['struggles'] > 0 or k['status'] == 'practiced')]
            lesson_words = [dict(id=l['id'], title=l['title'], order=l['order'], word=info[w]['lemma'])
                            for l in cur['lessons'] if l['id'] not in self.completed
                            for w in l['words'] if w in sc['words'] and status(w) != 'fluent']
            row = dict(activity=self._activity_context(a['id']), scenario=sc['id'], scenario_name=sc['name'],
                       is_current=self.data['context']['id'] == a['id'], visits=visits, phrases=phrases,
                       practice=practice, lesson_words=lesson_words)
            result.append(build_activity(row, lesson_status))
        return result

    def graph(self):
        return build_graph(self.state(), self._experience_rows(), self.progress(), self.activities())


def vocabulary_summary(evidence, before, after):
    """What changed in the learner's vocabulary because of one recording."""
    used = [e['word_id'] for e in evidence if e['outcome'] == 'used_correctly']
    return {'words_used': used,
            'words_struggled': [e['word_id'] for e in evidence if e['outcome'] != 'used_correctly'],
            'newly_fluent': [w for w in used if after.get(w) == 'fluent' and before.get(w) != 'fluent']}
