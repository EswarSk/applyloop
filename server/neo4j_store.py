"""Backend/graph workstream: replace DemoStore with a transactional Neo4j store.
Keep state(), graph(), assign(), record(), apply(), reset() contracts unchanged.
"""
from copy import deepcopy
from server.demo_seed import example


def application_delta(score):
    return .15 if score >= .8 else .14 if score >= .6 else .08 if score >= .4 else .03


class DemoStore:
    # ponytail: one in-memory learner; replace with Neo4j transactions for live persistence.
    def __init__(self):
        self.reset()

    def reset(self):
        self.data = example('state')
        self.recordings = {}
        self.experiences = {}

    def state(self):
        return deepcopy(self.data)

    def assign(self, mission):
        if self.data['active_mission']:
            raise ValueError('Complete or reset the active mission first')
        if mission.skill_id not in self.skill_ids() or mission.context_id != self.data['context']['id']:
            raise ValueError('Mission references an unknown skill or context')
        self.data['active_mission'] = mission.model_dump()

    def skill_ids(self):
        return {s['id'] for s in self.data['skills']}

    def record(self, recording_id, title):
        if recording_id in self.recordings:
            return False
        mission = self.data['active_mission']
        if not mission or mission['status'] != 'assigned':
            raise ValueError('Create an active mission before recording')
        self.recordings[recording_id] = {'title': title, 'mission_id': mission['id']}
        return True

    def apply(self, result, transcript):
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
        for skill in self.data['skills']:
            if skill['id'] == mission['skill_id']:
                skill['application_score'] = round(min(1, skill['application_score'] + application_delta(result.success_score)), 2)
        self.experiences[result.recording_id] = {'result': result.model_dump(), 'transcript': transcript}
        mission['status'] = 'completed'
        self.data['next_target'] = result.next_target.model_dump()
        return True

    def graph(self):
        nodes = [dict(id='learner', type='learner', data={'label': 'You'}),
                 dict(id='goal', type='goal', data={'label': self.data['goal']}),
                 dict(id=self.data['context']['id'], type='context', data={'label': self.data['context']['title']})]
        edges = []
        def edge(source, target, label):
            edges.append(dict(id=f'{source}:{label}:{target}', source=source, target=target, label=label))
        edge('learner', 'goal', 'PURSUING')
        edge('learner', self.data['context']['id'], 'HAS_CONTEXT')
        for skill in self.data['skills']:
            nodes.append(dict(id=skill['id'], type='skill', data={'label': skill['name'], 'knowledge': skill['knowledge_score'], 'application': skill['application_score']}))
            edge('learner', skill['id'], 'LEARNING')
            edge('goal', skill['id'], 'REQUIRES')
        mission = self.data['active_mission']
        if mission:
            nodes.append(dict(id=mission['id'], type='mission', data={'label': mission['title']}))
            edge(mission['id'], mission['skill_id'], 'TARGETS')
            edge(mission['id'], mission['context_id'], 'TRIGGERED_BY')
        for rid, experience in self.experiences.items():
            exp, evidence = f'experience:{rid}', f'evidence:{rid}'
            nodes.extend([dict(id=exp, type='experience', data={'label': 'Real-world attempt'}),
                          dict(id=evidence, type='evidence', data={'label': self.recordings[rid]['title']})])
            edge(exp, experience['result']['mission_id'], 'COMPLETED')
            edge(exp, evidence, 'EVIDENCED_BY')
            for demonstrated in experience['result']['demonstrated']:
                edge(exp, demonstrated['skill_id'], 'DEMONSTRATED')
            for gap in experience['result']['gaps']:
                gid = f'gap:{rid}:{gap["skill_id"]}'
                nodes.append(dict(id=gid, type='gap', data={'label': gap['name'], 'evidence': gap['evidence']}))
                edge(exp, gid, 'REVEALED')
                edge(gid, gap['skill_id'], 'ABOUT')
        return {'nodes': nodes, 'edges': edges}
