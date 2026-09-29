"""Learner knowledge-graph behaviour: words, fluency, level, lessons and resume point.

StoreContract runs against the in-memory store always, and against a real Neo4j database when
NEO4J_TEST_URI is set (it RESETS the ApplyLoop nodes in that database):

    NEO4J_TEST_URI=neo4j+s://xxxx.databases.neo4j.io NEO4J_TEST_PASSWORD=... \
      .venv/bin/python -m unittest server.test_graph_store -v
"""
import os
import re
import unittest
from unittest.mock import patch
from server.demo_seed import example
from server.learning_rules import curriculum, level_summary, word_status
from server.memory_store import DemoStore
from server.neo4j_store import Q, Neo4jStore
from server.schemas import Mission, ReflectionResult


def mission():
    return Mission.model_validate(example('mission'))


def reflection(**update):
    return ReflectionResult.model_validate({**example('reflection'), **update})


class StoreContract:
    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        self.store = self.make_store()
        self.store.reset()

    def words(self):
        return {w['id']: w for w in self.store.progress()['words']}

    def run_mission(self, result=None):
        self.store.assign(mission())
        self.assertTrue(self.store.record('plaud-recording-id', 'Evidence'))
        return self.store.apply(result or reflection(), 'I ordered my food and asked for water in Spanish.')

    def test_seeded_learner_level_and_resume_point(self):
        progress = self.store.progress()
        self.assertEqual(progress['level']['current'], 'A1')
        self.assertIsNone(progress['level']['fluent_level'])
        self.assertEqual(progress['resume']['lesson_id'], 'es-a2-01')
        self.assertEqual(progress['resume']['step'], 3)
        statuses = {l['id']: l['status'] for l in progress['lessons']}
        self.assertEqual(statuses['es-a1-04'], 'completed')
        self.assertEqual(statuses['es-a2-01'], 'current')
        self.assertEqual(statuses['es-a2-03'], 'locked')
        words = self.words()
        self.assertEqual(words['hola']['status'], 'fluent')
        self.assertEqual(words['quiero']['status'], 'practiced')
        self.assertEqual(words['la-salsa']['status'], 'introduced')
        self.assertEqual(words['donde-esta']['status'], 'new')
        focus = [w['id'] for w in self.store.state()['focus_words']]
        self.assertEqual(focus[:3], ['el-agua', 'por-favor', 'quiero'])

    def test_reflection_makes_words_fluent_and_records_struggles(self):
        summary = self.run_mission()
        self.assertEqual(sorted(summary['newly_fluent']), ['el-agua', 'quiero'])
        self.assertEqual(sorted(summary['words_struggled']), ['la-salsa', 'roja', 'verde'])
        words = self.words()
        self.assertEqual(words['quiero']['status'], 'fluent')
        self.assertEqual(words['quiero']['contexts'], 2)
        self.assertEqual(words['verde']['struggles'], 1)
        state = self.store.state()
        self.assertEqual(state['skills'][0]['application_score'], .39)
        self.assertEqual(state['active_mission']['status'], 'completed')
        self.assertEqual(state['next_target']['skill_id'], 'follow-up-questions')
        graph = self.store.graph()
        labels = {e['label'] for e in graph['edges']}
        self.assertTrue({'USED', 'STRUGGLED_WITH', 'AT_LEVEL', 'CURRENTLY_AT', 'PRACTICES'} <= labels)
        self.assertEqual(len({n['id'] for n in graph['nodes']}), len(graph['nodes']))
        self.assertFalse(self.store.apply(reflection(), 'duplicate'))
        self.assertEqual(self.words()['quiero']['real_uses'], 2)

    def test_rejected_reflection_changes_nothing(self):
        self.store.assign(mission())
        self.store.record('plaud-recording-id', 'Evidence')
        bad = reflection(word_evidence=[{'word_id': 'not-a-word', 'outcome': 'used_correctly', 'evidence': 'x'}])
        with self.assertRaises(ValueError):
            self.store.apply(bad, 'transcript text')
        self.assertEqual(self.store.state()['skills'][0]['application_score'], .25)
        self.assertEqual(self.store.state()['active_mission']['status'], 'assigned')
        self.assertFalse(self.store.has_experience('plaud-recording-id'))

    def test_resume_point_and_lesson_rules(self):
        self.assertEqual(self.store.save_step('es-a2-01', 5)['resume']['step'], 5)
        with self.assertRaises(ValueError):
            self.store.save_step('es-a2-01', 99)
        with self.assertRaises(ValueError):
            self.store.save_step('es-a2-03', 1)
        with self.assertRaises(LookupError):
            self.store.save_step('nope', 1)
        failed = self.store.complete_lesson('es-a2-01', .5)
        self.assertFalse(failed['passed'])
        self.assertEqual(failed['progress']['resume'], {**failed['progress']['resume'], 'lesson_id': 'es-a2-01', 'step': 0})
        passed = self.store.complete_lesson('es-a2-01', .9)
        self.assertTrue(passed['passed'])
        progress = passed['progress']
        self.assertEqual(progress['resume']['lesson_id'], 'es-a2-02')
        self.assertEqual(progress['resume']['step'], 0)
        lesson = next(l for l in progress['lessons'] if l['id'] == 'es-a2-01')
        self.assertEqual((lesson['status'], lesson['best_score'], lesson['attempts']), ('completed', .9, 2))
        self.assertEqual(self.words()['verde']['status'], 'practiced')

    def test_level_rises_when_lessons_are_passed(self):
        for lesson_id in ['es-a2-01', 'es-a2-02', 'es-a2-03', 'es-a2-04']:
            self.assertEqual(self.store.complete_lesson(lesson_id, .85)['progress']['level']['current'], 'A1')
        progress = self.store.complete_lesson('es-a2-05', .85)['progress']
        self.assertEqual(progress['level']['current'], 'A2')
        self.assertEqual(progress['resume']['lesson_id'], 'es-a2-05')

    def activity(self, activity_id):
        return next(a for a in self.store.activities() if a['id'] == activity_id)

    def test_activities_brief_what_you_can_say_and_where_to_start(self):
        acts = {a['id']: a for a in self.store.activities()}
        self.assertEqual(set(acts), {'dinner-001', 'class-001', 'dance-001'})
        self.assertTrue(acts['dinner-001']['is_current'])
        self.assertEqual(acts['dance-001']['with_whom'], 'Instructor Diego, from Mexico')
        dinner = [p['text'] for p in acts['dinner-001']['can_say']]
        self.assertIn('La cuenta, por favor.', dinner)
        dance = acts['dance-001']
        self.assertIn('¿De dónde eres?', [p['text'] for p in dance['almost']])  # only 'de dónde eres' missing
        self.assertEqual(dance['visits'], 0)
        self.assertIn('First time', dance['pick_up'])
        self.assertEqual(dance['lesson']['id'], 'es-a2-02')  # first unfinished lesson teaching dance words (izquierda/derecha)
        class_ = acts['class-001']
        self.assertIn('Más despacio, por favor.', [p['text'] for p in class_['can_say']])  # used once in real class
        self.assertEqual(class_['lesson']['id'], 'es-a2-04')

    def test_switching_activity_and_remembering_where_you_left_off(self):
        self.run_mission()                                   # dinner, restaurant replay
        with self.assertRaises(LookupError):
            self.store.start_activity('nope')
        ctx = self.store.start_activity('dance-001')
        self.assertEqual((ctx['id'], ctx['type']), ('dance-001', 'dance-class'))
        state = self.store.state()
        self.assertIsNone(state['active_mission'])
        self.assertEqual(state['context']['id'], 'dance-001')
        from server.agents.reflection_agent import demo_reflection
        import asyncio
        from server.agents.opportunity_agent import demo_opportunity
        mission = asyncio.run(demo_opportunity(state))
        self.assertEqual(mission.skill_id, 'dance-small-talk')
        self.store.assign(mission)
        with self.assertRaises(ValueError):
            self.store.start_activity('class-001')          # mission in progress
        self.store.record('dance-rec', 'Dance class')
        result = asyncio.run(demo_reflection(self.store.active_mission(), 'dance-rec', 'transcript text'))
        summary = self.store.apply(result, 'transcript text')
        self.assertEqual(summary['newly_fluent'], [])        # me llamo: 2 uses, but both at dance-001 -> not fluent yet
        me_llamo = next(w for w in self.store.progress()['words'] if w['id'] == 'me-llamo')
        self.assertEqual((me_llamo['real_uses'], me_llamo['contexts'], me_llamo['status']), (2, 1, 'practiced'))
        dance = self.activity('dance-001')
        self.assertEqual(dance['visits'], 1)
        self.assertIn('otra vez', dance['pick_up'])
        self.assertIn('otra vez', [w['lemma'] for w in dance['practice']])
        dinner = self.activity('dinner-001')
        self.assertEqual(dinner['visits'], 1)
        self.assertIn('la salsa', dinner['last_visit']['struggled'])
        graph = self.store.graph()
        self.assertEqual(len({e['id'] for e in graph['edges']}), len(graph['edges']))
        ids = {n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))


