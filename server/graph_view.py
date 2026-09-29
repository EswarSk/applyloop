"""Builds the React Flow {nodes, edges} payload from a store snapshot. Shared by both stores
so the UI sees the same graph shape whether the backend is Neo4j or in-memory."""


def build_graph(state, experiences, progress, activities=()):
    nodes, edges, seen = [], [], set()

    def node(id, type, **data):
        if id not in seen:
            seen.add(id)
            nodes.append(dict(id=id, type=type, data=data))

    def edge(source, target, label):
        eid = f'{source}:{label}:{target}'
        if eid not in seen:
            seen.add(eid)
            edges.append(dict(id=eid, source=source, target=target, label=label))

    words = {w['id']: w for w in progress['words']}

    def word(word_id):
        w = words[word_id]
        node(f'word:{word_id}', 'word', label=w['lemma'], meaning=w['meaning'], status=w['status'])
        return f'word:{word_id}'

    node('learner', 'learner', label='You')
    node('goal', 'goal', label=state['goal'])
    context = state['context']
    node(context['id'], 'context', label=context['title'])
    edge('learner', 'goal', 'PURSUING')
    edge('learner', context['id'], 'HAS_CONTEXT')
    level = progress['level']
    node(f"level:{level['current']}", 'level', label=f"Level {level['current']}", fluent_level=level['fluent_level'])
    edge('learner', f"level:{level['current']}", 'AT_LEVEL')
    resume = progress['resume']
    if resume:
        node(f"lesson:{resume['lesson_id']}", 'lesson', label=f"{resume['title']} · step {resume['step']}/{resume['steps']}")
        edge('learner', f"lesson:{resume['lesson_id']}", 'CURRENTLY_AT')
    for skill in state['skills']:
        node(skill['id'], 'skill', label=skill['name'], knowledge=skill['knowledge_score'], application=skill['application_score'])
        edge('learner', skill['id'], 'LEARNING')
        edge('goal', skill['id'], 'REQUIRES')
    mission = state['active_mission']
    if mission:
        node(mission['id'], 'mission', label=mission['title'])
        edge(mission['id'], mission['skill_id'], 'TARGETS')
        edge(mission['id'], mission['context_id'], 'TRIGGERED_BY')
        for focus in state.get('focus_words', []):
            edge(mission['id'], word(focus['id']), 'PRACTICES')
    for act in activities:
        if act['id'] != context['id']:
            node(act['id'], 'activity', label=act['title'])
        edge('learner', act['id'], 'DOES')
    for exp in experiences:
        rid = exp['recording_id']
        node(exp['mission_id'], 'mission', label=exp.get('mission_title') or 'Past mission')
        if exp.get('context_id') and exp['context_id'] in seen:
            edge(exp['mission_id'], exp['context_id'], 'TRIGGERED_BY')
        exp_id, evidence = f'experience:{rid}', f'evidence:{rid}'
        node(exp_id, 'experience', label='Real-world attempt')
        node(evidence, 'evidence', label=exp['title'])
        edge(exp_id, exp['mission_id'], 'COMPLETED')
        edge(exp_id, evidence, 'EVIDENCED_BY')
        for skill_id in exp['demonstrated']:
            edge(exp_id, skill_id, 'DEMONSTRATED')
        for gap in exp['gaps']:
            gid = f"gap:{rid}:{gap['skill_id']}"
            node(gid, 'gap', label=gap['name'], evidence=gap['evidence'])
            edge(exp_id, gid, 'REVEALED')
            edge(gid, gap['skill_id'], 'ABOUT')
        for word_id in exp['used']:
            edge(exp_id, word(word_id), 'USED')
        for word_id in exp['struggled']:
            edge(exp_id, word(word_id), 'STRUGGLED_WITH')
    return {'nodes': nodes, 'edges': edges}


def build_progress(learner_id, language, level, lessons, words, statuses):
    """Common progress payload. lessons/words are plain dict rows from either store."""
    lesson_rows, resume = [], None
    for lesson in sorted(lessons, key=lambda l: l['order']):
        row = dict(id=lesson['id'], title=lesson['title'], level=lesson['level'], order=lesson['order'],
                   steps=lesson['steps'], status=statuses[lesson['id']], best_score=lesson.get('best_score'),
                   attempts=lesson.get('attempts', 0))
        lesson_rows.append(row)
        if lesson.get('current_step') is not None:
            resume = dict(lesson_id=lesson['id'], title=lesson['title'], level=lesson['level'],
                          step=lesson['current_step'], steps=lesson['steps'], updated_at=lesson.get('updated_at'))
    counts = {s: 0 for s in ('new', 'introduced', 'practiced', 'fluent')}
    for w in words:
        counts[w['status']] += 1
    return dict(learner_id=learner_id, language=language, level=level, resume=resume,
                lessons=lesson_rows, words=words, counts=counts)


KNOWN = ('practiced', 'fluent')


def build_activity(row, lesson_status):
    """Per-activity briefing: where you left off there, what you can already say, what to work on next.
    row comes from either store: {activity, scenario, scenario_name, is_current, visits, phrases, practice, lesson_words}."""
    a = row['activity']
    can_say, almost = [], []
    for phrase in row['phrases']:
        missing = [w['lemma'] for w in phrase['words'] if w['status'] not in KNOWN]
        entry = dict(text=phrase['text'], meaning=phrase['meaning'])
        if not missing:
            can_say.append(entry)
        elif len(missing) == 1:
            almost.append(dict(entry, missing=missing))
    visits = sorted(row['visits'], key=lambda v: v['at'] or '', reverse=True)
    last = visits[0] if visits else None
    if last and last['struggled']:
        pick_up = f"Last time you missed: {', '.join(last['struggled'])}. Listen for it again."
    elif last:
        pick_up = f"Last time went well ({', '.join(last['used'][:3])}). Try something new today."
    else:
        pick_up = 'First time practicing here. Start with a phrase you already know.'
    practice = sorted(row['practice'], key=lambda w: (-w['struggles'], -w['real_uses'], w['lemma']))[:5]
    lesson = None
    for item in sorted(row['lesson_words'], key=lambda x: x['order']):
        if lesson is None:
            lesson = dict(id=item['id'], title=item['title'], status=lesson_status.get(item['id'], 'locked'), words=[])
        if item['id'] == lesson['id'] and item['word'] not in lesson['words']:
            lesson['words'].append(item['word'])
    return dict(id=a['id'], title=a['title'], scenario=row['scenario'], scenario_name=row['scenario_name'],
                location=a.get('location'), when=a.get('when'), with_whom=a.get('with_whom'), is_current=bool(row['is_current']),
                visits=len(visits), last_visit=last, pick_up=pick_up, can_say=can_say, almost=almost,
                practice=practice, lesson=lesson)
