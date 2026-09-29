# Neo4j learner knowledge graph

## Model

```
(:Learner)-[:KNOWS {status, lesson_passed, real_uses, contexts, struggles}]->(:Word)
(:Learner)-[:CURRENTLY_AT {step, updated_at}]->(:Lesson)       resume point: exactly one
(:Learner)-[:COMPLETED {best_score}]->(:Lesson)                  passed lessons
(:Learner)-[:ATTEMPTED {score, passed, at}]->(:Lesson)           every attempt
(:Learner)-[:LEARNING {knowledge_score, application_score}]->(:Skill)-[:USES_WORD]->(:Word)
(:Learner)-[:HAS_CONTEXT]->(:Context)
(:Learner)-[:ACTIVE_MISSION]->(:Mission)-[:TARGETS]->(:Skill)
(:Learner)-[:HAD]->(:Experience)-[:USED | STRUGGLED_WITH]->(:Word)
                   (:Experience)-[:EVIDENCED_BY]->(:Recording), -[:REVEALED]->(:Gap)-[:ABOUT]->(:Skill)
(:Level)<-[:IN_LEVEL]-(:Lesson)-[:TEACHES]->(:Word),  (:Lesson)-[:NEXT]->(:Lesson)
(:Scenario {id:'restaurant'})-[:NEEDS]->(:Word)
```

Every node also has the `:ApplyLoop` label, so reset (`MATCH (n:ApplyLoop) DETACH DELETE n`) never touches other data.

Rules (`server/learning_rules.py`):

| Status | When |
|---|---|
| introduced | lesson started (step ≥ 1), failed lesson, or first heard in a recording |
| practiced | lesson passed (score ≥ 0.7) **or** one correct real-world use |
| fluent | correct real-world use in ≥ 2 recordings **and** ≥ 2 different contexts |

Level = highest consecutive level with ≥ 80% of its words practiced. Fluent level needs ≥ 50% of words fluent.

## Queries for the pitch (paste into Neo4j Browser / Aura console)

Where did I stop?
```cypher
MATCH (:Learner {id:'demo'})-[c:CURRENTLY_AT]->(l:Lesson)-[:IN_LEVEL]->(lv)
RETURN lv.code, l.title, c.step + '/' + l.steps AS step, c.updated_at
```

What should I say tonight? (learned in lessons, never used for real, needed at the restaurant)
```cypher
MATCH (:Learner {id:'demo'})-[k:KNOWS {status:'practiced'}]->(w:Word)<-[:NEEDS]-(:Scenario {id:'restaurant'})
RETURN w.lemma, w.meaning, k.real_uses ORDER BY k.real_uses DESC
```

Knowledge vs. fluency by level
```cypher
MATCH (lv:Level)<-[:IN_LEVEL]-(:Lesson)-[:TEACHES]->(w)
OPTIONAL MATCH (:Learner {id:'demo'})-[k:KNOWS]->(w)
RETURN lv.code, count(w) AS words,
       count(CASE WHEN k.status IN ['practiced','fluent'] THEN 1 END) AS learned,
       count(CASE WHEN k.status = 'fluent' THEN 1 END) AS fluent
ORDER BY lv.code
```

Visual: what one real conversation changed
```cypher
MATCH p = (:Learner {id:'demo'})-[:HAD]->(:Experience)-[:USED|STRUGGLED_WITH|REVEALED]->() RETURN p
```

## Activities: Spanish class, restaurant, dance class

```
(:Learner)-[:DOES]->(:Activity:Context {title, when, with_whom})-[:IS_A]->(:Scenario)
(:Scenario)-[:NEEDS]->(:Word)          vocabulary that place needs
(:Scenario)-[:HAS_PHRASE]->(:Phrase)-[:USES]->(:Word)
(:Experience)-[:COMPLETED]->(:Mission)-[:TRIGGERED_BY]->(:Activity)   what happened there last time
```

What can I already say at dance class? (every word of the phrase is practiced or fluent)
```cypher
MATCH (:Activity {id:'dance-001'})-[:IS_A]->(:Scenario)-[:HAS_PHRASE]->(p:Phrase)
WHERE ALL(w IN [(p)-[:USES]->(x) | x] WHERE EXISTS { (:Learner {id:'demo'})-[:KNOWS {status:'fluent'}]->(w) }
                                        OR EXISTS { (:Learner {id:'demo'})-[:KNOWS {status:'practiced'}]->(w) })
RETURN p.text, p.meaning
```

Where did I leave off at each activity?
```cypher
MATCH (l:Learner {id:'demo'})-[:DOES]->(a:Activity)
OPTIONAL MATCH (l)-[:HAD]->(e:Experience)-[:COMPLETED]->(:Mission)-[:TRIGGERED_BY]->(a)
OPTIONAL MATCH (e)-[:STRUGGLED_WITH]->(w:Word)
RETURN a.title, count(DISTINCT e) AS visits, collect(DISTINCT w.lemma) AS missed_last_time
```

The whole picture for one place (visual)
```cypher
MATCH p = (:Learner {id:'demo'})-[:DOES]->(:Activity {id:'dance-001'})-[:IS_A]->(:Scenario)-[:HAS_PHRASE]->(:Phrase)-[:USES]->(:Word)
RETURN p
```