class MemoryStoreTest(StoreContract, unittest.TestCase):
    def make_store(self):
        return DemoStore()


def test_neo4j_store():
    from urllib.parse import urlparse
    from server.settings import NEO4J_URI, NEO4J_DATABASE
    uri = os.environ['NEO4J_TEST_URI']
    database = os.getenv('NEO4J_TEST_DATABASE') or None
    target = urlparse(uri)
    app_target = urlparse(NEO4J_URI)
    if (target.hostname, target.port or 7687, database or 'neo4j') == (app_target.hostname, app_target.port or 7687, NEO4J_DATABASE or 'neo4j'):
        raise RuntimeError('Refusing to reset the configured application database')
    return Neo4jStore(uri, os.getenv('NEO4J_TEST_USERNAME', 'neo4j'),
                      os.environ['NEO4J_TEST_PASSWORD'], database)


@unittest.skipUnless(os.getenv('NEO4J_TEST_URI'), 'set NEO4J_TEST_URI to run against a real Neo4j database')
class Neo4jStoreTest(StoreContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = test_neo4j_store()
        cls.shared.connect()

    @classmethod
    def tearDownClass(cls):
        cls.shared.reset()  # leave a clean, freshly seeded demo graph behind
        cls.shared.close()

    def make_store(self):
        return self.shared


class RulesTest(unittest.TestCase):
    def test_word_status_needs_two_real_contexts_for_fluency(self):
        self.assertEqual(word_status(True, 0, 0), 'practiced')
        self.assertEqual(word_status(False, 1, 1), 'practiced')
        self.assertEqual(word_status(True, 2, 1), 'practiced')
        self.assertEqual(word_status(False, 2, 2), 'fluent')
        self.assertEqual(word_status(False, 0, 0), 'introduced')

    def test_level_must_be_reached_in_order(self):
        rows = [dict(code='A1', name='', order=1, total=10, practiced=7, fluent=0),
                dict(code='A2', name='', order=2, total=10, practiced=10, fluent=10)]
        summary = level_summary(rows)
        self.assertEqual((summary['current'], summary['fluent_level']), ('Pre-A1', None))

    def test_curriculum_is_consistent(self):
        self.assertGreater(len(curriculum()['words']), 40)


# ---------- Neo4jStore glue check without a database ----------
PARAM = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*)')
NAME_BY_QUERY = {q: name for name, q in Q.items()}


