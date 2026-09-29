"""Neo4j learner knowledge graph.

Graph model (every node also carries the :ApplyLoop label so reset never touches unrelated data):

  (:Learner {id, goal, language, next_target_skill_id, next_target_reason})
    -[:HAS_CONTEXT]->(:Context)            current real-world context
    -[:LEARNING {knowledge_score, application_score}]->(:Skill)
    -[:ACTIVE_MISSION]->(:Mission)-[:TARGETS]->(:Skill), -[:TRIGGERED_BY]->(:Context)
    -[:HAD]->(:Experience)-[:COMPLETED]->(:Mission)
                          -[:EVIDENCED_BY]->(:Recording)-[:FOR_MISSION]->(:Mission)
                          -[:DEMONSTRATED]->(:Skill)
                          -[:REVEALED]->(:Gap)-[:ABOUT]->(:Skill)
                          -[:USED]->(:Word)             correct real-world use
                          -[:STRUGGLED_WITH]->(:Word)   misused / not understood
    -[:KNOWS {status, lesson_passed, real_uses, contexts, struggles}]->(:Word)
    -[:CURRENTLY_AT {step, updated_at}]->(:Lesson)      resume point (exactly one)
    -[:COMPLETED {best_score, at}]->(:Lesson)           passed lessons
    -[:ATTEMPTED {score, passed, at}]->(:Lesson)        every attempt (history)

  (:Level)<-[:IN_LEVEL]-(:Lesson)-[:TEACHES {position}]->(:Word)
  (:Lesson)-[:NEXT]->(:Lesson)
  (:Scenario)-[:NEEDS]->(:Word)                           e.g. restaurant vocabulary
  (:Scenario)-[:HAS_PHRASE]->(:Phrase)-[:USES]->(:Word)   things you can say there
  (:Learner)-[:DOES]->(:Activity:Context)-[:IS_A]->(:Scenario)   Spanish class, dinner, dance class
  (:Skill)-[:USES_WORD]->(:Word)

Rules live in learning_rules.py. The LLM only reports evidence; these transactions decide scores,
statuses and levels, and each mutation commits atomically or not at all.
"""
from neo4j import GraphDatabase
from server.demo_seed import example
from server.graph_view import build_activity, build_graph, build_progress
from server.learning_rules import (FLUENT_MIN_CONTEXTS, FLUENT_MIN_REAL_USES, LESSON_PASS_SCORE, application_delta,
                                   curriculum, dedupe_word_evidence, lesson_statuses, level_summary, scenario_for,
                                   seed_knows)
from server.settings import DEMO_MODE
from server.memory_store import DemoStore, vocabulary_summary  # noqa: F401  (re-exported for existing imports)

# Mirrors learning_rules.word_status(). `k` must be a KNOWS relationship.
STATUS_CYPHER = """CASE
  WHEN k.real_uses >= $fluent_uses AND size(k.contexts) >= $fluent_contexts THEN 'fluent'
  WHEN k.lesson_passed OR k.real_uses >= 1 THEN 'practiced'
  ELSE 'introduced' END"""
RULES = {'fluent_uses': FLUENT_MIN_REAL_USES, 'fluent_contexts': FLUENT_MIN_CONTEXTS}

CONSTRAINTS = [f'CREATE CONSTRAINT applyloop_{label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE'
               for label, key in [('Learner', 'id'), ('Word', 'id'), ('Lesson', 'id'), ('Level', 'code'),
                                  ('Scenario', 'id'), ('Mission', 'id'), ('Recording', 'id'), ('Experience', 'id'),
                                  ('Phrase', 'id'), ('Activity', 'id')]]