class FakeTx:
    """Asserts every $param a query references is supplied, and returns plausible rows."""
    def __init__(self, test):
        self.test, self.calls = test, []

    def run(self, query, **params):
        name = NAME_BY_QUERY[query]
        missing = set(PARAM.findall(query)) - set(params)
        self.test.assertFalse(missing, f'{name} is missing parameters {missing}')
        self.calls.append(name)
        return self.test.rows(name, params)


class FakeSession:
    def __init__(self, tx):
        self.tx = tx

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_read(self, fn, *args):
        return fn(self.tx, *args)

    execute_write = execute_read

    def run(self, statement):
        return type('R', (), {'consume': lambda self: None})()


class FakeDriver:
    def __init__(self, tx):
        self.tx = tx

    def session(self, database=None):
        return FakeSession(self.tx)

    def verify_connectivity(self):
        pass

    def close(self):
        pass


class Neo4jGlueTest(unittest.TestCase):
    def setUp(self):
        self.tx = FakeTx(self)
        self.store = object.__new__(Neo4jStore)
        self.store.driver, self.store.database, self.store.learner_id = FakeDriver(self.tx), None, 'demo'
        self.mission = {**example('mission'), 'status': 'assigned'}

    def rows(self, name, params):
        cur, state = curriculum(), example('state')
        seed = cur['learner_seed']
        if name == 'learner':
            return [{'learner': {'id': 'demo', 'goal': state['goal']}, 'context': state['context'], 'mission': self.mission}]
        if name == 'skills':
            return state['skills']
        if name == 'lessons':
            return [dict(id=l['id'], title=l['title'], order=l['order'], steps=l['steps'], level=l['level'],
                         best_score=seed['completed'].get(l['id']), attempts=1,
                         current_step=seed['current']['step'] if l['id'] == seed['current']['lesson_id'] else None,
                         updated_at='2026-09-29T00:00:00Z') for l in cur['lessons']]
        if name == 'words':
            return [dict(id=w, lemma=w, meaning=w, lesson_id=l['id'], status='practiced', real_uses=0, contexts=0, struggles=0)
                    for l in cur['lessons'] for w in l['words']]
        if name == 'levels':
            return [dict(code='A1', name='Beginner', order=1, total=30, practiced=30, fluent=3)]
        if name in ('experience_exists', 'recording_exists'):
            return [{'exists': False}]
        if name in ('learner_exists', 'graph_exists'):
            return [{'exists': True}]
        if name == 'apply_check':
            return [{'mission': self.mission, 'skill_ids': [s['id'] for s in state['skills']], 'word_ids': params['word_ids']}]
        if name == 'application_score':
            return [{'score': .25}]
        if name in ('assign', 'record'):
            return [{'id': 'x'}]
        if name == 'set_context':
            return [{'context': params['context']}]
        if name == 'next_lesson':
            return [{'id': 'es-a2-02'}]
        if name == 'focus_words':
            return [dict(id='quiero', lemma='quiero', meaning='I want', real_uses=1)]
        if name == 'activity_exists':
            return [{'exists': True}]
        if name == 'start_activity':
            return [{'context': {'id': 'dance-001', 'title': 'Dance class', 'type': 'dance-class', 'with_whom': None}}]
        if name == 'activities':
            return [{'activity': {'id': 'dance-001', 'title': 'Dance class', 'location': 'x', 'when': 'y', 'with_whom': 'z'},
                     'scenario': 'dance-class', 'scenario_name': 'Dance class', 'is_current': False, 'visits': [],
                     'phrases': [{'id': 'd', 'text': 't', 'meaning': 'm', 'words': [{'id': 'hola', 'lemma': 'hola', 'status': 'fluent'}]}],
                     'practice': [], 'lesson_words': [{'id': 'es-a2-03', 'title': 'Small talk', 'order': 7, 'word': 'me gusta'}]}]
        if name == 'recording':
            return [{'title': 'Evidence', 'mission_id': self.mission['id']}]
        return []

    def test_startup_failure_closes_driver_and_preserves_graph(self):
        with patch.object(self.store.driver, 'session', side_effect=RuntimeError('Constraint failure')), \
                patch.object(self.store.driver, 'close') as close, patch.object(self.store, 'reset') as reset:
            with self.assertRaisesRegex(RuntimeError, 'Neo4j startup failed'):
                self.store.connect()
            close.assert_called_once()
            reset.assert_not_called()
        with patch.object(self.store, '_read', side_effect=[False, True]), \
                patch.object(self.store.driver, 'close') as close, patch.object(self.store, 'reset') as reset:
            with self.assertRaisesRegex(RuntimeError, 'Neo4j startup failed'):
                self.store.connect()
            close.assert_called_once()
            reset.assert_not_called()

    def test_every_store_method_passes_its_parameters(self):
        s = self.store
        s.connect()
        s.reset()
        s.state(); s.progress(); s.graph()
        self.mission = None
        s.set_context({'id': 'c', 'title': 't', 'type': 'restaurant', 'location': 'SF'})
        s.assign(mission())
        self.mission = {**example('mission'), 'status': 'assigned'}
        self.assertTrue(s.record('r1', 'title'))
        self.assertEqual(s.recording('r1')['mission_id'], 'mission-001')
        summary = s.apply(reflection(recording_id='r1'), 'transcript text')
        self.assertIn('word_evidence', self.tx.calls)
        self.assertEqual(summary['words_used'], ['quiero', 'el-agua'])
        s.save_step('es-a2-01', 4)
        self.assertTrue(s.complete_lesson('es-a2-01', .9)['passed'])
        self.assertEqual(s.start_activity('dance-001')['id'], 'dance-001')
        self.assertEqual(len(s.activities()), 1)
        self.assertFalse(s.complete_lesson('es-a2-01', .2)['passed'])
        self.assertEqual(set(Q) - {'graph_exists'} - set(self.tx.calls), set(), 'every query is exercised')


if __name__ == '__main__':
    unittest.main()