Q = dict(
    wipe='MATCH (n:ApplyLoop) DETACH DELETE n',
    seed_levels='UNWIND $levels AS lv CREATE (:ApplyLoop:Level {code: lv.code, name: lv.name, order: lv.order})',
    seed_words='UNWIND $words AS w CREATE (:ApplyLoop:Word {id: w.id, lemma: w.lemma, meaning: w.meaning, pos: w.pos, language: $language})',
    seed_lessons="""
        UNWIND $lessons AS les
        MATCH (lv:Level:ApplyLoop {code: les.level})
        CREATE (l:ApplyLoop:Lesson {id: les.id, order: les.order, title: les.title, steps: les.steps})-[:IN_LEVEL]->(lv)
        WITH l, les
        UNWIND range(0, size(les.words) - 1) AS i
        MATCH (w:Word:ApplyLoop {id: les.words[i]})
        CREATE (l)-[:TEACHES {position: i}]->(w)""",
    seed_next="""
        MATCH (a:Lesson:ApplyLoop), (b:Lesson:ApplyLoop) WHERE b.order = a.order + 1
        CREATE (a)-[:NEXT]->(b)""",
    seed_scenarios="""
        UNWIND $scenarios AS sc
        CREATE (s:ApplyLoop:Scenario {id: sc.id, name: sc.name, skill: sc.skill})
        WITH s, sc
        UNWIND sc.words AS wid
        MATCH (w:Word:ApplyLoop {id: wid})
        CREATE (s)-[:NEEDS]->(w)""",
    seed_learner="""
        CREATE (l:ApplyLoop:Learner {id: $learner_id, goal: $goal, language: $language})
        WITH l
        UNWIND range(0, size($skills) - 1) AS i
        WITH l, i, $skills[i] AS sk
        CREATE (s:ApplyLoop:Skill {id: sk.id, name: sk.name, order: i})
        CREATE (l)-[:LEARNING {knowledge_score: sk.knowledge_score, application_score: sk.application_score}]->(s)
        WITH s, sk
        UNWIND sk.words AS wid
        MATCH (w:Word:ApplyLoop {id: wid})
        CREATE (s)-[:USES_WORD]->(w)""",
    seed_completed="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        UNWIND $completed AS c
        MATCH (les:Lesson:ApplyLoop {id: c.lesson_id})
        CREATE (l)-[:COMPLETED {best_score: c.score, at: datetime()}]->(les)
        CREATE (l)-[:ATTEMPTED {score: c.score, passed: true, at: datetime()}]->(les)""",
    seed_knows="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        UNWIND $knows AS k
        MATCH (w:Word:ApplyLoop {id: k.word_id})
        CREATE (l)-[:KNOWS {status: k.status, lesson_passed: k.lesson_passed, real_uses: k.real_uses,
                            contexts: k.contexts, struggles: 0, first_seen: datetime(), last_seen: datetime()}]->(w)""",
    set_resume="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        OPTIONAL MATCH (l)-[old:CURRENTLY_AT]->(:Lesson)
        DELETE old
        WITH DISTINCT l
        MATCH (les:Lesson:ApplyLoop {id: $lesson_id})
        CREATE (l)-[:CURRENTLY_AT {step: $step, updated_at: datetime()}]->(les)""",
    learner="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        OPTIONAL MATCH (l)-[:HAS_CONTEXT]->(c:Context)
        OPTIONAL MATCH (l)-[:ACTIVE_MISSION]->(m:Mission)
        RETURN l {.*} AS learner, c {.*} AS context, m {.*} AS mission""",
    skills="""
        MATCH (:Learner:ApplyLoop {id: $learner_id})-[r:LEARNING]->(s:Skill)
        RETURN s.id AS id, s.name AS name, r.knowledge_score AS knowledge_score,
               r.application_score AS application_score
        ORDER BY s.order""",
    set_context="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        WHERE NOT (l)-[:ACTIVE_MISSION]->(:Mission {status: 'assigned'})
        OPTIONAL MATCH (l)-[am:ACTIVE_MISSION]->(:Mission)
        DELETE am
        WITH DISTINCT l
        OPTIONAL MATCH (l)-[old:HAS_CONTEXT]->(:Context)
        DELETE old
        WITH DISTINCT l
        CREATE (c:ApplyLoop:Context {id: $context.id, title: $context.title, type: $context.type, location: $context.location})
        CREATE (l)-[:HAS_CONTEXT]->(c)
        RETURN c {.*} AS context""",
    assign="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})-[:HAS_CONTEXT]->(c:Context {id: $mission.context_id})
        WHERE NOT (l)-[:ACTIVE_MISSION]->(:Mission)
        MATCH (l)-[:LEARNING]->(s:Skill {id: $mission.skill_id})
        CREATE (m:ApplyLoop:Mission)
        SET m = $mission
        CREATE (l)-[:ACTIVE_MISSION]->(m), (m)-[:TARGETS]->(s), (m)-[:TRIGGERED_BY]->(c)
        RETURN m.id AS id""",
    recording_exists='MATCH (r:Recording:ApplyLoop {id: $rid}) RETURN count(r) > 0 AS exists',
    record="""
        MATCH (:Learner:ApplyLoop {id: $learner_id})-[:ACTIVE_MISSION]->(m:Mission {status: 'assigned'})
        CREATE (r:ApplyLoop:Recording {id: $rid, title: $title, detected_at: datetime()})-[:FOR_MISSION]->(m)
        RETURN r.id AS id""",
    recording="""
        MATCH (r:Recording:ApplyLoop {id: $rid})-[:FOR_MISSION]->(m:Mission)
        RETURN r.title AS title, m.id AS mission_id""",
    experience_exists='MATCH (e:Experience:ApplyLoop {id: $rid}) RETURN count(e) > 0 AS exists',
    apply_check="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})-[:ACTIVE_MISSION]->(m:Mission)
        MATCH (:Recording:ApplyLoop {id: $rid})-[:FOR_MISSION]->(m)
        RETURN m {.*} AS mission,
               COLLECT { MATCH (l)-[:LEARNING]->(s:Skill) RETURN s.id } AS skill_ids,
               COLLECT { MATCH (w:Word:ApplyLoop) WHERE w.id IN $word_ids RETURN w.id } AS word_ids""",
    word_statuses="""
        MATCH (:Learner:ApplyLoop {id: $learner_id})-[k:KNOWS]->(w:Word)
        WHERE w.id IN $word_ids
        RETURN w.id AS id, k.status AS status""",
    application_score="""
        MATCH (:Learner:ApplyLoop {id: $learner_id})-[r:LEARNING]->(:Skill {id: $skill_id})
        RETURN r.application_score AS score""",
    set_application_score="""
        MATCH (:Learner:ApplyLoop {id: $learner_id})-[r:LEARNING]->(:Skill {id: $skill_id})
        SET r.application_score = $score""",
    create_experience="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})-[:ACTIVE_MISSION]->(m:Mission {id: $mission_id})
        MATCH (r:Recording:ApplyLoop {id: $rid})
        CREATE (e:ApplyLoop:Experience {id: $rid, success_score: $success_score, transcript: $transcript, created_at: datetime()})
        CREATE (l)-[:HAD]->(e), (e)-[:COMPLETED]->(m), (e)-[:EVIDENCED_BY]->(r)
        SET m.status = 'completed',
            l.next_target_skill_id = $next_target.skill_id,
            l.next_target_reason = $next_target.reason""",
    demonstrated="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})-[:HAD]->(e:Experience {id: $rid})
        UNWIND $demonstrated AS d
        MATCH (l)-[:LEARNING]->(s:Skill {id: d.skill_id})
        CREATE (e)-[:DEMONSTRATED {evidence: d.evidence, confidence: d.confidence}]->(s)""",
    gaps="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})-[:HAD]->(e:Experience {id: $rid})
        UNWIND $gaps AS g
        MATCH (l)-[:LEARNING]->(s:Skill {id: g.skill_id})
        CREATE (e)-[:REVEALED]->(gap:ApplyLoop:Gap {id: $rid + ':' + g.skill_id, name: g.name,
                                                     evidence: g.evidence, confidence: g.confidence})-[:ABOUT]->(s)""",
    word_evidence=f"""
        MATCH (l:Learner:ApplyLoop {{id: $learner_id}})-[:HAD]->(e:Experience {{id: $rid}})
        UNWIND $evidence AS ev
        MATCH (w:Word:ApplyLoop {{id: ev.word_id}})
        MERGE (l)-[k:KNOWS]->(w)
          ON CREATE SET k.lesson_passed = false, k.real_uses = 0, k.contexts = [], k.struggles = 0,
                        k.first_seen = datetime()
        SET k.last_seen = datetime(),
            k.real_uses = k.real_uses + CASE WHEN ev.outcome = 'used_correctly' THEN 1 ELSE 0 END,
            k.struggles = k.struggles + CASE WHEN ev.outcome = 'used_correctly' THEN 0 ELSE 1 END,
            k.contexts = CASE WHEN ev.outcome = 'used_correctly' AND NOT $context_id IN k.contexts
                              THEN k.contexts + $context_id ELSE k.contexts END
        SET k.status = {STATUS_CYPHER}
        FOREACH (_ IN CASE WHEN ev.outcome = 'used_correctly' THEN [1] ELSE [] END |
          CREATE (e)-[:USED {{evidence: ev.evidence}}]->(w))
        FOREACH (_ IN CASE WHEN ev.outcome = 'used_correctly' THEN [] ELSE [1] END |
          CREATE (e)-[:STRUGGLED_WITH {{outcome: ev.outcome, evidence: ev.evidence}}]->(w))""",
    lessons="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        MATCH (les:Lesson:ApplyLoop)-[:IN_LEVEL]->(lv:Level)
        OPTIONAL MATCH (l)-[c:COMPLETED]->(les)
        OPTIONAL MATCH (l)-[cur:CURRENTLY_AT]->(les)
        RETURN les.id AS id, les.title AS title, les.order AS order, les.steps AS steps, lv.code AS level,
               c.best_score AS best_score, COUNT { (l)-[:ATTEMPTED]->(les) } AS attempts,
               cur.step AS current_step, toString(cur.updated_at) AS updated_at
        ORDER BY les.order""",
    words="""
        MATCH (les:Lesson:ApplyLoop)-[t:TEACHES]->(w:Word)
        OPTIONAL MATCH (:Learner:ApplyLoop {id: $learner_id})-[k:KNOWS]->(w)
        RETURN w.id AS id, w.lemma AS lemma, w.meaning AS meaning, les.id AS lesson_id,
               coalesce(k.status, 'new') AS status, coalesce(k.real_uses, 0) AS real_uses,
               size(coalesce(k.contexts, [])) AS contexts, coalesce(k.struggles, 0) AS struggles
        ORDER BY les.order, t.position""",
    levels="""
        MATCH (lv:Level:ApplyLoop)<-[:IN_LEVEL]-(:Lesson)-[:TEACHES]->(w:Word)
        OPTIONAL MATCH (:Learner:ApplyLoop {id: $learner_id})-[k:KNOWS]->(w)
        RETURN lv.code AS code, lv.name AS name, lv.order AS order, count(DISTINCT w) AS total,
               count(DISTINCT CASE WHEN k.status IN ['practiced', 'fluent'] THEN w END) AS practiced,
               count(DISTINCT CASE WHEN k.status = 'fluent' THEN w END) AS fluent""",
    introduce_words="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        MATCH (:Lesson:ApplyLoop {id: $lesson_id})-[:TEACHES]->(w:Word)
        MERGE (l)-[k:KNOWS]->(w)
          ON CREATE SET k.lesson_passed = false, k.real_uses = 0, k.contexts = [], k.struggles = 0,
                        k.status = 'introduced', k.first_seen = datetime(), k.last_seen = datetime()""",
    attempt="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id}), (les:Lesson:ApplyLoop {id: $lesson_id})
        CREATE (l)-[:ATTEMPTED {score: $score, passed: $passed, at: datetime()}]->(les)""",
    pass_lesson=f"""
        MATCH (l:Learner:ApplyLoop {{id: $learner_id}}), (les:Lesson:ApplyLoop {{id: $lesson_id}})
        MERGE (l)-[c:COMPLETED]->(les)
        SET c.best_score = CASE WHEN c.best_score IS NULL OR $score > c.best_score THEN $score ELSE c.best_score END,
            c.at = datetime()
        WITH l, les
        MATCH (les)-[:TEACHES]->(w:Word)
        MERGE (l)-[k:KNOWS]->(w)
          ON CREATE SET k.real_uses = 0, k.contexts = [], k.struggles = 0, k.first_seen = datetime()
        SET k.lesson_passed = true, k.last_seen = datetime()
        SET k.status = {STATUS_CYPHER}""",
    next_lesson="""
        MATCH (:Lesson:ApplyLoop {id: $lesson_id})-[:NEXT]->(n:Lesson)
        RETURN n.id AS id""",
    focus_words="""
        MATCH (:Learner:ApplyLoop {id: $learner_id})-[k:KNOWS {status: 'practiced'}]->(w:Word)
              <-[:NEEDS]-(:Scenario:ApplyLoop {id: $scenario})
        RETURN w.id AS id, w.lemma AS lemma, w.meaning AS meaning, k.real_uses AS real_uses
        ORDER BY k.struggles DESC, k.real_uses DESC, w.id
        LIMIT $limit""",
    experiences="""
        MATCH (:Learner:ApplyLoop {id: $learner_id})-[:HAD]->(e:Experience)-[:EVIDENCED_BY]->(r:Recording)
        MATCH (e)-[:COMPLETED]->(m:Mission)
        RETURN e.id AS recording_id, r.title AS title, m.id AS mission_id, m.title AS mission_title,
               m.context_id AS context_id,
               COLLECT { MATCH (e)-[:DEMONSTRATED]->(s:Skill) RETURN s.id } AS demonstrated,
               COLLECT { MATCH (e)-[:REVEALED]->(g:Gap)-[:ABOUT]->(s:Skill)
                         RETURN {skill_id: s.id, name: g.name, evidence: g.evidence} } AS gaps,
               COLLECT { MATCH (e)-[:USED]->(w:Word) RETURN w.id } AS used,
               COLLECT { MATCH (e)-[:STRUGGLED_WITH]->(w:Word) RETURN w.id } AS struggled
        ORDER BY e.created_at""",
    seed_phrases="""
        UNWIND $phrases AS ph
        MATCH (s:Scenario:ApplyLoop {id: ph.scenario})
        CREATE (s)-[:HAS_PHRASE]->(p:ApplyLoop:Phrase {id: ph.id, text: ph.text, meaning: ph.meaning, order: ph.order})
        WITH p, ph
        UNWIND range(0, size(ph.words) - 1) AS i
        MATCH (w:Word:ApplyLoop {id: ph.words[i]})
        CREATE (p)-[:USES {position: i}]->(w)""",
    seed_activities="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})
        UNWIND $activities AS a
        MATCH (s:Scenario:ApplyLoop {id: a.scenario})
        CREATE (c:ApplyLoop:Context:Activity {id: a.id, title: a.title, type: a.scenario, location: a.location,
                                              when: a.when, with_whom: a.with_whom, starts_at: a.starts_at})
        CREATE (l)-[:DOES]->(c), (c)-[:IS_A]->(s)""",
    seed_current_context="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id}), (c:Context:ApplyLoop {id: $context_id})
        CREATE (l)-[:HAS_CONTEXT]->(c)""",
    activity_exists='MATCH (a:Activity:ApplyLoop {id: $activity_id}) RETURN count(a) > 0 AS exists',
    start_activity="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})-[:DOES]->(a:Activity {id: $activity_id})
        WHERE NOT (l)-[:ACTIVE_MISSION]->(:Mission {status: 'assigned'})
        OPTIONAL MATCH (l)-[am:ACTIVE_MISSION]->(:Mission)
        DELETE am
        WITH DISTINCT l, a
        OPTIONAL MATCH (l)-[hc:HAS_CONTEXT]->(:Context)
        DELETE hc
        WITH DISTINCT l, a
        CREATE (l)-[:HAS_CONTEXT]->(a)
        RETURN a {.*} AS context""",
    activities="""
        MATCH (l:Learner:ApplyLoop {id: $learner_id})-[:DOES]->(a:Activity)-[:IS_A]->(s:Scenario)
        OPTIONAL MATCH (l)-[:HAS_CONTEXT]->(cur:Context)
        RETURN a {.*} AS activity, s.id AS scenario, s.name AS scenario_name, cur.id = a.id AS is_current,
          COLLECT {
            MATCH (l)-[:HAD]->(e:Experience)-[:COMPLETED]->(:Mission)-[:TRIGGERED_BY]->(a)
            RETURN {at: toString(e.created_at), success: e.success_score,
                    used: COLLECT { MATCH (e)-[:USED]->(w:Word) RETURN w.lemma },
                    struggled: COLLECT { MATCH (e)-[:STRUGGLED_WITH]->(w:Word) RETURN w.lemma }}
          } AS visits,
          COLLECT {
            MATCH (s)-[:HAS_PHRASE]->(p:Phrase)
            RETURN {id: p.id, text: p.text, meaning: p.meaning,
                    words: COLLECT { MATCH (p)-[u:USES]->(w:Word)
                                     OPTIONAL MATCH (l)-[k:KNOWS]->(w)
                                     RETURN {id: w.id, lemma: w.lemma, status: coalesce(k.status, 'new')}
                                     ORDER BY u.position }}
            ORDER BY p.order
          } AS phrases,
          COLLECT {
            MATCH (s)-[:NEEDS]->(w:Word)<-[k:KNOWS]-(l)
            WHERE k.status <> 'fluent' AND (k.struggles > 0 OR k.status = 'practiced')
            RETURN {id: w.id, lemma: w.lemma, meaning: w.meaning, status: k.status,
                    struggles: k.struggles, real_uses: k.real_uses}
          } AS practice,
          COLLECT {
            MATCH (s)-[:NEEDS]->(w:Word)<-[:TEACHES]-(les:Lesson)
            WHERE NOT (l)-[:COMPLETED]->(les) AND NOT (l)-[:KNOWS {status: 'fluent'}]->(w)
            RETURN {id: les.id, title: les.title, order: les.order, word: w.lemma}
          } AS lesson_words
        ORDER BY a.id""",
    learner_exists='MATCH (l:Learner:ApplyLoop {id: $learner_id}) RETURN count(l) > 0 AS exists',
)


class Neo4jStore:
    backend = 'neo4j'

    def __init__(self, uri, user, password, database=None, learner_id='demo'):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database
        self.learner_id = learner_id

    # ---------- plumbing ----------
    def _read(self, fn, *args):
        with self.driver.session(database=self.database) as session:
            return session.execute_read(fn, *args)

    def _write(self, fn, *args):
        """One managed transaction: commits everything fn does, or rolls all of it back on any exception."""
        with self.driver.session(database=self.database) as session:
            return session.execute_write(fn, *args)

    def _run(self, tx, name, **params):
        return list(tx.run(Q[name], learner_id=self.learner_id, **RULES, **params))

    def _one(self, tx, name, **params):
        rows = self._run(tx, name, **params)
        return rows[0] if rows else None

    def connect(self):
        """Verify connectivity once at startup, create constraints, and seed the demo graph if it is empty."""
        self.driver.verify_connectivity()
        with self.driver.session(database=self.database) as session:
            for statement in CONSTRAINTS:
                try:
                    session.run(statement).consume()
                except Exception as exc:  # e.g. conflicting existing data in a shared database
                    print(f'[neo4j] constraint skipped: {exc}')
        if not self._read(lambda tx: self._one(tx, 'learner_exists')['exists']):
            self.reset()

    def close(self):
        self.driver.close()

    # ---------- lifecycle ----------
    def reset(self):
        cur, state = curriculum(), example('state')
        skills = [dict(s, words=cur['skill_words'].get(s['id'], [])) for s in state['skills']]
        knows = [dict(word_id=w, **k) for w, k in seed_knows(cur).items()]
        seed = cur['learner_seed']
        phrases = [dict(p, scenario=sc['id'], order=i) for sc in cur['scenarios'] for i, p in enumerate(sc.get('phrases', []))]
        activities = [dict({'starts_at': None}, **a) for a in cur['activities']]

        def work(tx):
            self._run(tx, 'wipe')
            self._run(tx, 'seed_levels', levels=cur['levels'])
            self._run(tx, 'seed_words', words=cur['words'], language=cur['language'])
            self._run(tx, 'seed_lessons', lessons=cur['lessons'])
            self._run(tx, 'seed_next')
            self._run(tx, 'seed_scenarios', scenarios=[dict({'skill': None}, **sc) for sc in cur['scenarios']])
            self._run(tx, 'seed_learner', goal=state['goal'], language=cur['language'], skills=skills)
            self._run(tx, 'seed_phrases', phrases=phrases)
            self._run(tx, 'seed_activities', activities=activities)
            self._run(tx, 'seed_current_context', context_id=state['context']['id'])
            self._run(tx, 'seed_completed', completed=[{'lesson_id': k, 'score': v} for k, v in seed['completed'].items()])
            self._run(tx, 'seed_knows', knows=knows)
            self._run(tx, 'set_resume', lesson_id=seed['current']['lesson_id'], step=seed['current']['step'])
        self._write(work)

    # ---------- learner state ----------
    def _state(self, tx):
        row = self._one(tx, 'learner')
        if not row:
            raise LookupError('Learner not found; POST /api/demo/reset to seed the graph')
        learner, context = row['learner'], row['context']
        next_target = None
        if learner.get('next_target_skill_id'):
            next_target = {'skill_id': learner['next_target_skill_id'], 'reason': learner['next_target_reason']}
        return dict(learner_id=learner['id'], goal=learner['goal'],
                    skills=[dict(r) for r in self._run(tx, 'skills')],
                    active_mission=row['mission'], context={k: v for k, v in context.items() if v is not None},
                    next_target=next_target, mode='demo' if DEMO_MODE else 'live', graph_backend=self.backend,
                    focus_words=self._focus_words(tx, context))

    def state(self):
        return self._read(self._state)

    def active_mission(self):
        return self.state()['active_mission']

    def set_context(self, context):
        def work(tx):
            row = self._one(tx, 'set_context', context=context)
            if not row:
                raise ValueError('Finish (or reset) the current mission before changing context')
            return row['context']
        return self._write(work)

    def start_activity(self, activity_id):
        """The learner is heading to this activity now: it becomes the context for the next mission."""
        def work(tx):
            if not self._one(tx, 'activity_exists', activity_id=activity_id)['exists']:
                raise LookupError('Unknown activity')
            row = self._one(tx, 'start_activity', activity_id=activity_id)
            if not row:
                raise ValueError('Finish (or reset) the current mission before switching activity')
            return {k: v for k, v in row['context'].items() if v is not None}
        return self._write(work)

    def _activities(self, tx, progress):
        lesson_status = {l['id']: l['status'] for l in progress['lessons']}
        return [build_activity(dict(r), lesson_status) for r in self._run(tx, 'activities')]

    def activities(self):
        return self._read(lambda tx: self._activities(tx, self._progress(tx)))

    def assign(self, mission):
        def work(tx):
            if not self._one(tx, 'assign', mission=mission.model_dump()):
                raise ValueError('Mission references an unknown skill or context, or a mission is already active')
        self._write(work)

    # ---------- evidence ----------
    def record(self, recording_id, title):
        def work(tx):
            if self._one(tx, 'recording_exists', rid=recording_id)['exists']:
                return False
            if not self._one(tx, 'record', rid=recording_id, title=title):
                raise ValueError('Create an active mission before recording')
            return True
        return self._write(work)

    def recording(self, recording_id):
        row = self._read(lambda tx: self._one(tx, 'recording', rid=recording_id))
        return dict(row) if row else None

    def has_experience(self, recording_id):
        return self._read(lambda tx: self._one(tx, 'experience_exists', rid=recording_id)['exists'])

    def apply(self, result, transcript):
        """Validate references, then write score, experience, gaps and vocabulary in ONE transaction."""
        evidence = dedupe_word_evidence([w.model_dump() for w in result.word_evidence])
        word_ids = sorted({e['word_id'] for e in evidence})
        rid = result.recording_id

        def work(tx):
            if self._one(tx, 'experience_exists', rid=rid)['exists']:
                return False
            check = self._one(tx, 'apply_check', rid=rid, word_ids=word_ids)
            if not check or check['mission']['id'] != result.mission_id:
                raise ValueError('Unknown recording or mission mismatch')
            mission = check['mission']
            if mission['status'] != 'assigned':
                raise ValueError('Mission already completed')
            referenced = {x.skill_id for x in result.demonstrated + result.gaps} | {result.next_target.skill_id}
            if not referenced <= set(check['skill_ids']):
                raise ValueError('Reflection references an unknown skill')
            unknown = set(word_ids) - set(check['word_ids'])
            if unknown:
                raise ValueError(f'Reflection references unknown words: {sorted(unknown)}')
            before = {r['id']: r['status'] for r in self._run(tx, 'word_statuses', word_ids=word_ids)}

            current = self._one(tx, 'application_score', skill_id=mission['skill_id'])['score']
            new_score = round(min(1, current + application_delta(result.success_score)), 2)
            self._run(tx, 'set_application_score', skill_id=mission['skill_id'], score=new_score)
            self._run(tx, 'create_experience', mission_id=mission['id'], rid=rid, transcript=transcript,
                      success_score=result.success_score, next_target=result.next_target.model_dump())
            self._run(tx, 'demonstrated', rid=rid, demonstrated=[d.model_dump() for d in result.demonstrated])
            self._run(tx, 'gaps', rid=rid, gaps=[g.model_dump() for g in result.gaps])
            self._run(tx, 'word_evidence', rid=rid, evidence=evidence, context_id=mission['context_id'])
            after = {r['id']: r['status'] for r in self._run(tx, 'word_statuses', word_ids=word_ids)}
            return vocabulary_summary(evidence, before, after)
        return self._write(work)

    # ---------- lessons / resume ----------
    def _lesson_rows(self, tx):
        return [dict(r) for r in self._run(tx, 'lessons')]

    def _check_lesson(self, tx, lesson_id, step=None):
        rows = {r['id']: r for r in self._lesson_rows(tx)}
        if lesson_id not in rows:
            raise LookupError('Unknown lesson')
        if lesson_statuses(rows.values())[lesson_id] == 'locked':
            raise ValueError('Lesson is locked; pass the earlier lessons first')
        if step is not None and step > rows[lesson_id]['steps']:
            raise ValueError('Step is beyond the end of the lesson')
        return rows[lesson_id]

    def save_step(self, lesson_id, step):
        def work(tx):
            self._check_lesson(tx, lesson_id, step)
            self._run(tx, 'set_resume', lesson_id=lesson_id, step=step)
            if step > 0:
                self._run(tx, 'introduce_words', lesson_id=lesson_id)
        self._write(work)
        return self.progress()

    def complete_lesson(self, lesson_id, score):
        passed = score >= LESSON_PASS_SCORE

        def work(tx):
            lesson = self._check_lesson(tx, lesson_id)
            self._run(tx, 'attempt', lesson_id=lesson_id, score=score, passed=passed)
            if not passed:
                self._run(tx, 'introduce_words', lesson_id=lesson_id)
                self._run(tx, 'set_resume', lesson_id=lesson_id, step=0)
                return
            self._run(tx, 'pass_lesson', lesson_id=lesson_id, score=score)
            if lesson['current_step'] is not None:
                nxt = self._one(tx, 'next_lesson', lesson_id=lesson_id)
                if nxt:
                    self._run(tx, 'set_resume', lesson_id=nxt['id'], step=0)
                else:
                    self._run(tx, 'set_resume', lesson_id=lesson_id, step=lesson['steps'])
        self._write(work)
        return {'passed': passed, 'progress': self.progress()}

    def _progress(self, tx):
        lessons = self._lesson_rows(tx)
        words = [dict(r) for r in self._run(tx, 'words')]
        level = level_summary([dict(r) for r in self._run(tx, 'levels')])
        return build_progress(self.learner_id, curriculum()['language'], level, lessons, words, lesson_statuses(lessons))

    def progress(self):
        return self._read(self._progress)

    def _focus_words(self, tx, context, limit=5):
        scenario = scenario_for(context)
        if not scenario:
            return []
        return [dict(r) for r in self._run(tx, 'focus_words', scenario=scenario, limit=limit)]

    # ---------- graph ----------
    def graph(self):
        def work(tx):
            progress = self._progress(tx)
            return build_graph(self._state(tx), [dict(r) for r in self._run(tx, 'experiences')], progress,
                               self._activities(tx, progress))
        return self._read(work)
